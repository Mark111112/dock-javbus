#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import requests
import base64
import hashlib
import secrets
import threading
import sqlite3
import copy
from jellyfin_apiclient_python import JellyfinClient

# Add current directory to Python path to ensure modules can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory, Response, stream_with_context, flash, abort
from flask_cors import CORS, cross_origin
from flask_sock import Sock
from werkzeug.utils import secure_filename, safe_join
from javbus_db import JavbusDatabase
from modules.translation.translator import get_translator
from transcription_service import configure_transcription_from_dict
from modules.javbus_service import get_javbus_client
import logging
import logging.config
import traceback
import moviescraper  # Changed from movieinfo to moviescraper
from datetime import datetime
from strm_library import StrmLibrary  # Import the STRM library module
from cloud115_library import Cloud115Library
from jellyfin_library import JellyfinLibrary  # Import the Jellyfin library module
# 导入视频播放器适配器
try:
    import video_player_adapter
    logging.info("成功导入视频播放器适配器")
except ImportError as e:
    logging.error(f"导入视频播放器适配器失败: {str(e)}")
    video_player_adapter = None

# 添加URL解析库
import urllib.parse
import re
import subprocess
import uuid
import shutil
import posixpath
from transcription_service import handle_transcription_ws
from modules.live_caption_proxy import handle_caption_proxy_ws
# Import new transcode module V2
from modules.transcode import (
    TranscodeConfig,
    TranscodeManager,
    PlaylistGenerator,
    get_transcode_config,
    get_transcode_manager,
)
# Import AV-League scraper
from modules.scrapers.av_league_scraper_fast import AVLeagueScraperFast
from modules.transcode.api import init_transcode_manager, register_routes, get_transcode_manager as get_v2_transcode_manager

# 创建视频相关日志过滤器
class VideoRequestFilter(logging.Filter):
    """过滤掉视频播放相关的详细日志"""
    def filter(self, record):
        # 过滤掉proxy_stream函数中的详细日志
        if hasattr(record, 'funcName') and record.funcName == 'proxy_stream':
            # 只在错误时显示日志
            return record.levelno >= logging.WARNING
        
        # 对视频播放相关的日志进行过滤
        message = record.getMessage()
        if any(x in message for x in ['视频流代理请求', '代理解码后的URL', '代理流成功', 'HLS URL', '请求:']):
            return record.levelno >= logging.WARNING
        
        return True

LOG_DIR = 'logs'
os.makedirs(LOG_DIR, exist_ok=True)

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'video_request_filter': {
            '()': VideoRequestFilter,
        },
    },
    'formatters': {
        'standard': {
            'format': '%(asctime)s - %(levelname)s - %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
        },
        'file': {
            'class': 'logging.handlers.TimedRotatingFileHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'filename': os.path.join(LOG_DIR, 'webserver.log'),
            'when': 'midnight',
            'interval': 1,
            'backupCount': 3,
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'urllib3': {'level': 'WARNING'},
        'requests': {'level': 'WARNING'},
        'werkzeug': {'level': 'WARNING'},
        'chardet.charsetprober': {'level': 'WARNING'},
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console', 'file'],
        'filters': ['video_request_filter'],
    },
}

logging.config.dictConfig(LOGGING_CONFIG)

# Initialize Flask application
app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)  # Enable CORS
sock = Sock(app)

# Add secret key for session
app.secret_key = os.urandom(24)

# 注册 V2 转码 API 路由（在 load_config 之后执行）
def register_transcode_v2_routes():
    """延迟注册 V2 转码路由，确保在 app 创建后调用"""
    try:
        register_routes(app)
        TRANSCODE_LOGGER.info("V2 转码 API 路由已注册")
    except Exception as e:
        TRANSCODE_LOGGER.error(f"注册 V2 转码路由失败: {e}", exc_info=True)

# Add timestamp to date filter
@app.template_filter('timestamp_to_date')
def timestamp_to_date(timestamp):
    """Convert Unix timestamp to human-readable date"""
    if not timestamp:
        return ""
    return datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M')

# Add JSON parsing filter
@app.template_filter('fromjson')
def parse_json(json_string):
    """Parse JSON string to Python object"""
    try:
        if json_string:
            return json.loads(json_string)
        return []
    except:
        return []

# Configuration file path
CONFIG_FILE = "config/config.json"
DB_FILE = os.path.abspath("data/javbus.db")  # Use absolute path to avoid confusion

# Directory setup
os.makedirs("data", exist_ok=True)
os.makedirs("buspic/covers", exist_ok=True)
os.makedirs("buspic/actor", exist_ok=True)
DOWNLOAD_DIR = os.path.abspath(os.path.join("data", "downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
DOWNLOAD_TASKS = {}

TRANSCODE_DEFAULT_DIR = os.path.abspath(os.path.join("data", "transcode"))
os.makedirs(TRANSCODE_DEFAULT_DIR, exist_ok=True)
TRANSCODE_WORK_DIR = TRANSCODE_DEFAULT_DIR
TRANSCODE_TASKS = {}
TRANSCODE_TASK_KEYS = {}
TRANSCODE_TASKS_LOCK = threading.Lock()
TRANSCODE_PROBE_CACHE = {}
TRANSCODE_LAST_CLEANUP = 0.0
TRANSCODE_ACTIVE_STATUSES = {"queued", "starting", "running", "ready"}
TRANSCODE_LOGGER = logging.getLogger("Cloud115Transcode")
# 转码流访问 token 存储
TRANSCODE_TOKENS = {}
TRANSCODE_TOKENS_LOCK = threading.Lock()

# 新转码管理器（V2）
TRANSCODE_V2_MANAGER = None

# 115目录导入任务管理
IMPORT_115_TASKS = {}
IMPORT_115_LOGGER = logging.getLogger("Import115")


@sock.route('/ws/transcription')
def transcription_ws(ws):
    """WebSocket 端点：接收前端音频流并返回转写结果。"""
    handle_transcription_ws(ws)


@sock.route('/ws/caption')
def caption_proxy_ws(ws):
    """WebSocket 端点：代理到 faster-whisper 并附加翻译。"""
    handle_caption_proxy_ws(ws)

def _run_ffmpeg_hls_to_mp4(task_id, m3u8_url, output_path):
    """在后台运行 ffmpeg 将 HLS(m3u8) 保存为 MP4。"""
    DOWNLOAD_TASKS[task_id]["status"] = "running"
    cmd = [
        "ffmpeg", "-y",
        "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        output_path
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _, stderr = proc.communicate()
        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            DOWNLOAD_TASKS[task_id]["status"] = "completed"
        else:
            DOWNLOAD_TASKS[task_id]["status"] = "error"
            DOWNLOAD_TASKS[task_id]["error"] = (stderr or b"").decode(errors="ignore")[:2000]
    except Exception as e:
        DOWNLOAD_TASKS[task_id]["status"] = "error"
        DOWNLOAD_TASKS[task_id]["error"] = str(e)

# Initialize database
db = JavbusDatabase(db_file=DB_FILE)
logging.info(f"Using database file: {DB_FILE}")

# Initialize translator
translator = get_translator()

# Load configuration
def load_config():
    """Load configuration file"""
    config = {
        "watch_url_prefix": "https://missav.ai",
        "base_url": "https://www.javbus.com",
        "javbus": {
            "mode": "internal",
            "external_api_url": "",
            "timeout": 10,
            "page_size": 30,
            "allow_external_fallback": False,
            "internal": {
                "enabled": True,
                "max_concurrency": 4,
                "timeout": 10,
                "cache_ttl_seconds": 3600,
                "page_size": 30,
                "allow_external_fallback": False
            }
        },
        "translation": {
            "api_url": "https://api.siliconflow.cn/v1/chat/completions",
            "source_lang": "日语",
            "target_lang": "中文",
            "api_token": "",
            "model": "THUDM/glm-4-9b-chat"
        },
        "cloud115": {
            "default_folder_id": "",
            "auth_mode": "auto",
            "token_file": "data/cloud115_token.json",
            "request_timeout": 15,
            "driver": {
                "enabled": True,
                "cookie": "",
                "cookie_file": "data/cloud115_cookie.txt",
                "user_agent": "Mozilla/5.0 115Browser/27.0.5.7",
                "timeout": 15,
                "api_urls": [
                    "https://webapi.115.com/files",
                    "http://web.api.115.com/files"
                ],
                "login_check_interval": 300
            },
            "library_settings": {
                "category": "other",
                "min_file_size_mb": 200,
                "default_delay_seconds": 5
            },
            "alist": {
                "enabled": True,
                "base_url": "",
                "root_path": "/115",
                "username": "",
                "password": "",
                "timeout": 30,
                "url_cache_seconds": 300
            }
        },
        "jellyfin": {
            "server_url": "",
            "username": "",
            "password": "",
            "api_key": "",
            "client_name": "BusPre",
            "client_id": "buspre-web-player",
            "device_name": "Web Browser",
            "device_id": "buspre-web-player-01",
            "transcoding": {
                "enable_auto_transcoding": True,
                "max_streaming_bitrate": 20000000,
                "preferred_video_codec": "h264",
                "preferred_audio_codec": "aac",
                "container": "ts"
            }
        }
    }
    default_javbus_config = copy.deepcopy(config["javbus"])
    config_modified = False
    legacy_api_url = None
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                config.update(loaded_config)
                if "api_url" in config:
                    legacy_api_url = config.pop("api_url", None)
                    config_modified = True
                else:
                    legacy_api_url = loaded_config.get("api_url")
                logging.info(f"Loaded configuration file: {CONFIG_FILE}")
        else:
            # Create config directory if it doesn't exist
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            # Save default config
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                logging.info(f"Created default configuration file: {CONFIG_FILE}")
    except Exception as e:
        logging.error(f"Failed to load configuration file: {str(e)}")
    
    # Ensure JavBus configuration section is fully populated
    javbus_defaults = default_javbus_config
    existing_javbus_config = config.get("javbus", {}) or {}
    merged_javbus_config = copy.deepcopy(javbus_defaults)
    if isinstance(existing_javbus_config, dict):
        merged_javbus_config.update(existing_javbus_config)

    existing_internal_config = (
        existing_javbus_config.get("internal")
        if isinstance(existing_javbus_config, dict)
        else {}
    ) or {}
    merged_internal_config = javbus_defaults["internal"].copy()
    if isinstance(existing_internal_config, dict):
        merged_internal_config.update(existing_internal_config)
    merged_javbus_config["internal"] = merged_internal_config

    if not merged_javbus_config.get("external_api_url") and legacy_api_url:
        merged_javbus_config["external_api_url"] = str(legacy_api_url).strip()
        config_modified = True

    if existing_javbus_config != merged_javbus_config:
        config_modified = True
    config["javbus"] = merged_javbus_config

    # Ensure 115 and alist configuration sections are fully populated
    cloud115_config = config.setdefault("cloud115", {})
    
    # 确保 driver 配置存在
    driver_defaults = {
            "enabled": False,
            "cookie": "",
        "cookie_file": "data/cloud115_cookie.txt",
            "user_agent": "Mozilla/5.0 115Browser/27.0.5.7",
            "timeout": 15,
        "api_urls": ["https://webapi.115.com/files", "http://web.api.115.com/files"],
            "login_check_interval": 300
    }
    existing_driver_config = cloud115_config.get("driver", {}) or {}
    merged_driver_config = driver_defaults.copy()
    merged_driver_config.update(existing_driver_config)
    cloud115_config["driver"] = merged_driver_config
    
    # 确保其他115配置字段存在
    cloud115_config.setdefault("auth_mode", "openapi")
    cloud115_config.setdefault("token_file", "data/cloud115_token.json")
    cloud115_config.setdefault("request_timeout", 15)
    
    alist_defaults = {
            "enabled": True,
            "base_url": "",
            "root_path": "/115",
            "username": "",
            "password": "",
            "timeout": 30,
            "url_cache_seconds": 300
        }
    existing_alist_config = cloud115_config.get("alist", {}) or {}
    merged_alist_config = alist_defaults.copy()
    merged_alist_config.update(existing_alist_config)
    cloud115_config["alist"] = merged_alist_config

    transcode_defaults = {
        "enabled": True,
        "auto_start": True,
        "use_hwaccel": True,
        "qsv_device": "/dev/dri/renderD128",
        "qsv_preset": "7",
        "video_encoder": "h264_qsv",
        "audio_encoder": "aac",
        "video_bitrate": "5000k",
        "maxrate": "6000k",
        "bufsize": "12000k",
        "audio_bitrate": "192k",
        "audio_channels": 2,
        "audio_sample_rate": 44100,
        "segment_duration": 4,
        "hls_list_size": 0,  # HLS播放列表大小，0表示不限制（包含所有片段），用于跳转和完整播放列表
        "max_concurrent_tasks": 2,
        "ready_timeout_seconds": 30,
        "work_dir": "data/transcode",
        "video_encoder_sw": "libx264",
        "trigger_on_extensions": ["wmv", "avi", "asf", "rmvb", "rm", "flv", "ts", "m2ts", "mpeg", "mpg", "mov", "mkv", "webm"],
        "trigger_on_codecs": ["hevc", "h265", "hevc_qsv", "vp9"],
        "probe_enabled": True,
        "probe_timeout": 15,
        "cleanup_idle_minutes": 30,
        "hls_mode": "streaming",  # "streaming" 或 "vod"
        "hls_flags": "append_list+omit_endlist",  # 会根据hls_mode自动调整（不包含delete_segments）
        "gop_size": 120,
    }
    existing_transcode_config = cloud115_config.get("transcode", {}) or {}
    merged_transcode_config = transcode_defaults.copy()
    if isinstance(existing_transcode_config, dict):
        merged_transcode_config.update(existing_transcode_config)
    
    # 根据hls_mode自动调整hls_flags
    hls_mode = merged_transcode_config.get("hls_mode", "streaming")
    existing_hls_flags = merged_transcode_config.get("hls_flags", "")
    existing_hls_list_size = merged_transcode_config.get("hls_list_size")
    default_streaming_flags = "append_list+omit_endlist"  # 不包含delete_segments
    
    # 自动设置hls_list_size（如果未设置，默认设置为0，不限制）
    if existing_hls_list_size is None:
        merged_transcode_config["hls_list_size"] = 0
    
    # 检查hls_flags是否与当前hls_mode匹配
    if hls_mode == "vod":
        # VOD模式：不应该包含omit_endlist和append_list
        # 如果包含这些标志，说明是旧的streaming配置，需要自动调整
        # 添加temp_file标志确保m3u8实时更新（边转码边生成m3u8）
        if "omit_endlist" in existing_hls_flags or "append_list" in existing_hls_flags or not existing_hls_flags:
            # 自动设置为VOD模式推荐的flags（保留片段用于跳转，实时更新m3u8）
            merged_transcode_config["hls_flags"] = "temp_file"  # temp_file确保m3u8实时更新
        elif "temp_file" not in existing_hls_flags:
            # 如果用户已经设置了VOD模式的flags但没有temp_file，自动添加
            merged_transcode_config["hls_flags"] = existing_hls_flags + ("+" if existing_hls_flags else "") + "temp_file"
    else:
        # Streaming模式：应该包含omit_endlist和append_list
        # 移除delete_segments，允许用户保留所有片段
        if "omit_endlist" not in existing_hls_flags or "append_list" not in existing_hls_flags or not existing_hls_flags:
            # 自动设置为Streaming模式推荐的flags（不包含delete_segments）
            merged_transcode_config["hls_flags"] = "append_list+omit_endlist"
    if existing_transcode_config != merged_transcode_config:
        config_modified = True
    cloud115_config["transcode"] = merged_transcode_config
    config["cloud115"] = cloud115_config

    if config_modified:
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                logging.info(f"Configuration file {CONFIG_FILE} normalized and updated")
        except Exception as exc:
            logging.error(f"Failed to normalize configuration file: {exc}")

    # 初始化新转码管理器（V2）
    global TRANSCODE_V2_MANAGER
    try:
        transcode_config = get_transcode_config(config)

        # 创建 URL 刷新回调函数
        # 当转码任务需要重启 FFmpeg 时，会调用此函数获取新的 115 直链
        def url_refresh_callback(pickcode: str):
            """刷新 115 直链的回调函数

            Args:
                pickcode: 115 文件 pickcode

            Returns:
                (source_url, header_string) 或 None
            """
            if CLOUD115_CLIENT is None or getattr(CLOUD115_CLIENT, 'driver', None) is None:
                TRANSCODE_LOGGER.warning(f"115 driver 未配置，无法刷新 URL (pickcode={pickcode})")
                return None

            try:
                info = CLOUD115_CLIENT.get_download_info(pickcode)
                download_url = info.get('url')
                if not download_url:
                    TRANSCODE_LOGGER.warning(f"115 未返回下载链接 (pickcode={pickcode})")
                    return None

                # 构建 HTTP 头（与启动转码时相同）
                raw_data = info.get('raw', {}) if isinstance(info.get('raw'), dict) else {}
                url_info = raw_data.get('url', {}) if isinstance(raw_data.get('url'), dict) else {}
                auth_cookie = url_info.get('auth_cookie') if isinstance(url_info, dict) else None

                headers = _build_http_headers_for_transcode({
                    'download_url': download_url,
                    'auth_cookie': auth_cookie,
                }, pickcode=pickcode)
                header_string = _build_ffmpeg_header_string(headers)

                TRANSCODE_LOGGER.info(f"成功刷新 115 直链 (pickcode={pickcode})")
                return download_url, header_string

            except Exception as exc:
                # 捕获所有异常，包括 Cloud115AuthError
                error_type = type(exc).__name__
                if error_type == 'Cloud115AuthError':
                    TRANSCODE_LOGGER.warning(f"115 鉴权失败，无法刷新 URL (pickcode={pickcode}): {exc}")
                else:
                    TRANSCODE_LOGGER.error(f"刷新 115 直链失败 (pickcode={pickcode}): {exc}")
                return None

        TRANSCODE_V2_MANAGER = get_transcode_manager(transcode_config, url_refresh_callback=url_refresh_callback)
        init_transcode_manager(TRANSCODE_V2_MANAGER)
        # 注意：register_routes 需要在 app 创建后调用，将在后面执行
        TRANSCODE_LOGGER.info("新转码管理器已初始化（支持 URL 自动刷新）")
    except Exception as e:
        TRANSCODE_LOGGER.error(f"初始化新转码管理器失败: {e}")

    return config

# Get current configuration
CURRENT_CONFIG = load_config()

# 注册 V2 转码 API 路由（在 load_config 之后执行）
register_transcode_v2_routes()

def apply_runtime_configuration() -> None:
    """应用配置并初始化运行时依赖。"""

    global CURRENT_API_URL, CURRENT_WATCH_URL_PREFIX, CURRENT_BASE_URL, javbus_client, av_league_scraper

    javbus_config_section = CURRENT_CONFIG.get("javbus")
    if not isinstance(javbus_config_section, dict):
        javbus_config_section = {}
        CURRENT_CONFIG["javbus"] = javbus_config_section

    javbus_mode = str(javbus_config_section.get("mode", "internal")).lower()

    env_api_url = os.environ.get("API_URL", "").strip()
    config_updated = False
    resolved_api_url = ""

    if "api_url" in CURRENT_CONFIG:
        CURRENT_CONFIG.pop("api_url", None)
        config_updated = True

    if env_api_url:
        resolved_api_url = env_api_url.rstrip("/")
        logging.info("Using API URL from environment: %s", resolved_api_url)
        if javbus_config_section.get("external_api_url") != resolved_api_url:
            javbus_config_section["external_api_url"] = resolved_api_url
            config_updated = True
    else:
        candidate_api_url = str(javbus_config_section.get("external_api_url", "")).strip()

        resolved_api_url = candidate_api_url.rstrip("/") if candidate_api_url else ""
        if resolved_api_url:
            logging.info("Using API URL from config file: %s", resolved_api_url)
            if not javbus_config_section.get("external_api_url"):
                javbus_config_section["external_api_url"] = resolved_api_url
                config_updated = True
        else:
            logging.warning("No JavBus API URL configured; some features may not work correctly")

    if config_updated:
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(CURRENT_CONFIG, f, ensure_ascii=False, indent=2)
                logging.info("Configuration file updated with latest JavBus API settings")
        except Exception as e:
            logging.error(f"Failed to update configuration file: {str(e)}")

    CURRENT_API_URL = resolved_api_url if env_api_url or resolved_api_url else ""
    CURRENT_WATCH_URL_PREFIX = CURRENT_CONFIG.get("watch_url_prefix", "https://missav.ai")
    CURRENT_BASE_URL = CURRENT_CONFIG.get("base_url", "https://www.javbus.com")
    javbus_client = get_javbus_client(
        CURRENT_CONFIG,
        logger=logging.getLogger("JavBus"),
        db=db,
    )

    # Initialize AV-League scraper
    av_league_scraper = AVLeagueScraperFast()

    # Apply transcription / STT configuration (Speaches or faster-whisper)
    try:
        transcription_cfg = CURRENT_CONFIG.get("transcription") or {}
        configure_transcription_from_dict(transcription_cfg)
    except Exception as exc:
        logging.getLogger("Transcription").error("Failed to apply STT config: %s", exc)

    if javbus_mode == "internal":
        scraper_logger = logging.getLogger("JavBus.Internal.Scraper")
        scraper_logger.setLevel(logging.DEBUG)
        scraper_logger.debug("[调试] JavBus Internal Scraper 日志级别已设置为 DEBUG")


# 初始加载配置
apply_runtime_configuration()

# Alist integration caches and defaults
ALIST_AUTH_CACHE = {
    "token": None,
    "expires_at": 0
}
ALIST_FILE_URL_CACHE = {}
ALIST_CACHE_LOCK = threading.Lock()

# Initialize 115 Cloud Client
cloud115_config = CURRENT_CONFIG.get("cloud115", {}) or {}
driver_config = cloud115_config.get("driver", {}) or {}

try:
    from modules.cloud115_client import Cloud115Client, Cloud115AuthError

    CLOUD115_CLIENT = Cloud115Client(
        token_file=cloud115_config.get("token_file", "data/cloud115_token.json"),
        driver_cookie=driver_config.get("cookie", "").strip() or None,
        driver_cookie_file=driver_config.get("cookie_file", "").strip() or None,
        mode=cloud115_config.get("auth_mode", "openapi"),
        timeout=int(cloud115_config.get("request_timeout", 15)),
        driver_user_agent=driver_config.get("user_agent", "Mozilla/5.0 115Browser/27.0.5.7"),
        driver_api_urls=driver_config.get("api_urls"),
        driver_login_check_interval=int(driver_config.get("login_check_interval", 300)),
        logger=logging.getLogger("Cloud115Client"),
    )
    logging.info(f"115 云盘客户端初始化成功 (mode={cloud115_config.get('auth_mode', 'openapi')})")
except Exception as exc:
    logging.error(f"115 云盘客户端初始化失败: {exc}")
    CLOUD115_CLIENT = None
    if "Cloud115AuthError" not in globals():
        class Cloud115AuthError(Exception):  # type: ignore
            """Fallback Cloud115 auth error when client不可用。"""
            pass

CLOUD115_TRANSCODE_CONFIG = cloud115_config.get("transcode", {}) or {}
TRANSCODE_WORK_DIR = os.path.abspath(CLOUD115_TRANSCODE_CONFIG.get("work_dir") or TRANSCODE_DEFAULT_DIR)
os.makedirs(TRANSCODE_WORK_DIR, exist_ok=True)
CLOUD115_DRIVER_USER_AGENT = driver_config.get("user_agent", "Mozilla/5.0 115Browser/27.0.5.7")
CLOUD115_TRANSCODE_PLAYLIST = CLOUD115_TRANSCODE_CONFIG.get("playlist_filename", "index.m3u8")
CLOUD115_TRANSCODE_SEGMENT_TEMPLATE = CLOUD115_TRANSCODE_CONFIG.get("segment_template", "segment_%05d.ts")
CLOUD115_TRANSCODE_READY_TIMEOUT = int(CLOUD115_TRANSCODE_CONFIG.get("ready_timeout_seconds", 30) or 30)
CLOUD115_TRANSCODE_PROBE_TIMEOUT = int(CLOUD115_TRANSCODE_CONFIG.get("probe_timeout", 15) or 15)
CLOUD115_TRANSCODE_CLEANUP_MINUTES = int(CLOUD115_TRANSCODE_CONFIG.get("cleanup_idle_minutes", 30) or 30)
CLOUD115_TRANSCODE_MAX_TASKS = int(CLOUD115_TRANSCODE_CONFIG.get("max_concurrent_tasks", 2) or 2)


# Favorites management
FAVORITES_FILE = "data/favorites.json"

def load_favorites():
    """Load favorites from file"""
    favorites = []
    try:
        if os.path.exists(FAVORITES_FILE):
            with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
                favorites = json.load(f)
                logging.info(f"Loaded favorites file: {FAVORITES_FILE}")
    except Exception as e:
        logging.error(f"Failed to load favorites file: {str(e)}")
    
    return favorites

def save_favorites(favorites):
    """Save favorites to file"""
    try:
        os.makedirs(os.path.dirname(FAVORITES_FILE), exist_ok=True)
        with open(FAVORITES_FILE, 'w', encoding='utf-8') as f:
            json.dump(favorites, f, ensure_ascii=False, indent=4)
            logging.info(f"Saved favorites to file: {FAVORITES_FILE}")
        return True
    except Exception as e:
        logging.error(f"Failed to save favorites file: {str(e)}")
        return False

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 Not Found errors"""
    return render_template('error.html', 
                          error_title="Page Not Found", 
                          error_message="The page you are looking for does not exist."), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handle 500 Internal Server Error"""
    return render_template('error.html', 
                          error_title="Internal Server Error", 
                          error_message="An unexpected error occurred on the server."), 500

@app.errorhandler(Exception)
def handle_exception(e):
    """Handle uncaught exceptions"""
    # Log the error
    app.logger.error(f"Uncaught exception: {str(e)}")
    app.logger.error(traceback.format_exc())
    
    # Return error page
    return render_template('error.html', 
                          error_title="Application Error", 
                          error_message="An unexpected error occurred.", 
                          error_details=str(e)), 500

# Routes: Web pages
@app.route('/')
def index():
    """Show homepage"""
    # Get the 4 most recently viewed movies
    recent_movies = []
    try:
        # Ensure DB is initialized
        if db and db.local:
            # Query database for recently viewed movies
            recent_movies_data = db.get_recent_movies(limit=4)
            if recent_movies_data:
                recent_movies = [format_movie_data(movie) for movie in recent_movies_data]
    except Exception as e:
        logging.error(f"Failed to get recent movies: {str(e)}")
    
    return render_template('index.html', recent_movies=recent_movies)

@app.route('/search')
def search():
    """Search for movie by ID"""
    movie_id = request.args.get('id', '')
    if not movie_id:
        return render_template('search.html', search_query='')
    
    movie_data = get_movie_data(movie_id)
    if movie_data:
        formatted_movie = format_movie_data(movie_data)
        return render_template('search.html', movie=formatted_movie, search_query=movie_id)
    else:
        return render_template('search.html', search_query=movie_id)

@app.route('/search_keyword')
def search_keyword():
    """关键字搜索电影"""
    keyword = request.args.get('keyword', '')
    page = request.args.get('page', '1')
    magnet = request.args.get('magnet', '')  # Get magnet parameter
    movie_type = request.args.get('type', '')  # Get type parameter
    filter_type = request.args.get('filterType', '')  # Get filterType parameter
    filter_value = request.args.get('filterValue', '')  # Get filterValue parameter
    
    # 确保页码是整数
    try:
        page = int(page)
    except ValueError:
        page = 1
    
    try:
        effective_filter_type = filter_type if not keyword else ""
        effective_filter_value = filter_value if not keyword else ""

        search_result = javbus_client.search_movies(
            keyword=keyword,
            page=page,
            magnet=magnet,
            movie_type=movie_type,
            filter_type=effective_filter_type,
            filter_value=effective_filter_value,
        )
        if not isinstance(search_result, dict):
            search_result = {}

        movies_list = search_result.get("movies", [])
        pagination = search_result.get("pagination", {})
            
        if not movies_list and keyword:
            logging.warning("搜索结果为空: keyword=%s, page=%s", keyword, page)

        # 格式化电影列表数据
        formatted_movies = []
        for movie in movies_list:
            formatted_movies.append({
                "id": movie.get("id", ""),
                "title": movie.get("title", ""),
                "image_url": movie.get("img", ""),
                "date": movie.get("date", ""),
                "tags": movie.get("tags", []),
                "translated_title": movie.get("translated_title", "")
            })

        # 构建分页数据
        page_info = {
            "current_page": pagination.get("currentPage", 1),
            "total_pages": len(pagination.get("pages", [])),
            "has_next": pagination.get("hasNextPage", False),
            "next_page": pagination.get("nextPage", 1),
            "pages": pagination.get("pages", [])
        }

        # 如果是按演员搜索，触发 av-league 数据刷新（后台异步）
        actor_id_for_refresh = None
        if filter_type == "star" and filter_value:
            actor_id_for_refresh = filter_value
            # 检查是否需要刷新 av-league 数据（30天未更新或无 av-league 数据）
            actor_data = db.get_star_info_for_display(filter_value)
            needs_refresh = False
            if actor_data:
                # 检查是否有 av-league 数据
                has_av_league_data = bool(actor_data.get("av_league_updated"))
                # 检查数据是否过期（30天）
                av_league_updated = actor_data.get("av_league_updated", 0)
                is_expired = (time.time() - av_league_updated) > (30 * 24 * 60 * 60) if av_league_updated else True

                if not has_av_league_data or is_expired:
                    needs_refresh = True
                    logging.info(f"[AV-League] 演员 {filter_value} 需要刷新数据")

                    # 在后台线程中刷新 av-league 数据
                    def refresh_av_league_bg(star_id, star_name):
                        try:
                            logging.info(f"[AV-League] 后台开始获取演员 {star_name} 的数据")
                            av_league_data = av_league_scraper.search_actress(star_name)
                            if av_league_data:
                                normalized_data = av_league_scraper.normalize_for_javbus(av_league_data)
                                db.update_star_with_av_league_data(star_id, normalized_data)
                                logging.info(f"[AV-League] 后台成功获取演员 {star_name} 的数据")
                            else:
                                logging.warning(f"[AV-League] 后台未找到演员 {star_name} 的数据")
                        except Exception as e:
                            logging.error(f"[AV-League] 后台刷新失败: {str(e)}")

                    # 启动后台线程
                    thread = threading.Thread(target=refresh_av_league_bg, args=(filter_value, actor_data.get("name", filter_value)))
                    thread.daemon = True
                    thread.start()

        # 如果是按演员筛选，获取演员详情信息
        actor_info_for_template = None
        if filter_type == "star" and filter_value:
            av_league_data = db.get_star_info_for_display(filter_value)
            if av_league_data:
                actor_info_for_template = {
                    "id": filter_value,
                    "name": av_league_data.get("name", ""),
                    "image_url": av_league_data.get("avatar", ""),
                    "birthdate": av_league_data.get("birthday", ""),
                    "age": av_league_data.get("age", ""),
                    "height": av_league_data.get("height", ""),
                    "measurements": f"{av_league_data.get('bust', '')} - {av_league_data.get('waistline', '')} - {av_league_data.get('hipline', '')}" if av_league_data.get('bust') else "",
                    "birthplace": av_league_data.get("birthplace", ""),
                    "hobby": av_league_data.get("hobby", ""),
                    "av_league_data": av_league_data
                }
                logging.info(f"[DEBUG] Actor info for template: id={filter_value}, name={av_league_data.get('name')}, av_league_updated={av_league_data.get('av_league_updated')}")

        return render_template('search.html',
                              keyword_results=formatted_movies,
                              keyword_query=keyword,
                              pagination=page_info,
                              filter_type=filter_type,
                              filter_value=filter_value,
                              movie_type=movie_type,
                              actor=actor_info_for_template)
    except Exception as e:
        logging.error(f"搜索失败: {str(e)}")
        return render_template('search.html', 
                             keyword_query=keyword,
                             filter_type=filter_type,
                             filter_value=filter_value,
                             error_message=f"搜索出错: {str(e)}")

@app.route('/search_actor')
def search_actor():
    """Search for actor by name"""
    actor_name = request.args.get('name', '')
    if not actor_name:
        return render_template('search.html', actor_query='')
    
    # First try to find actor by name in database
    actors = db.search_stars(actor_name)
    
    # If not found in DB, search via API
    if not actors:
        try:
            api_actors = list(javbus_client.search_stars(actor_name))
            actors = api_actors

            # Save actors to database
            for actor in actors:
                db.save_star(actor)
        except Exception as e:
            logging.error(f"Failed to search actor by API: {str(e)}")
    
    # If we found exactly one actor, show their details
    if len(actors) == 1:
        actor = actors[0]
        actor_id = actor.get("id", "")

        # Get av-league data for this actor
        av_league_data = db.get_star_info_for_display(actor_id)
        logging.info(f"[DEBUG] Actor {actor_id} av_league_data: {av_league_data}")
        logging.info(f"[DEBUG] av_league_updated: {av_league_data.get('av_league_updated') if av_league_data else 'N/A'}")

        # Format actor data with av-league information
        formatted_actor = {
            "id": actor_id,
            "name": actor.get("name", ""),
            "image_url": actor.get("avatar", ""),
            "birthdate": actor.get("birthday", ""),
            "age": actor.get("age", ""),
            "height": actor.get("height", ""),
            "measurements": f"{actor.get('bust', '')} - {actor.get('waistline', '')} - {actor.get('hipline', '')}" if actor.get('bust') else "",
            "birthplace": actor.get("birthplace", ""),
            "hobby": actor.get("hobby", ""),
            # av-league fields
            "av_league_data": av_league_data
        }

        # 修改：获取演员的电影基本信息，不预先获取详情
        actor_movies = get_actor_movies(actor_id)
        # 使用修改后的format_movie_data函数处理简化的电影数据
        formatted_movies = [format_movie_data(movie) for movie in actor_movies]

        return render_template('search.html', actor=formatted_actor, actor_movies=formatted_movies, actor_query=actor_name)
    
    # If multiple actors found, show a list of them
    elif len(actors) > 1:
        formatted_actors = []
        for actor in actors:
            formatted_actors.append({
                "id": actor.get("id", ""),
                "name": actor.get("name", ""),
                "image_url": actor.get("avatar", "")
            })
        return render_template('search.html', actors=formatted_actors, actor_query=actor_name)
    
    # No actors found
    return render_template('search.html', actor_query=actor_name)

@app.route('/actor/<actor_id>')
def actor_detail(actor_id):
    """Show actor detail page"""
    # Get actor information
    actor_data = get_actor_data(actor_id)
    if not actor_data:
        return redirect(url_for('index'))
    
    # Format actor data
    formatted_actor = {
        "name": actor_data.get("name", ""),
        "image_url": actor_data.get("avatar", ""),
        "birthdate": actor_data.get("birthday", ""),
        "age": actor_data.get("age", ""),
        "height": actor_data.get("height", ""),
        "measurements": f"{actor_data.get('bust', '')} - {actor_data.get('waistline', '')} - {actor_data.get('hipline', '')}" if actor_data.get('bust') else "",
        "birthplace": actor_data.get("birthplace", ""),
        "hobby": actor_data.get("hobby", "")
    }
    
    # 修改：获取演员的电影基本信息，不预先获取详情
    actor_movies = get_actor_movies(actor_id)
    # 使用修改后的format_movie_data函数处理简化的电影数据
    formatted_movies = [format_movie_data(movie) for movie in actor_movies]
    
    return render_template('actor.html', actor=formatted_actor, actor_movies=formatted_movies)


@app.route('/api/actor/<actor_id>/refresh-av-league', methods=['POST'])
def refresh_actor_av_league(actor_id):
    """Refresh actor data from AV-League

    This endpoint is called when user clicks on actor avatar to:
    1. Get actor name from database
    2. Search av-league using the actor name
    3. Merge av-league data with existing database data
    4. Return the updated data
    """
    try:
        # First, get existing actor data from database
        existing_data = db.get_star_info_for_display(actor_id)
        if not existing_data:
            return jsonify({
                "success": False,
                "error": "Actor not found in database"
            }), 404

        actor_name = existing_data.get("name", "")
        if not actor_name:
            return jsonify({
                "success": False,
                "error": "Actor name not available"
            }), 400

        logging.info(f"[AV-League] 开始获取演员 {actor_name} ({actor_id}) 的数据")

        # Search av-league using actor name
        av_league_data = av_league_scraper.search_actress(actor_name)

        if not av_league_data:
            logging.warning(f"[AV-League] 未找到演员 {actor_name} 的数据")
            return jsonify({
                "success": False,
                "error": "Actor not found on AV-League"
            }), 404

        # Normalize av-league data to match javbus format
        normalized_data = av_league_scraper.normalize_for_javbus(av_league_data)

        # Merge with existing data (av-league doesn't override javbus basic fields)
        # Update database with merged data
        if not db.update_star_with_av_league_data(actor_id, normalized_data):
            logging.error(f"[AV-League] 保存演员 {actor_id} 的数据失败")
            return jsonify({
                "success": False,
                "error": "Failed to save actor data"
            }), 500

        # Get updated data from database
        updated_data = db.get_star_info_for_display(actor_id)

        logging.info(f"[AV-League] 成功获取并保存演员 {actor_name} 的数据")

        return jsonify({
            "success": True,
            "data": updated_data
        })

    except Exception as e:
        logging.error(f"[AV-League] 刷新演员数据失败: {str(e)}")
        logging.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/actor/<actor_id>/detail', methods=['GET'])
def get_actor_detail_api(actor_id):
    """Get actor detail data for frontend display

    Returns cached data from database without fetching from external sources.
    This is fast and used for initial page load.
    """
    try:
        actor_data = db.get_star_info_for_display(actor_id)
        if not actor_data:
            return jsonify({
                "success": False,
                "error": "Actor not found"
            }), 404

        return jsonify({
            "success": True,
            "data": actor_data
        })
    except Exception as e:
        logging.error(f"获取演员详情失败: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/movie/<movie_id>')
def movie_detail(movie_id):
    """Show movie detail page"""
    # Check if it looks like an uncensored movie ID
    is_likely_uncensored = bool(re.search(r'_\d+$', movie_id))
    
    # Fetch movie data - our updated get_movie_data function will handle uncensored movies
    movie_data = get_movie_data(movie_id)
    
    if movie_data:
        formatted_movie = format_movie_data(movie_data)
        
        # Check if it's still missing important information like magnets
        if CURRENT_API_URL and not movie_data.get("magnets") and (movie_data.get("is_uncensored", False) or is_likely_uncensored):
            # For uncensored movies, we'll need to fetch magnets separately with type=uncensored
            try:
                logging.info(f"Fetching magnets for uncensored movie {movie_id}")
                magnet_url = f"{CURRENT_API_URL}/magnets/{movie_id}"
                params = {"type": "uncensored"}
                
                # Extract gid and uc if available
                gid = movie_data.get("gid", "")
                uc = movie_data.get("uc", "0")
                if gid:
                    params["gid"] = gid
                if uc:
                    params["uc"] = uc
                
                response = requests.get(magnet_url, params=params)
                if response.status_code == 200:
                    magnets = response.json()
                    # Update movie data with magnets
                    movie_data["magnets"] = magnets
                    db.save_movie(movie_data)
                    
                    # Re-format movie data to include magnets
                    formatted_movie = format_movie_data(movie_data)
                    logging.info(f"Successfully fetched magnets for {movie_id}")
            except Exception as e:
                logging.error(f"Failed to fetch magnets for uncensored movie: {str(e)}")
        
        # Check if movie is in favorites
        favorites = load_favorites()
        formatted_movie["is_favorite"] = movie_id in favorites
        
        # Note: We'll fetch summary asynchronously if it's missing
        has_summary = bool(formatted_movie.get("summary") or movie_data.get("description"))
        
        # Get magnet links for this movie if they're not already fetched
        if CURRENT_API_URL and not formatted_movie.get("magnet_links"):
            try:
                # Extract gid and uc from movie data if available
                gid = movie_data.get("gid", "")
                uc = movie_data.get("uc", "0")
                
                # Call the API to get magnet links
                magnet_url = f"{CURRENT_API_URL}/magnets/{movie_id}"
                params = {}
                
                # For uncensored movies, we need to include the type parameter
                if formatted_movie.get("is_uncensored", False) or is_likely_uncensored:
                    params["type"] = "uncensored"
                
                if gid:
                    params["gid"] = gid
                if uc:
                    params["uc"] = uc
                
                logging.info(f"Fetching magnets for {movie_id} with params: {params}")
                response = requests.get(magnet_url, params=params)
                if response.status_code == 200:
                    magnets = response.json()
                    # Format and sort magnets (HD first, then by size)
                    formatted_magnets = []
                    for magnet in magnets:
                        formatted_magnets.append({
                            "name": magnet.get("title", ""),
                            "size": magnet.get("size", ""),
                            "link": magnet.get("link", ""),
                            "date": magnet.get("date", ""),
                            "is_hd": magnet.get("isHD", False),
                            "has_subtitle": magnet.get("hasSubtitle", False)
                        })
                    
                    # Sort magnets: HD first, then with subtitles, then by size
                    formatted_magnets.sort(key=lambda x: (
                        not x["is_hd"],  # HD first
                        not x["has_subtitle"],  # Subtitles second
                        -float(x["size"].replace("GB", "").replace("MB", "").strip()) if x["size"] else 0  # Size third (descending)
                    ))
                    
                    formatted_movie["magnet_links"] = formatted_magnets
                    
                    # Save magnets in the original data for future use
                    movie_data["magnets"] = magnets
                    db.save_movie(movie_data)
            except Exception as e:
                logging.error(f"Failed to get magnet links: {str(e)}")
        
        # Update STRM library metadata if exists for this movie ID
        try:
            # 确保数据库中存在必要的列
            db.add_video_id_column_if_not_exists()
            db.add_cover_and_actors_columns_if_not_exists()
            db.add_date_column_if_not_exists()
            
            # 查找所有带有此video_id的STRM文件
            strm_files = db.get_strm_files()
            matched_files = []
            
            for file in strm_files:
                if file.get('video_id') == movie_id:
                    matched_files.append(file)
            
            if matched_files:
                logging.info(f"为 {movie_id} 找到了 {len(matched_files)} 个STRM文件记录，更新它们的元数据")
                
                # 准备演员数据 (JSON格式)
                actors_data = []
                for actor in formatted_movie.get("actors", []):
                    actors_data.append({
                        "id": actor.get("id", ""),
                        "name": actor.get("name", ""),
                        "image_url": actor.get("image_url", "")
                    })
                
                # 将演员数据序列化为JSON字符串
                actors_json = json.dumps(actors_data)
                
                # 获取封面图片URL
                cover_image = formatted_movie.get("image_url", "")
                
                # 获取电影标题和发布日期
                movie_title = formatted_movie.get("title", "")
                movie_date = formatted_movie.get("date", "")
                
                # 更新每个匹配的文件
                for file in matched_files:
                    # 更新元数据
                    db.update_strm_metadata(
                        file_id=file.get('id'),
                        video_id=movie_id,
                        cover_image=cover_image,
                        actors=actors_json
                    )
                    
                    # 更新标题和日期
                    db.update_strm_movie_info(
                        file_id=file.get('id'),
                        title=movie_title,
                        date=movie_date
                    )
                
                logging.info(f"成功更新STRM文件的元数据，影片ID: {movie_id}")
                
            # 同样更新115云盘文件库
            try:
                # 查找所有带有此video_id的cloud115文件
                cloud115_files = db.get_cloud115_files()
                cloud115_matched_files = []
                
                for file in cloud115_files:
                    if file.get('video_id') == movie_id:
                        cloud115_matched_files.append(file)
                
                if cloud115_matched_files:
                    logging.info(f"为 {movie_id} 找到了 {len(cloud115_matched_files)} 个115云盘文件记录，更新它们的元数据")
                    
                    # 准备演员数据 (JSON格式)
                    actors_data = []
                    for actor in formatted_movie.get("actors", []):
                        actors_data.append({
                            "id": actor.get("id", ""),
                            "name": actor.get("name", ""),
                            "image_url": actor.get("image_url", "")
                        })
                    
                    # 将演员数据序列化为JSON字符串
                    actors_json = json.dumps(actors_data)
                    
                    # 获取封面图片URL
                    cover_image = formatted_movie.get("image_url", "")
                    
                    # 获取电影标题和发布日期
                    movie_title = formatted_movie.get("title", "")
                    movie_date = formatted_movie.get("date", "")
                    
                    # 更新每个匹配的文件
                    for file in cloud115_matched_files:
                        # 更新元数据
                        db.update_cloud115_metadata(
                            file_id=file.get('id'),
                            video_id=movie_id,
                            cover_image=cover_image,
                            actors=actors_json
                        )
                        
                        # 更新标题和日期
                        db.update_cloud115_movie_info(
                            file_id=file.get('id'),
                            title=movie_title,
                            date=movie_date
                        )
                    
                    logging.info(f"成功更新115云盘文件的元数据，影片ID: {movie_id}")
            except Exception as e:
                logging.error(f"更新115云盘文件元数据时出错: {str(e)}")
                logging.error(traceback.format_exc())
                
        except Exception as e:
            logging.error(f"更新STRM文件元数据时出错: {str(e)}")
            logging.error(traceback.format_exc())

        # 获取演员详细信息（用于 tooltip 显示）
        # 优先从数据库获取，不调用 javbus API，提高页面加载速度
        actors_detail_map = {}
        if formatted_movie.get("actors"):
            for actor in formatted_movie["actors"]:
                actor_id = actor.get("id")
                if actor_id and actor_id not in actors_detail_map:
                    try:
                        # 使用 get_star_info_for_display 从数据库获取
                        # 不会调用外部 API，页面加载更快
                        actor_detail = db.get_star_info_for_display(actor_id)
                        if actor_detail:
                            actors_detail_map[actor_id] = actor_detail
                        else:
                            # 如果数据库没有，创建一个最小化的信息
                            actors_detail_map[actor_id] = {
                                "id": actor_id,
                                "name": actor.get("name", ""),
                                "avatar": actor.get("avatar", "")
                            }
                    except Exception as e:
                        logging.warning(f"获取演员 {actor_id} 详情失败: {str(e)}")

        return render_template('movie.html',
                              movie=formatted_movie,
                              has_summary=has_summary,
                              movie_id=movie_id,
                              watch_url_prefix=CURRENT_WATCH_URL_PREFIX,
                              actors_detail=actors_detail_map)
    else:
        return redirect(url_for('index'))

@app.route('/video_player/<movie_id>')
def video_player(movie_id):
    """Show ad-free video player page"""
    try:
        from urllib.parse import urlparse
        t_cfg = CURRENT_CONFIG.get("transcription") or {}
        base = (t_cfg.get("api_base_url") or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        parsed = urlparse(base) if base else None
        transcription_ws_host = parsed.hostname if parsed else ""
        transcription_ws_port = str(parsed.port) if parsed and parsed.port else "8001"

        # Get movie data to display title
        movie_data = get_movie_data(movie_id)
        if not movie_data:
            return render_template('error.html', 
                              error_title="Movie Not Found", 
                              error_message=f"Could not find movie data for {movie_id}"), 404
        
        formatted_movie = format_movie_data(movie_data)
        
        # Try to find video URL or magnet link
        video_url = ""
        hls_url = ""
        magnet_link = ""
        
        # Try to fetch HLS stream URL from external source - using the similar method as the Windows app
        if CURRENT_WATCH_URL_PREFIX and video_player_adapter:
            try:
                # 修正：使用正确的URL格式：https://missav.ai/MOVIE-ID
                target_url = f"{CURRENT_WATCH_URL_PREFIX}/{movie_id}"
                logging.info(f"Fetching video page for {movie_id}: {target_url}")
                
                # 创建会话用于请求
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Referer": CURRENT_WATCH_URL_PREFIX,
                })

                # 使用适配器获取视频流URL
                logging.info("使用VideoAPIAdapter获取视频流")
                adapter = video_player_adapter.VideoAPIAdapter(retry=3, delay=2)
                hls_url = video_player_adapter.get_video_stream_url(target_url, session)
                
                if hls_url:
                    logging.info(f"成功获取HLS URL: {hls_url}")
                else:
                    logging.error(f"无法获取视频流URL")
            except Exception as e:
                logging.error(f"Error fetching video stream URL: {str(e)}")
                import traceback
                logging.error(traceback.format_exc())
        
        # If HLS URL was not found, fallback to direct link
        if not hls_url and CURRENT_WATCH_URL_PREFIX:
            video_url = f"{CURRENT_WATCH_URL_PREFIX}/{movie_id}"
            logging.info(f"Using direct video URL for {movie_id}: {video_url}")
        
        # Check if we have magnet links as another fallback
        if formatted_movie.get("magnet_links") and len(formatted_movie["magnet_links"]) > 0:
            # Get the best quality magnet link (first one after sorting)
            magnet_link = formatted_movie["magnet_links"][0]["link"]
            logging.info(f"Using magnet link as fallback for {movie_id}")
        
        tconf = CURRENT_CONFIG.get("transcription") or {}
        return render_template('video_player.html', 
                              movie=formatted_movie,
                              video_url=video_url,
                              hls_url=hls_url,
                              magnet_link=magnet_link,
                              movie_id=movie_id,
                              fwh_ws_host=transcription_ws_host,
                              fwh_ws_port=transcription_ws_port,
                              fwh_model=tconf.get("model"),
                              fwh_language=tconf.get("language"),
                              fwh_chunk_secs=tconf.get("chunk_secs", 4.0),
                              fwh_overlap_secs=tconf.get("overlap_secs", 0.7),
                              fwh_prefix_chars=tconf.get("prefix_chars", 0),
                              fwh_segmenter=tconf.get("segmenter", "vad"),
                              fwh_vad_max_window=tconf.get("vad_max_window_secs", 15.0),
                              fwh_vad_overlap=tconf.get("vad_overlap_secs", 0.35),
                              fwh_vad_min_silence=tconf.get("vad_min_silence_secs", 0.4),
                              fwh_vad_min_speech=tconf.get("vad_min_speech_secs", 0.6),
                              fwh_vad_frame=tconf.get("vad_frame_secs", 0.03),
                              fwh_vad_energy=tconf.get("vad_energy_threshold", 0.001))
    except Exception as e:
        error_message = str(e)
        logging.error(f"Error in video_player route: {error_message}")
        import traceback
        logging.error(traceback.format_exc())
        return render_template('error.html', 
                              error_title="Video Player Error", 
                              error_message=error_message), 500

# -----------------------------
# 下载 MP4 相关 API
# -----------------------------
@app.route('/api/download_mp4', methods=['POST'])
def api_download_mp4():
    """启动后台任务：将 HLS(m3u8) 保存为本地 MP4。
    JSON: { "movie_id": "IPX-123", "quality": "720p", "filename": "IPX-123.mp4" }
    """
    try:
        data = request.get_json(force=True) or {}
        movie_id = str(data.get('movie_id', '')).strip()
        quality = data.get('quality')
        filename = str(data.get('filename') or f"{movie_id}.mp4").strip()
        if not movie_id:
            return jsonify({"success": False, "message": "movie_id 必填"}), 400

        # 生成 m3u8 播放地址
        hls_url = None
        try:
            if CURRENT_WATCH_URL_PREFIX and video_player_adapter:
                target_url = f"{CURRENT_WATCH_URL_PREFIX}/{movie_id}"
                session = requests.Session()
                session.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Referer": CURRENT_WATCH_URL_PREFIX,
                })
                adapter = video_player_adapter.VideoAPIAdapter(retry=3, delay=2)
                hls_url = video_player_adapter.get_video_stream_url(target_url, session, quality)
        except Exception as e:
            logging.warning(f"获取 HLS 地址失败: {e}")

        if not hls_url:
            hls_url = f"{CURRENT_WATCH_URL_PREFIX}/{movie_id}"

        # 检查 ffmpeg 是否可用
        if not shutil.which('ffmpeg'):
            return jsonify({
                "success": False,
                "message": "未检测到 ffmpeg，请在运行环境安装并加入 PATH 后重试"
            }), 500

        # 后台任务
        task_id = uuid.uuid4().hex
        safe_name = secure_filename(filename) or f"{movie_id}.mp4"
        output_path = os.path.join(DOWNLOAD_DIR, safe_name)
        DOWNLOAD_TASKS[task_id] = {
            "movie_id": movie_id,
            "m3u8": hls_url,
            "output": output_path,
            "status": "queued",
            "created_at": int(time.time())
        }

        t = threading.Thread(target=_run_ffmpeg_hls_to_mp4, args=(task_id, hls_url, output_path), daemon=True)
        t.start()

        return jsonify({
            "success": True,
            "task_id": task_id,
            "status": DOWNLOAD_TASKS[task_id]["status"],
            "output_path": output_path
        })
    except Exception as e:
        logging.exception("download_mp4 启动失败")
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/download_mp4/status/<task_id>', methods=['GET'])
def api_download_mp4_status(task_id):
    task = DOWNLOAD_TASKS.get(task_id)
    if not task:
        return jsonify({"success": False, "message": "任务不存在"}), 404
    resp = {"success": True, **task}
    if task.get("status") == "completed":
        filename = os.path.basename(task.get("output"))
        resp["download_url"] = url_for('downloaded_file', filename=filename)
    return jsonify(resp)

@app.route('/downloads/<path:filename>', methods=['GET'])
def downloaded_file(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

@app.route('/favorites')
def favorites():
    """Show favorites page"""
    favorites_list = load_favorites()
    favorite_movies = []
    
    for movie_id in favorites_list:
        movie_data = get_movie_data(movie_id)
        if movie_data:
            formatted_movie = format_movie_data(movie_data)
            favorite_movies.append(formatted_movie)
    
    return render_template('favorites.html', favorites=favorite_movies)

@app.route('/refresh_movie/<movie_id>')
def refresh_movie(movie_id):
    """Force refresh movie data from API"""
    try:
        # 强制从数据库中删除电影数据，确保重新获取
        db.delete_movie(movie_id)
        logging.info(f"Deleted existing movie data for {movie_id} to force refresh")
        
        # 使用get_movie_data函数获取电影数据，它已经包含了API和爬虫的回退逻辑
        movie_data = get_movie_data(movie_id)
        
        if movie_data:
            logging.info(f"Successfully refreshed movie data for {movie_id}")
            return redirect(url_for('movie_detail', movie_id=movie_id))
        else:
            logging.error(f"Failed to refresh movie data for {movie_id}")
            return render_template('error.html', 
                                  error_title="刷新失败", 
                                  error_message=f"无法获取影片 {movie_id} 的信息。"), 404
    except Exception as e:
        logging.error(f"Failed to refresh movie data: {str(e)}")
        return render_template('error.html', 
                              error_title="刷新错误", 
                              error_message=f"发生错误: {str(e)}"), 500

# Routes: API endpoints
@app.route('/api/check_connection', methods=['GET'])
def check_api_connection():
    """Check API connection status"""
    api_url = request.args.get('api_url', CURRENT_API_URL)
    javbus_mode = str(CURRENT_CONFIG.get("javbus", {}).get("mode", "external")).lower()

    if javbus_mode == "internal":
        return jsonify({"status": "success", "message": "JavBus 内部模式运行中"})
    
    if not api_url:
        return jsonify({"status": "error", "message": "API URL is not set"}), 400
    
    try:
        # Try to connect to the API
        url = f"{api_url}/stars/1"  # Try to request the first page of stars
        response = requests.get(url, timeout=5)
        
        if response.status_code == 200:
            return jsonify({"status": "success", "message": "API connection successful"})
        else:
            return jsonify({"status": "error", "message": f"API connection error: HTTP {response.status_code}"}), 400
    
    except Exception as e:
        return jsonify({"status": "error", "message": f"API connection failed: {str(e)}"}), 400

@app.route('/api/translate', methods=['POST'])
def translate_text():
    """Translate text using the configured translation service"""
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({"status": "error", "message": "Missing text to translate"}), 400
    
    text = data.get('text')
    translate_summary = data.get('translate_summary', False)
    movie_id = data.get('movie_id', '')
    
    try:
        # Use the translator to translate the text
        # Since the translator uses signals, we need to implement a synchronous version
        api_url = CURRENT_CONFIG.get("translation", {}).get("api_url", "")
        api_token = CURRENT_CONFIG.get("translation", {}).get("api_token", "")
        model = CURRENT_CONFIG.get("translation", {}).get("model", "gpt-3.5-turbo")
        source_lang = CURRENT_CONFIG.get("translation", {}).get("source_lang", "日语")
        target_lang = CURRENT_CONFIG.get("translation", {}).get("target_lang", "中文")
        
        # Check if API URL and token are set
        if not api_url:
            return jsonify({"status": "error", "message": "Translation API URL is not set"}), 400
        
        # Check if API token is set (not needed for local Ollama)
        is_ollama = "localhost:11434" in api_url or "192.168.1.133:11434" in api_url
        if not api_token and not is_ollama:
            return jsonify({"status": "error", "message": "Translation API token is not set"}), 400
        
        # Prepare request headers
        headers = {
            "Content-Type": "application/json"
        }
        
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        
        # Prepare request data
        prompt = f"Translate the following {source_lang} text to {target_lang}. Only return the translated text, no explanations:\n\n{text}"
        
        # Add debugging
        logging.info(f"Translation request: API URL = {api_url}, Model = {model}")
        logging.info(f"Text to translate: {text}")
        
        # Build request payload based on API type
        if is_ollama:
            if "/api/chat" in api_url:
                # 使用chat接口的格式
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": f"你是一个专业的{source_lang}到{target_lang}翻译器。"},
                        {"role": "user", "content": prompt}
                    ],
                    "stream": False,  # 关键：禁用流式输出
                    "options": {
                        "temperature": 0.3,
                        "top_p": 0.9
                    }
                }
            else:
                # generate接口的格式
                payload = {
                    "model": model,
                    "prompt": f"你是一个专业的{source_lang}到{target_lang}翻译器。\n{prompt}",
                    "stream": False,
                    "options": {
                            "temperature": 0.3,
                        "top_p": 0.9
                    }
                }
        else:
            # Standard OpenAI-compatible format
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": f"You are a professional {source_lang} to {target_lang} translator."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3
            }
        
        # Send request
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=60
        )
        
        # Log the response for debugging
        logging.info(f"Translation API response status: {response.status_code}")
        try:
            response_text = response.text[:500]  # Limit log size
            logging.info(f"Translation API response: {response_text}")
        except:
            logging.info("Could not log response text")
        
        # Parse response
        if response.status_code == 200:
            result = response.json()
            
            # Extract translated text from different response formats
            translated_text = ""
            
            # Ollama API format
            if is_ollama:
                if "response" in result:
                    translated_text = result["response"].strip()
                elif "message" in result and isinstance(result["message"], dict):
                    if "content" in result["message"] and result["message"]["content"]:
                        translated_text = result["message"]["content"].strip()
            
            # Standard OpenAI format
            elif "choices" in result and len(result["choices"]) > 0:
                choice = result["choices"][0]
                if "message" in choice and "content" in choice["message"]:
                    translated_text = choice["message"]["content"].strip()
                elif "text" in choice:  # Some APIs may use text field directly
                    translated_text = choice["text"].strip()
            
            logging.info(f"Extracted translated text: {translated_text}")
            
            if translated_text:
                # If movie_id is provided, save the translation to database
                if movie_id:
                    try:
                        # Import database functions
                        from javbus_db import update_movie_translation
                        
                        if translate_summary:
                            # Update translated summary
                            update_movie_translation(movie_id, translated_summary=translated_text)
                        else:
                            # Update translated title
                            update_movie_translation(movie_id, translated_title=translated_text)
                        
                        logging.info(f"Translation saved to database for movie {movie_id}")
                    except Exception as e:
                        logging.warning(f"Failed to save translation to database: {str(e)}")
                        # Continue even if database save fails
                
                return jsonify({"status": "success", "translated_text": translated_text})
            else:
                return jsonify({"status": "error", "message": "Could not extract translated text from API response"}), 500
        else:
            return jsonify({"status": "error", "message": f"Translation request failed: HTTP {response.status_code}"}), 500
    
    except Exception as e:
        logging.error(f"Translation process error: {str(e)}")
        return jsonify({"status": "error", "message": f"Translation process error: {str(e)}"}), 500

@app.route('/api/save_translation/<movie_id>', methods=['POST'])
def save_translation(movie_id):
    """Save the translated title and summary to the database"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Missing translation data"}), 400
    
    translated_title = data.get('translated_title')
    translated_summary = data.get('translated_summary')
    
    try:
        # Get the movie data
        movie_data = get_movie_data(movie_id)
        if not movie_data:
            return jsonify({"status": "error", "message": "Movie not found"}), 404
        
        # Update the movie data with the translated information
        if translated_title:
            movie_data['translated_title'] = translated_title
        
        if translated_summary:
            movie_data['translated_description'] = translated_summary
        
        # Save to database
        db.save_movie(movie_data)
        
        return jsonify({"status": "success"})
    except Exception as e:
        logging.error(f"Failed to save translation: {str(e)}")
        return jsonify({"status": "error", "message": f"Failed to save translation: {str(e)}"}), 500

@app.route('/api/toggle_favorite/<movie_id>', methods=['POST'])
def toggle_favorite(movie_id):
    """Toggle a movie's favorite status"""
    favorites = load_favorites()
    
    if movie_id in favorites:
        favorites.remove(movie_id)
        is_favorite = False
    else:
        favorites.append(movie_id)
        is_favorite = True
    
    save_favorites(favorites)
    
    return jsonify({"status": "success", "is_favorite": is_favorite})

@app.route('/api/clear_favorites', methods=['POST'])
def clear_favorites():
    """Clear all favorites"""
    save_favorites([])
    
    return jsonify({"status": "success"})

@app.route('/images/<path:filename>')
def serve_image(filename):
    """Serve images from the buspic directory"""
    # Define the fallback image path
    fallback_image = "static/images/no-cover.jpg"
    
    # Make sure the fallback image exists
    if not os.path.exists(fallback_image):
        try:
            # Try to import and run the placeholder image generation script
            import serve_image_fallback
            serve_image_fallback.create_placeholder_image()
        except ImportError:
            # If the script doesn't exist, create a simple directory
            os.makedirs("static/images", exist_ok=True)
            logging.warning("Fallback image creation script not available. Created directory only.")
    
    # Split the path to get the movie ID and actual filename
    parts = filename.split('/')
    if len(parts) < 2:
        return send_from_directory(os.path.dirname(fallback_image), os.path.basename(fallback_image))
    
    # Check if this is an actor image from the unified actor directory
    if parts[0] == 'actor':
        actor_id = parts[1].split('.')[0]  # Extract actor_id from filename
        directory = os.path.join("buspic", "actor")
        
        # Check if the directory exists
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        # Check if the file exists
        file_path = os.path.join(directory, parts[1])
        if not os.path.exists(file_path):
            # Try to download the image
            try:
                actor_data = get_actor_data(actor_id)
                if actor_data:
                    image_url = actor_data.get("avatar", "")
                    if image_url:
                        download_image(image_url, file_path)
            except Exception:
                pass
        
        # If file exists now, serve it
        if os.path.exists(file_path):
            return send_from_directory(directory, parts[1])
        
        # Otherwise return the fallback image
        return send_from_directory(os.path.dirname(fallback_image), os.path.basename(fallback_image))
    
    # Check if this is a cover image
    if parts[0] == 'covers':
        movie_id = parts[1].split('.')[0]  # Extract movie_id from filename
        directory = os.path.join("buspic", "covers")
        
        # Check if the directory exists
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
        
        # Check if the file exists
        file_path = os.path.join(directory, parts[1])
        if not os.path.exists(file_path):
            # 修改：使用get_movie_image_url函数获取图片URL，避免获取完整电影详情
            try:
                # 获取图片URL而不是完整的电影数据
                image_url = get_movie_image_url(movie_id)
                if image_url and not image_url.startswith('/static/'):  # Skip if it's a fallback image
                    success = download_image(image_url, file_path)
                    if not success:
                        # 如果下载失败，尝试获取完整数据以找到样本图
                        movie_data = get_movie_data(movie_id)
                        if movie_data and "samples" in movie_data:
                            samples = movie_data.get("samples", [])
                            if samples and len(samples) > 0:
                                sample_url = samples[0].get("src", "")
                                if not sample_url:
                                    sample_url = samples[0].get("thumbnail", "")
                                if sample_url:
                                    download_image(sample_url, file_path)
            except Exception:
                pass
        
        # If file exists now, serve it
        if os.path.exists(file_path):
            return send_from_directory(directory, parts[1])
        
        # Otherwise return the fallback image
        return send_from_directory(os.path.dirname(fallback_image), os.path.basename(fallback_image))
    
    # Regular movie image handling (sample images and movie-specific covers)
    movie_id = parts[0]
    image_name = parts[-1]
    directory = os.path.join("buspic", movie_id)
    
    # Check if the directory exists
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    
    # Check if the file exists
    file_path = os.path.join(directory, image_name)
    if not os.path.exists(file_path):
        # Try to download the image
        try:
            if "cover" in image_name:
                # 修改：对于封面图片，先尝试使用简化方法获取URL
                image_url = get_movie_image_url(movie_id)
                if image_url and not image_url.startswith('/static/'):  # Skip if it's a fallback image
                    success = download_image(image_url, file_path)
                    # Also save to covers directory
                    if success:
                        cover_path = os.path.join("buspic", "covers", f"{movie_id}.jpg")
                        os.makedirs(os.path.dirname(cover_path), exist_ok=True)
                        import shutil
                        shutil.copy2(file_path, cover_path)
                    else:
                        # 如果简化方法失败，再尝试获取完整数据
                        movie_data = get_movie_data(movie_id)
                        if movie_data and "samples" in movie_data:
                            samples = movie_data.get("samples", [])
                            if samples and len(samples) > 0:
                                sample_url = samples[0].get("src", "")
                                if not sample_url:
                                    sample_url = samples[0].get("thumbnail", "")
                                if sample_url and download_image(sample_url, file_path):
                                    shutil.copy2(file_path, cover_path)
                else:
                    # 如果获取不到图片URL，尝试完整获取电影数据
                    movie_data = get_movie_data(movie_id)
                    if movie_data:
                        full_image_url = movie_data.get("img", "")
                        if full_image_url and not full_image_url.startswith('/static/'):  # Skip if it's a fallback image
                            success = download_image(full_image_url, file_path)
                            # Also save to covers directory
                            if success:
                                cover_path = os.path.join("buspic", "covers", f"{movie_id}.jpg")
                                os.makedirs(os.path.dirname(cover_path), exist_ok=True)
                                import shutil
                                shutil.copy2(file_path, cover_path)
                
            elif "actor_" in image_name:
                # Extract actor ID from filename (e.g., actor_123.jpg)
                actor_id = image_name.split('_')[1].split('.')[0]
                
                # First check if the actor image exists in the actor directory
                actor_path = os.path.join("buspic", "actor", f"{actor_id}.jpg")
                if os.path.exists(actor_path):
                    # Copy the file from actor directory
                    import shutil
                    shutil.copy2(actor_path, file_path)
                else:
                    # Download actor image to both locations
                    actor_data = get_actor_data(actor_id)
                    if actor_data:
                        image_url = actor_data.get("avatar", "")
                        if image_url:
                            # Download to unified actor directory first
                            os.makedirs(os.path.dirname(actor_path), exist_ok=True)
                            if download_image(image_url, actor_path):
                                # Copy to movie-specific location
                                shutil.copy2(actor_path, file_path)
            elif "sample_" in image_name:
                # 对于样本图片，需要获取完整电影数据
                movie_data = get_movie_data(movie_id)
                if movie_data:
                    # Extract sample index from filename (e.g., sample_1.jpg)
                    try:
                        sample_index = int(image_name.split('_')[1].split('.')[0]) - 1
                        samples = movie_data.get("samples", [])
                        if samples and 0 <= sample_index < len(samples):
                            # 首先尝试获取全尺寸图片，如果没有则使用缩略图
                            sample_url = samples[sample_index].get("src", "")
                            if not sample_url:
                                sample_url = samples[sample_index].get("thumbnail", "")
                            
                            if sample_url:
                                download_image(sample_url, file_path)
                    except (ValueError, IndexError):
                        pass
        except Exception:
            pass
    
    # If file exists now, serve it
    if os.path.exists(file_path):
        return send_from_directory(directory, image_name)
    
    # Otherwise return the fallback image
    return send_from_directory(os.path.dirname(fallback_image), os.path.basename(fallback_image))

# Helper functions
def get_movie_data(movie_id):
    """Get movie data from database or API"""
    # Get the caller function name
    caller_function = sys._getframe().f_back.f_code.co_name
    
    # Try to get from database first; relax expiry for favorites page
    max_age_days = 7300  # 20 years for all callers; use explicit refresh to update
    movie_data = db.get_movie(movie_id, max_age=max_age_days)
    
    # Check if it's likely an uncensored movie based on ID pattern or if data is incomplete
    is_likely_uncensored = bool(re.search(r'_\d+$', movie_id))  # IDs like xxx_001 are often uncensored
    is_data_incomplete = not movie_data or not (movie_data.get("director") or movie_data.get("publisher") or movie_data.get("producer") or movie_data.get("magnets"))
    
    # Only fetch from API if we're in the movie_detail route or related functions
    # Don't fetch when displaying the cloud115_library or cloud115_library_search pages
    should_fetch_from_api = ('movie_detail' in caller_function or 
                           'update_cloud115_video_id' in caller_function or
                           'refresh_movie' in caller_function or
                           'sync_cloud115_movie_info' in caller_function or
                           'sync_strm_movie_info' in caller_function)
    
    # If not in database or data is incomplete, try to get from API only if we should fetch
    if is_data_incomplete and should_fetch_from_api:
        javbus_api_success = False
        try:
            logging.info(f"Fetching complete data for movie {movie_id} from API (is_likely_uncensored={is_likely_uncensored})")
            
            # For uncensored movies, we may need to specify a type parameter
            params = {}
            if is_likely_uncensored:
                params["type"] = "uncensored"
                
            movie_data_from_api = javbus_client.get_movie(movie_id, params=params)
            if movie_data_from_api:
                movie_data = movie_data_from_api
                # Save to database
                if not db.save_movie(movie_data):
                    logging.error(f"Failed to save movie data for {movie_id} to database")
                else:
                    logging.info(f"Successfully retrieved and saved complete data for {movie_id}")
                    javbus_api_success = True
            else:
                logging.error(f"JavBus API returned no data for movie {movie_id}")
        except Exception as e:
            logging.error(f"Failed to get movie data from API: {str(e)}")
        
        # If JavBus API failed to return data, try using moviescraper
        if not javbus_api_success and not movie_data:
            try:
                logging.info(f"JavBus API failed for {movie_id}, attempting to use moviescraper")
                # Try to get data using moviescraper
                scraped_movie_info = moviescraper.get_movie_summary(movie_id)
                
                if scraped_movie_info:
                    logging.info(f"Successfully retrieved movie data for {movie_id} using moviescraper ({scraped_movie_info.get('source', 'unknown')})")
                    
                    # Transform scraped data to match expected format
                    movie_data = {
                        "id": movie_id,
                        "title": scraped_movie_info.get('title', movie_id),
                        "date": scraped_movie_info.get('release_date', ''),
                        "img": scraped_movie_info.get('img', ''),
                        "description": scraped_movie_info.get('summary', ''),
                        "duration": scraped_movie_info.get('duration', ''),
                        "director": {"name": scraped_movie_info.get('director', '')},
                        "publisher": {"name": scraped_movie_info.get('maker', scraped_movie_info.get('label', ''))},
                        "series": {"name": scraped_movie_info.get('series', '')},
                        "genres": [{"id": "", "name": genre} for genre in scraped_movie_info.get('genres', [])],
                        "stars": [{"id": "", "name": actress} for actress in scraped_movie_info.get('actresses', [])],
                        "data_source": f"moviescraper:{scraped_movie_info.get('source', 'unknown')}",
                        "samples": scraped_movie_info.get('samples', []) if scraped_movie_info.get('samples') else [{"src": url, "thumbnail": url} for url in scraped_movie_info.get('thumbnails', [])],
                        "product_code": scraped_movie_info.get('product_code', movie_id),
                        "magnets": []
                    }
                    
                    # Save to database
                    if not db.save_movie(movie_data):
                        logging.error(f"Failed to save scraped movie data for {movie_id} to database")
                    else:
                        logging.info(f"Successfully saved scraped movie data for {movie_id}")
            except Exception as e:
                logging.error(f"Failed to get movie data from moviescraper: {str(e)}")
    else:
        # If we got data from the database and we're in movie_detail function, log it
        if movie_data and 'movie_detail' in caller_function:
            logging.info(f"Retrieved movie data for {movie_id} from database")
            
            # If we're viewing the movie details page, always save the movie data again 
            # to ensure it's properly saved according to JavbusDatabase requirements
            logging.info(f"Ensuring movie data for {movie_id} is properly saved in the database")
            if not db.save_movie(movie_data):
                logging.error(f"Failed to update movie data for {movie_id} in database")
    
    return movie_data

def get_actor_data(actor_id):
    """Get actor data from database or API"""
    # Try to get from database first (uses default 365 day expiration)
    actor_data = db.get_star(actor_id)
    
    # If not in database, try to get from API
    if not actor_data:
        try:
            api_actor_data = javbus_client.get_star(actor_id)
            if api_actor_data:
                actor_data = api_actor_data
                # Save to database
                if not db.save_star(actor_data):
                    logging.error(f"Failed to save actor data for {actor_id} to database")
                else:
                    logging.info(f"Successfully retrieved and saved actor data for {actor_id}")
            else:
                logging.error(f"JavBus API returned no data for actor {actor_id}")
        except Exception as e:
            logging.error(f"Failed to get actor data from API: {str(e)}")
    else:
        logging.info(f"Retrieved actor data for {actor_id} from database")
        
        # If we're viewing the actor details page, always save the actor data again
        # to ensure it's properly saved according to JavbusDatabase requirements
        if actor_data and 'actor_detail' in sys._getframe().f_back.f_code.co_name:
            logging.info(f"Ensuring actor data for {actor_id} is properly saved in the database")
            if not db.save_star(actor_data):
                logging.error(f"Failed to update actor data for {actor_id} in database")
    
    return actor_data

def get_actor_movies(actor_id):
    """Get actor's movies from database or API"""
    # Try to get from database first
    movies = db.get_star_movies(actor_id)
    
    # If not in database, try to get from API
    if not movies:
        try:
            all_movies = []
            page = 1
            max_pages = 3  # Limit to 3 pages for performance
            
            while page <= max_pages:
                data = javbus_client.list_star_movies(actor_id, page=page)
                if not isinstance(data, dict):
                    break

                movies_list = data.get("movies", [])
                pagination = data.get("pagination", {})
                
                if not movies_list:
                    break
                
                # 修改：直接使用API返回的基本电影信息，不再获取每部电影的详细信息
                # 只保留必要的基本信息
                for movie in movies_list:
                    # 确保有基本结构，但不调用API获取详情
                    movie_info = {
                        "id": movie.get("id", ""),
                        "title": movie.get("title", ""),
                        "img": movie.get("img", ""),
                        "date": movie.get("date", ""),
                        "stars": []  # 空的演员列表
                    }
                    all_movies.append(movie_info)
                
                # Check if there's a next page
                has_next_page = pagination.get("hasNextPage", False)
                if not has_next_page:
                    break
                
                page += 1
            
            movies = all_movies
        except Exception as e:
            logging.error(f"Failed to get actor movies from API: {str(e)}")
    
    return movies

def format_movie_data(movie_data):
    """Format movie data for template rendering"""
    # Check if it's likely an uncensored movie
    is_uncensored = movie_data.get("is_uncensored", False) or bool(re.search(r'_\d+$', movie_data.get("id", "")))
    
    formatted_movie = {
        "id": movie_data.get("id", ""),
        "title": movie_data.get("title", ""),
        "translated_title": movie_data.get("translated_title", ""),
        "image_url": movie_data.get("img", ""),
        "date": movie_data.get("date", ""),
        "is_uncensored": is_uncensored,
        # For uncensored movies, publisher and producer might be structured differently
        "producer": movie_data.get("publisher", {}).get("name", "") if isinstance(movie_data.get("publisher"), dict) else movie_data.get("publisher", ""),
        "publisher": movie_data.get("publisher", {}),  # Add the publisher object
        # Make sure we also have producer object if it exists
        "producer_obj": movie_data.get("producer", {}),  # Add the producer object
        "director": movie_data.get("director", {}),  # Add the director object
        "series": movie_data.get("series", {}),  # Add the series object
        "videoLength": movie_data.get("videoLength", ""),  # Add video length
        "genres": movie_data.get("genres", []),  # Add full genres objects
        "summary": movie_data.get("description", ""),
        "translated_summary": movie_data.get("translated_description", ""),
        "user_reviews": movie_data.get("user_reviews", []),  # Add user reviews
        "samples": movie_data.get("samples", []),  # Add samples
        "actors": [],
        "magnet_links": [],
        "sample_images": []
    }
    
    # Format actors
    for actor in movie_data.get("stars", []):
        # 检查是否是字典，因为简化数据可能没有完整的演员信息
        if isinstance(actor, dict):
            actor_id = actor.get("id", "")
            formatted_movie["actors"].append({
                "id": actor_id,
                "name": actor.get("name", ""),
                "image_url": actor.get("avatar", "")
            })
    
    # Format magnet links
    for magnet in movie_data.get("magnets", []):
        formatted_movie["magnet_links"].append({
            "name": magnet.get("title", ""),
            "size": magnet.get("size", ""),
            "link": magnet.get("link", ""),
            "date": magnet.get("shareDate", ""),
            "is_hd": magnet.get("isHD", False),
            "has_subtitle": magnet.get("hasSubtitle", False)
        })
    
    # Format sample images
    for i, sample in enumerate(movie_data.get("samples", [])):
        # Handle different types of sample data formats:
        # 1. Dictionary format from JavBus API: {"src": "url", "thumbnail": "url"} 
        # 2. String format from scrapers: "url"
        if isinstance(sample, dict):
            # For samples without src (full-size image), use the thumbnail as both thumbnail and full image
            sample_src = sample.get("src")
            sample_thumbnail = sample.get("thumbnail", "")
            
            # If src is null or empty, use the thumbnail as the source
            if not sample_src and sample_thumbnail:
                sample_src = sample_thumbnail
                can_enlarge = False  # Flag to indicate if image can be enlarged
            else:
                can_enlarge = bool(sample_src)  # Can enlarge only if we have a proper src
        else:
            # Handle the case where sample is a string (direct URL)
            sample_src = sample
            sample_thumbnail = sample
            can_enlarge = True  # Assume direct URLs can be enlarged
            
        formatted_movie["sample_images"].append({
            "index": i + 1,
            "src": sample_src or sample_thumbnail,  # Fallback to thumbnail if src is None
            "thumbnail": sample_thumbnail,
            "url": f"/images/{formatted_movie['id']}/sample_{i+1}.jpg",
            "can_enlarge": can_enlarge  # Add flag to indicate if image can be enlarged
        })
    
    return formatted_movie

def download_image(url, save_path):
    """Download an image from URL and save it to path"""
    max_retries = 2
    retry_delay = 1
    timeout = 5  # Shorter timeout to avoid long waits
    
    for retry in range(max_retries + 1):
        try:
            # 设置请求头，模拟浏览器行为
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Referer": CURRENT_BASE_URL + "/",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            }
            
            # 根据URL来源设置正确的Referer
            if "javbus" in url:
                headers["Referer"] = CURRENT_BASE_URL + "/"
            elif "dmm.co.jp" in url:
                headers["Referer"] = "https://www.dmm.co.jp/"
            else:
                # 从URL中提取域名作为Referer
                domain = url.split('/')[2]
                headers["Referer"] = f"https://{domain}/"
            
            # 首先尝试直接下载图片
            response = requests.get(url, headers=headers, stream=True, timeout=timeout)
            
            # 如果直接下载失败，使用更复杂的会话方法
            if response.status_code != 200:
                # 创建一个会话来保持cookies
                session = requests.Session()
                session.headers.update(headers)
                
                # 对于DMM，需要先访问其主页面以获取必要的cookies
                if "dmm.co.jp" in url:
                    session.get("https://www.dmm.co.jp/", timeout=timeout)
                elif "javbus.com" in url:
                    # 对于javbus，先访问主页获取cookies
                    session.get(f"{CURRENT_BASE_URL}/", timeout=timeout)
                
                # 重新尝试下载图片
                response = session.get(url, stream=True, timeout=timeout)
            
            # 如果下载成功，保存图片
            if response.status_code == 200:
                # Check if the response contains an image
                content_type = response.headers.get('Content-Type', '')
                if not content_type.startswith('image/'):
                    # Try one more time with a different approach if this isn't the last retry
                    if retry < max_retries:
                        continue
                
                # Ensure the directory exists
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                
                with open(save_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                # Check if the file was successfully saved and has content
                if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                    return True
                else:
                    # Continue to retry if this isn't the last attempt
                    if retry < max_retries:
                        continue
            else:
                # Continue to retry if this isn't the last attempt
                if retry < max_retries:
                    time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                    continue
            
            # If we've reached this point on the last retry, we've failed
            if retry == max_retries:
                return False
                
        except requests.exceptions.Timeout:
            if retry < max_retries:
                time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                continue
            return False
        except requests.exceptions.ConnectionError:
            if retry < max_retries:
                time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                continue
            return False
        except Exception:
            if retry < max_retries:
                time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                continue
            return False
    
    # If we've reached this point after all retries, we've failed
    return False

@app.route('/config')
def config_page():
    """Show configuration page"""
    # Load the current configuration
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config_json = f.read()
        
        config_data = {}
        if config_json.strip():
            try:
                config_data = json.loads(config_json)
            except json.JSONDecodeError as exc:
                error_message = f"配置文件格式无效: {exc}"
                logging.error(error_message)
                return render_template('config.html', error_message=error_message, config_json=config_json, config_data={})
        
        return render_template('config.html', config_json=config_json, config_data=config_data)
    except Exception as e:
        error_message = f"Failed to load configuration file: {str(e)}"
        logging.error(error_message)
        return render_template('config.html', error_message=error_message, config_json="{}", config_data={})

@app.route('/api/save_config', methods=['POST'])
def save_config_api():
    """API endpoint to save configuration"""
    try:
        data = request.get_json()
        if not data or 'config' not in data:
            return jsonify({"status": "error", "message": "Missing configuration data"}), 400
        
        config_str = data.get('config')
        
        # Validate JSON format
        try:
            config_data = json.loads(config_str)
        except json.JSONDecodeError as e:
            return jsonify({"status": "error", "message": f"Invalid JSON format: {str(e)}"}), 400
        
        # Save to file
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            f.write(config_str)
        
        # Update current configuration
        global CURRENT_CONFIG
        CURRENT_CONFIG = load_config()
        apply_runtime_configuration()
        
        logging.info(f"Configuration saved successfully")
        
        return jsonify({"status": "success"})
    except Exception as e:
        error_message = f"Failed to save configuration: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/restart_application', methods=['POST'])
def restart_application():
    """API endpoint to restart the application"""
    try:
        import os
        import signal
        import threading
        
        def delayed_restart():
            # Wait a short time to allow the response to be sent
            import time
            time.sleep(1)
            # Send SIGTERM to the application process
            os.kill(os.getpid(), signal.SIGTERM)
        
        # Start a thread to restart the application
        threading.Thread(target=delayed_restart).start()
        
        return jsonify({"status": "success", "message": "Application restart initiated"})
    except Exception as e:
        error_message = f"Failed to restart application: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/get_movie_summary/<movie_id>', methods=['GET'])
def get_movie_summary(movie_id):
    """API endpoint to fetch movie summary using moviescraper module asynchronously"""
    try:
        # Get movie data
        movie_data = get_movie_data(movie_id)
        if not movie_data:
            return jsonify({"status": "error", "message": "Movie not found"}), 404
            
        # If summary already exists, return it
        if movie_data.get("description"):
            return jsonify({
                "status": "success", 
                "summary": movie_data.get("description"),
                "translated_summary": movie_data.get("translated_description", "")
            })
            
        # Try to fetch summary using moviescraper
        logging.info(f"Fetching summary using moviescraper for movie ID: {movie_id}")
        
        # Use the new convenience function from moviescraper
        movie_info = moviescraper.get_movie_summary(movie_id)
        
        if movie_info and movie_info.get('summary'):
            summary = movie_info.get('summary', '')
            scraper_name = movie_info.get('source', 'unknown')
            
            logging.info(f"Found summary for {movie_id} from {scraper_name}")
            
            # Update movie data with summary and additional information
            movie_data["description"] = summary
            movie_data["summary_source"] = f"moviescraper:{scraper_name}"
            
            # Add additional data from movie_info if available
            if movie_info.get('title'):
                movie_data["original_title"] = movie_info.get('title')
            if movie_info.get('genres'):
                movie_data["additional_genres"] = movie_info.get('genres')
            if movie_info.get('actors') or movie_info.get('actresses'):
                movie_data["additional_actors"] = movie_info.get('actors') or movie_info.get('actresses')
            if movie_info.get('release_date'):
                movie_data["original_date"] = movie_info.get('release_date')
            
            # Save to database
            db.save_movie(movie_data)
            
            return jsonify({
                "status": "success", 
                "summary": summary,
                "translated_summary": "",
                "source": scraper_name
            })
        else:
            logging.warning(f"No summary found for {movie_id}")
            return jsonify({"status": "error", "message": "No summary found"}), 404
            
    except Exception as e:
        logging.error(f"Failed to get summary: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/video_player/<movie_id>')
def api_video_player(movie_id):
    """获取影片的在线播放流URL，用于移动端Flutter应用"""
    try:
        logging.info(f"Flutter App requesting video stream for: {movie_id}")

        target_url = f"{CURRENT_WATCH_URL_PREFIX}/{movie_id}"
        logging.info(f"Fetching from: {target_url}")

        # 创建 session 并设置 headers
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Referer": CURRENT_WATCH_URL_PREFIX,
            "Cookie": "age_verify=true",
        })

        # 使用适配器获取视频流URL
        adapter = video_player_adapter.VideoAPIAdapter(retry=3, delay=2)
        hls_url = video_player_adapter.get_video_stream_url(target_url, session)

        if hls_url:
            logging.info(f"Success: Got stream URL for {movie_id}")
            return jsonify({
                "success": True,
                "movie_id": movie_id,
                "stream_url": hls_url,
            })
        else:
            logging.warning(f"Failed: No stream URL found for {movie_id}")
            return jsonify({
                "success": False,
                "movie_id": movie_id,
                "stream_url": None,
                "error": "No stream URL found"
            }), 404

    except Exception as e:
        logging.error(f"Error getting video stream: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "movie_id": movie_id,
            "stream_url": None,
            "error": str(e)
        }), 500

@app.route('/api/proxy/stream')
def proxy_stream():
    """代理HLS视频流内容，解决CORS问题"""
    stream_url = request.args.get('url')
    logging.info(f"视频流代理请求: {stream_url}")
    
    if not stream_url:
        return jsonify({"error": "Missing URL parameter"}), 400
        
    try:
        # 解码URL
        decoded_url = urllib.parse.unquote(stream_url)
        logging.info(f"代理解码后的URL: {decoded_url}")
        
        # 获取URL的基本路径（用于解析相对路径）
        url_parts = urllib.parse.urlparse(decoded_url)
        base_url = f"{url_parts.scheme}://{url_parts.netloc}{os.path.dirname(url_parts.path)}/"
        base_domain = f"{url_parts.scheme}://{url_parts.netloc}"
        
        # 设置请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": request.headers.get("Origin", request.host_url.rstrip("/")),
            "Referer": request.headers.get("Referer", request.host_url)
        }
        
        # 传递一些重要的请求头
        for header in ["Range", "If-Modified-Since", "If-None-Match"]:
            if header in request.headers:
                headers[header] = request.headers[header]
        
        # 发送请求
        response = requests.get(
            decoded_url,
            headers=headers,
            stream=True,
            timeout=10,
            verify=False
        )
        
        # 检查响应状态
        if response.status_code != 200:
            logging.error(f"代理请求失败: HTTP {response.status_code}")
            return jsonify({"error": f"Remote server returned HTTP {response.status_code}"}), response.status_code
            
        # 获取内容类型
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        
        # 特殊处理M3U8文件，修改其中的相对URL为代理URL
        if "application/vnd.apple.mpegurl" in content_type or decoded_url.endswith(".m3u8"):
            logging.info("检测到M3U8文件，进行处理")
            content = response.text
            processed_content = ""
            
            # 处理每一行
            for line in content.splitlines():
                # 跳过注释和空行
                if line.strip() == "" or line.startswith("#"):
                    processed_content += line + "\n"
                    continue
                    
                # 处理URL
                if line.startswith("http"):
                    # 绝对URL
                    absolute_url = line
                elif line.startswith("/"):
                    # 以斜杠开头的相对URL（相对于域名根目录）
                    absolute_url = base_domain + line
                else:
                    # 常规相对URL，转换为绝对URL
                    absolute_url = urllib.parse.urljoin(base_url, line)
                
                # 将URL转换为代理URL
                encoded_url = urllib.parse.quote(absolute_url)
                proxy_url = f"/api/proxy/stream?url={encoded_url}"
                processed_content += proxy_url + "\n"
                logging.info(f"M3U8处理: {line} -> {proxy_url}")
            
            # 创建响应
            proxy_response = Response(
                processed_content,
                status=response.status_code
            )
            
            # 设置内容类型
            proxy_response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
            
        else:
            # 创建响应
            proxy_response = Response(
                stream_with_context(response.iter_content(chunk_size=1024)),
                status=response.status_code
            )
            
            # 设置内容类型
            proxy_response.headers["Content-Type"] = content_type
        
        # 设置CORS头
        proxy_response.headers["Access-Control-Allow-Origin"] = "*"
        proxy_response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        proxy_response.headers["Access-Control-Allow-Headers"] = "Origin, X-Requested-With, Content-Type, Accept, Range"
        
        # 复制其他重要的响应头（只对非M3U8内容）
        if "application/vnd.apple.mpegurl" not in content_type and not decoded_url.endswith(".m3u8"):
            for header in ["Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "Etag"]:
                if header in response.headers:
                    proxy_response.headers[header] = response.headers[header]
                    
        logging.info(f"代理流成功: {content_type}")
        return proxy_response
        
    except Exception as e:
        logging.error(f"代理流失败: {str(e)}")
        return jsonify({"error": str(e)}), 500

def get_movie_image_url(movie_id):
    """仅获取电影的封面图片URL，避免获取完整详情"""
    # 辅助函数：判断是否是无码影片
    def is_uncensored_movie(thumb_url, movie_data=None, movie_id_param=None):
        """判断是否是无码影片"""
        # 方法1：检查缩略图URL中是否包含imgs（无码）或pics（有码）
        if thumb_url and "/imgs/" in thumb_url:
            return True
        if thumb_url and "/pics/" in thumb_url:
            return False
        # 方法2：检查is_uncensored字段
        if movie_data and movie_data.get("is_uncensored", False):
            return True
        # 方法3：通过ID模式判断（如 xxx_001）
        check_id = movie_id_param or movie_id
        if check_id and re.search(r'_\d+$', check_id):
            return True
        return False
    
    # 先尝试从数据库获取只包含图片URL的简单记录
    movie_data = db.get_movie(movie_id)
    if movie_data and movie_data.get("img"):
        # 将缩略图URL转换为高清封面图URL
        thumb_url = movie_data.get("img")
        
        # 处理JavBus格式的URL
        if "thumb" in thumb_url:
            # 从缩略图URL中提取ID部分
            # 例如：从 https://www.javbus.com/pics/thumb/b9f2.jpg 提取 b9f2
            # 或从 https://www.javbus.com/imgs/thumb/1zj1.jpg 提取 1zj1
            thumb_id = thumb_url.split('/')[-1].split('.')[0]
            # 判断是否是无码影片，使用对应的路径
            if is_uncensored_movie(thumb_url, movie_data, movie_id):
                cover_url = f"{CURRENT_BASE_URL}/imgs/cover/{thumb_id}_b.jpg"
            else:
                cover_url = f"{CURRENT_BASE_URL}/pics/cover/{thumb_id}_b.jpg"
            return cover_url
            
        # 处理DMM格式的URL
        if "pics.dmm.co.jp" in thumb_url and "ps.jpg" in thumb_url:
            # 将ps.jpg替换为pl.jpg来获取高清封面图
            cover_url = thumb_url.replace("ps.jpg", "pl.jpg")
            return cover_url
            
        return thumb_url
    
    # 如果数据库中没有，尝试从API获取基本信息
    # 使用安全的方式获取图片URL，添加超时和重试机制
    try:
        # 设置超时时间（秒）和最大重试次数
        timeout = 3
        max_retries = 2
        retry_delay = 1  # 初始重试延迟（秒）
        
        # 使用本地占位符图片路径作为默认返回值
        default_image = f"/static/images/no-cover.jpg"
        
        # 设置请求API的函数，添加重试逻辑
        def fetch_with_retry(url, params=None, current_retry=0):
            try:
                return requests.get(url, params=params, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                if current_retry < max_retries:
                    # 使用指数退避策略增加重试延迟
                    sleep_time = retry_delay * (2 ** current_retry)
                    logging.warning(f"API连接失败，将在{sleep_time}秒后重试: {str(e)}")
                    time.sleep(sleep_time)
                    return fetch_with_retry(url, params, current_retry + 1)
                else:
                    # 超过最大重试次数，记录错误并返回None
                    logging.error(f"API连接失败，已超过最大重试次数: {str(e)}")
                    return None
        
        movies_list = []
        if CURRENT_API_URL:
            search_url = f"{CURRENT_API_URL}/movies/search"
            search_params = {"keyword": movie_id, "page": "1"}
            response = fetch_with_retry(search_url, search_params)
            if response and response.status_code == 200:
                data = response.json()
                movies_list = data.get("movies", [])
        else:
            try:
                search_result = javbus_client.search_movies(keyword=movie_id, page=1)
                movies_list = search_result.get("movies", [])
            except Exception as exc:
                logging.error(f"本地搜索影片封面失败: {exc}")

        for movie in movies_list:
            if movie.get("id") == movie_id and movie.get("img"):
                thumb_url = movie.get("img")

                if "thumb" in thumb_url:
                    thumb_id = thumb_url.split('/')[-1].split('.')[0]
                    # 判断是否是无码影片，使用对应的路径
                    if is_uncensored_movie(thumb_url, movie, movie_id):
                        cover_url = f"{CURRENT_BASE_URL}/imgs/cover/{thumb_id}_b.jpg"
                    else:
                        cover_url = f"{CURRENT_BASE_URL}/pics/cover/{thumb_id}_b.jpg"
                    return cover_url

                if "pics.dmm.co.jp" in thumb_url and "ps.jpg" in thumb_url:
                    cover_url = thumb_url.replace("ps.jpg", "pl.jpg")
                    return cover_url

                return thumb_url
        
        # 如果搜索没有结果，尝试直接获取电影数据（这是最后的选择）
        fallback_movie = None
        if CURRENT_API_URL:
            response = fetch_with_retry(f"{CURRENT_API_URL}/movies/{movie_id}")
            if response and response.status_code == 200:
                fallback_movie = response.json()
        else:
            try:
                fallback_movie = javbus_client.get_movie(movie_id)
            except Exception as exc:
                logging.error(f"本地获取影片封面失败: {exc}")

        if fallback_movie and fallback_movie.get("img"):
            thumb_url = fallback_movie.get("img")

            def _store_basic(cover: str) -> None:
                basic_info = {
                    "id": movie_id,
                    "img": cover,
                    "title": fallback_movie.get("title", ""),
                    "date": fallback_movie.get("date", ""),
                }
                try:
                    db.save_movie(basic_info)
                except Exception as exc:  # pylint: disable=broad-except
                    logging.error(f"保存影片封面基础信息失败: {exc}")

            if "thumb" in thumb_url:
                thumb_id = thumb_url.split('/')[-1].split('.')[0]
                # 判断是否是无码影片，使用对应的路径
                if is_uncensored_movie(thumb_url, fallback_movie, movie_id):
                    cover_url = f"{CURRENT_BASE_URL}/imgs/cover/{thumb_id}_b.jpg"
                else:
                    cover_url = f"{CURRENT_BASE_URL}/pics/cover/{thumb_id}_b.jpg"
                _store_basic(cover_url)
                return cover_url

            if "pics.dmm.co.jp" in thumb_url and "ps.jpg" in thumb_url:
                cover_url = thumb_url.replace("ps.jpg", "pl.jpg")
                _store_basic(cover_url)
                return cover_url

            _store_basic(thumb_url)
            return thumb_url
        
        # 检查本地是否有实际存在的封面图片
        local_cover_path = f"buspic/covers/{movie_id}.jpg"
        if os.path.exists(local_cover_path):
            return f"/images/covers/{movie_id}.jpg"
            
        # 如果所有尝试都失败，记录一条错误并返回默认图片路径
        logging.warning(f"无法获取电影 {movie_id} 的图片URL，将使用默认封面图")
        return default_image
    
    except Exception as e:
        logging.error(f"获取电影图片URL失败: {str(e)}")
        # 出现异常时返回默认图片路径
        return "/static/images/no-cover.jpg"

# STRM Library routes
# Initialize STRM library
strm_lib = StrmLibrary(db)

# Initialize Jellyfin library
jellyfin_lib = JellyfinLibrary(db_file=DB_FILE)

@app.route('/strm')
def strm_library_route():
    """Show STRM library page"""
    return redirect(url_for('strm_library'))

@app.route('/strm/search')
def strm_library_search():
    """Search STRM library"""
    query = request.args.get('query', '')
    category = request.args.get('category', '')
    page = request.args.get('page', '1')
    sort_by = request.args.get('sort_by', 'added_time')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Convert page to int and handle errors
    try:
        page = int(page)
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    
    # Items per page
    per_page = 24
    offset = (page - 1) * per_page
    
    # Get search results with sorting applied at database level
    strm_files = db.search_strm_files(
        query, 
        category=category if category else None, 
        limit=per_page, 
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    # Get all categories for the selector
    categories = db.get_strm_categories()
    
    # Calculate pagination
    if category:
        total_count = len(db.search_strm_files(query, category=category))
    else:
        total_count = len(db.search_strm_files(query))
    
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
    
    # Generate page numbers
    max_visible_pages = 10
    if total_pages <= max_visible_pages:
        pages = list(range(1, total_pages + 1))
    else:
        # Show pages around current page
        pages = list(range(
            max(1, min(page - max_visible_pages // 2, total_pages - max_visible_pages + 1)),
            min(total_pages + 1, max(page + max_visible_pages // 2 + 1, max_visible_pages + 1))
        ))
    
    # Pagination info
    pagination = {
        'current_page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'next_page': min(page + 1, total_pages),
        'pages': pages
    }
    
    # Get the display text for the current sort option
    sort_display = get_sort_display(sort_by, sort_order)
    
    return render_template('strm_library.html', 
                          strm_files=strm_files, 
                          categories=categories, 
                          current_category=category,
                          pagination=pagination if total_pages > 1 else None,
                          search_query=query,
                          sort_by=sort_by,
                          sort_order=sort_order,
                          sort_display=sort_display)

@app.route('/strm/library')
def strm_library():
    """Show STRM library page"""
    category = request.args.get('category', '')
    page = request.args.get('page', '1')
    sort_by = request.args.get('sort_by', 'added_time')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Convert page to int and handle errors
    try:
        page = int(page)
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    
    # Items per page
    per_page = 24
    offset = (page - 1) * per_page
    
    # Get STRM files with sorting applied at database level
    strm_files = db.get_strm_files(
        category=category if category else None, 
        limit=per_page, 
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    # Get all categories for the selector
    categories = db.get_strm_categories()
    
    # Calculate pagination
    if category:
        total_count = len(db.get_strm_files(category=category))
    else:
        total_count = len(db.get_strm_files())
    
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
    
    # Generate page numbers
    max_visible_pages = 10
    if total_pages <= max_visible_pages:
        pages = list(range(1, total_pages + 1))
    else:
        # Show pages around current page
        pages = list(range(
            max(1, min(page - max_visible_pages // 2, total_pages - max_visible_pages + 1)),
            min(total_pages + 1, max(page + max_visible_pages // 2 + 1, max_visible_pages + 1))
        ))
    
    # Pagination info
    pagination = {
        'current_page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'next_page': min(page + 1, total_pages),
        'pages': pages
    }
    
    # Get the display text for the current sort option
    sort_display = get_sort_display(sort_by, sort_order)
    
    return render_template('strm_library.html', 
                          strm_files=strm_files, 
                          categories=categories, 
                          current_category=category,
                          pagination=pagination if total_pages > 1 else None,
                          sort_by=sort_by,
                          sort_order=sort_order,
                          sort_display=sort_display)

def get_sort_display(sort_by, sort_order):
    """Get a human-readable display string for the current sort options"""
    sort_by_text = {
        'added_time': 'Added Time',
        'date_added': 'Added Time',
        'video_id': 'Video ID',
        'title': 'Title',
        'date': 'Release Date'
    }.get(sort_by, 'Sort')
    
    sort_order_text = {
        'asc': '↑',
        'desc': '↓',
        'random': '⤮'
    }.get(sort_order, '')
    
    return f"{sort_by_text} {sort_order_text}"

@app.route('/strm/player/<int:file_id>')
def strm_player(file_id):
    """Show STRM player page"""
    # Get STRM file info
    strm_file = db.get_strm_file(file_id)
    if not strm_file:
        return render_template('error.html', 
                              error_title="File Not Found", 
                              error_message=f"Could not find STRM file with ID {file_id}"), 404
    
    # Get stream URL
    strm_url = strm_lib.get_strm_play_url(file_id)
    if not strm_url:
        return render_template('error.html', 
                              error_title="Stream Error", 
                              error_message="Could not get stream URL"), 500
    
    # Add file_path for template
    strm_file['file_path'] = strm_file.get('filepath', '')
    
    # Determine source type based on URL or extension
    source_type = 'application/x-mpegURL'  # Default to HLS
    url_lower = strm_url.lower()
    if url_lower.endswith('.mp4'):
        source_type = 'video/mp4'
    elif url_lower.endswith('.mkv'):
        source_type = 'video/x-matroska'
    elif url_lower.endswith('.webm'):
        source_type = 'video/webm'
    elif url_lower.endswith('.m3u8'):
        source_type = 'application/x-mpegURL'
    
    # Get referring page for the back button
    referrer = request.referrer
    
    # 更新播放计数
    db.update_strm_play_count(file_id)
    
    return render_template('strm_player_new.html', 
                          strm_file=strm_file, 
                          strm_url=strm_url,
                          source_type=source_type,
                          referrer=referrer)

@app.route('/strm/add', methods=['POST'])
def add_strm_file():
    """Add a new STRM file"""
    url = request.form.get('url', '')
    title = request.form.get('title', '')
    category = request.form.get('category', 'movies')
    thumbnail = request.form.get('thumbnail', '')
    description = request.form.get('description', '')
    
    if not url:
        return redirect(url_for('strm_library'))
    
    # Create STRM file
    result = strm_lib.import_strm_url(url, title, category, thumbnail, description)
    
    # Redirect to library page
    if result:
        return redirect(url_for('strm_library', category=category))
    else:
        return render_template('error.html', 
                              error_title="STRM Creation Failed", 
                              error_message="Failed to create STRM file"), 500

@app.route('/strm/delete/<int:file_id>', methods=['POST'])
def delete_strm_file(file_id):
    """Delete a STRM file"""
    # Get STRM file info for category (for redirect)
    strm_file = db.get_strm_file(file_id)
    category = strm_file.get('category', '') if strm_file else ''
    
    # Delete the file
    result = strm_lib.delete_strm_file(file_id)
    
    # Redirect to library page
    if result:
        return redirect(url_for('strm_library', category=category))
    else:
        return render_template('error.html', 
                              error_title="Deletion Failed", 
                              error_message=f"Failed to delete STRM file with ID {file_id}"), 500

@app.route('/strm/scan', methods=['POST'])
def scan_strm_directory():
    """Scan directory for STRM files"""
    directory = request.form.get('directory', '')
    
    # Scan directory
    count = strm_lib.scan_directory(directory if directory else None)
    
    # Redirect to library page
    return redirect(url_for('strm_library'))

@app.route('/api/proxy/image')
def proxy_image():
    """代理图片请求，解决CORS问题"""
    image_url = request.args.get('url')
    
    if not image_url:
        return jsonify({"error": "Missing URL parameter"}), 400
        
    try:
        # 解码URL
        decoded_url = urllib.parse.unquote(image_url)
        
        # 设置请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": urllib.parse.urlparse(decoded_url).netloc
        }
        
        # 发送请求
        response = requests.get(
            decoded_url,
            headers=headers,
            stream=True,
            timeout=10,
            verify=False
        )
        
        # 检查响应状态
        if response.status_code != 200:
            logging.error(f"代理图片请求失败: HTTP {response.status_code}")
            return send_from_directory('static/img', 'no_image.jpg')
            
        # 获取内容类型
        content_type = response.headers.get("Content-Type", "image/jpeg")
        
        # 创建响应
        proxy_response = Response(
            stream_with_context(response.iter_content(chunk_size=1024)),
            status=response.status_code
        )
        
        # 设置内容类型
        proxy_response.headers["Content-Type"] = content_type
        
        # 设置CORS头
        proxy_response.headers["Access-Control-Allow-Origin"] = "*"
        proxy_response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        proxy_response.headers["Access-Control-Allow-Headers"] = "Origin, X-Requested-With, Content-Type, Accept"
        
        # 复制其他重要的响应头
        for header in ["Content-Length", "Cache-Control", "Etag"]:
            if header in response.headers:
                proxy_response.headers[header] = response.headers[header]
                
        return proxy_response
        
    except Exception as e:
        logging.error(f"代理图片失败: {str(e)}")
        return send_from_directory('static/img', 'no_image.jpg')

@app.route('/strm/delete_category/<category>', methods=['POST'])
def delete_strm_category(category):
    """删除某个分类的所有STRM记录"""
    try:
        # 获取分类下的所有文件
        files = db.get_strm_files(category=category)
        count = 0
        
        # 逐个删除文件
        for file in files:
            if strm_lib.delete_strm_file(file['id']):
                count += 1
                
        logging.info(f"已删除分类 '{category}' 中的 {count} 个STRM文件")
        
        return redirect(url_for('strm_library'))
    except Exception as e:
        logging.error(f"删除分类STRM文件失败: {str(e)}")
        return render_template('error.html', 
                              error_title="Deletion Failed", 
                              error_message=f"Failed to delete STRM files in category {category}: {str(e)}"), 500

@app.route('/strm/delete_all', methods=['POST'])
def delete_all_strm_files():
    """删除所有STRM记录"""
    try:
        # 获取所有文件
        files = db.get_strm_files()
        count = 0
        
        # 逐个删除文件
        for file in files:
            if strm_lib.delete_strm_file(file['id']):
                count += 1
                
        logging.info(f"已删除所有分类中的 {count} 个STRM文件")
        
        return redirect(url_for('strm_library'))
    except Exception as e:
        logging.error(f"删除所有STRM文件失败: {str(e)}")
        return render_template('error.html', 
                              error_title="Deletion Failed", 
                              error_message=f"Failed to delete all STRM files: {str(e)}"), 500

@app.route('/strm/scan_category/<category>', methods=['POST'])
def scan_strm_category(category):
    """扫描特定分类目录下的STRM文件"""
    try:
        # 构建分类目录路径
        category_dir = os.path.join(strm_lib.strm_dir, category)
        
        # 确保目录存在
        if not os.path.exists(category_dir):
            os.makedirs(category_dir, exist_ok=True)
            
        # 扫描目录
        count = strm_lib.scan_directory(category_dir)
        
        logging.info(f"已扫描分类 '{category}' 目录，添加了 {count} 个STRM文件")
        
        return redirect(url_for('strm_library', category=category))
    except Exception as e:
        logging.error(f"扫描分类目录失败: {str(e)}")
        return render_template('error.html', 
                              error_title="Scan Failed", 
                              error_message=f"Failed to scan category directory {category}: {str(e)}"), 500

@app.route('/player/<int:file_id>')
def player(file_id):
    strm_file = StrmFile.query.get_or_404(file_id)
    strm_url = strm_file.get_play_url()
    
    # Detect file format from URL
    source_type = 'application/x-mpegURL'  # Default to HLS
    if strm_url.lower().endswith('.mp4'):
        source_type = 'video/mp4'
    elif strm_url.lower().endswith('.mkv'):
        source_type = 'video/x-matroska'
    elif strm_url.lower().endswith('.webm'):
        source_type = 'video/webm'
    elif strm_url.lower().endswith('.m3u8'):
        source_type = 'application/x-mpegURL'
    
    # Update play count
    strm_file.play_count += 1
    db.session.commit()
    
    return render_template('strm_player_new.html', 
                         strm_file=strm_file,
                         strm_url=strm_url,
                         source_type=source_type)

@app.route('/strm/video_ids', methods=['GET'])
def video_id_extractor():
    """显示视频ID提取器页面"""
    # 获取分类列表
    categories = db.get_strm_categories()
    
    # 获取默认字典
    dictionary = strm_lib.get_default_dictionary()
    
    # 如果字典为空，使用预设值
    if not dictionary:
        # 将默认字典保存到文件
        with open("config/filter_dictionary.txt", 'w', encoding='utf-8') as f:
            for line in [
                # 常见前缀/后缀
                "[Thz.la]", "720p", "720P", "1080p", "1080P", "FHD", "[FHD]",
                # 演员前缀
                "1pondo-", "1Pon", "1Pondo", "1pon", "1pondo", "Carib", "carib", 
                "-pacopacomama", "]Caribbean", "Caribbean", "paco-", "paco_",
                # 网站标识
                "YA88.CC", "dioguitar23", "avav77.xyz", "Myxav.Pw",
                "hjd2048", "fun2048", "Vol", "_hd_", "_hd", "QJ530", 
                "boy999", "play999", "av9", "xv9",
                # 分辨率标识
                "00Kbps", "000Kbps", "-4K", "-4k", "PP168", "chd1080", "-3D"
            ]:
                f.write(f"{line}\n")
        
        # 重新读取字典
        dictionary = strm_lib.get_default_dictionary()
    
    return render_template('video_id_extractor.html', 
                          categories=categories, 
                          dictionary=dictionary)

@app.route('/strm/save_dictionary', methods=['POST'])
def save_video_id_dictionary():
    """保存视频ID提取器的过滤字典"""
    dictionary_text = request.form.get('dictionary', '')
    dictionary_list = [line.strip() for line in dictionary_text.split('\n') if line.strip()]
    
    # 保存到文件
    if strm_lib.update_default_dictionary(dictionary_list):
        flash("过滤字典保存成功")
    else:
        flash("过滤字典保存失败", "error")
    
    # 重定向回提取器页面
    return redirect(url_for('video_id_extractor'))

@app.route('/strm/extract_ids', methods=['POST'])
def extract_video_ids():
    """执行视频ID提取"""
    category = request.form.get('category', '')
    preview_only = request.form.get('preview_only', '1') == '1'
    
    # 获取字典
    dictionary = strm_lib.get_default_dictionary()
    
    # 执行提取
    if preview_only:
        # 预览模式，不修改数据库
        updated_count, results = 0, []
        
        # 获取STRM文件
        strm_files = db.get_strm_files(category=category if category else None)
        
        if strm_files:
            # 导入模块
            try:
                from modules.video_id_matcher import VideoIDMatcher
                matcher = VideoIDMatcher()
                
                # 加载字典
                if dictionary:
                    matcher.load_dictionary_from_json(dictionary)
                
                # 处理STRM文件
                processed_files = matcher.process_strm_files(strm_files)
                
                # 准备预览结果
                results = []
                for file in processed_files:
                    file_id = file.get('id')
                    video_id = file.get('video_id')
                    original_title = file.get('title', '')
                    
                    # 预览标题更新
                    updated_title = matcher.update_strm_title(file, video_id)
                    
                    results.append({
                        'id': file_id,
                        'video_id': video_id,
                        'title': updated_title,
                        'original_title': original_title
                    })
            except ImportError:
                # 如果模块导入失败，直接使用strm_lib的方法
                logging.warning("无法导入VideoIDMatcher模块，使用STRM库的提取方法")
                _, results = strm_lib.extract_video_ids(
                    category=category if category else None,
                    dictionary=dictionary
                )
    else:
        # 执行实际更新
        updated_count, results = strm_lib.extract_video_ids(
            category=category if category else None,
            dictionary=dictionary
        )
    
    # 渲染结果页面
    categories = db.get_strm_categories()
    return render_template('video_id_extractor.html', 
                          categories=categories, 
                          dictionary=dictionary,
                          results=results,
                          updated_count=updated_count,
                          preview_only=preview_only,
                          selected_category=category)

@app.route('/strm/find_video/<video_id>')
def find_strm_file(video_id):
    """根据视频ID查找并重定向到STRM播放器页面"""
    try:
        # 确保数据库中存在video_id列
        db.add_video_id_column_if_not_exists()
        
        # 查询数据库中匹配的STRM文件
        matching_files = []
        strm_files = db.get_strm_files()
        
        for file in strm_files:
            if file.get('video_id') == video_id:
                matching_files.append(file)
        
        # 如果找到，重定向到播放器页面
        if matching_files:
            strm_file = matching_files[0]
            return redirect(url_for('strm_player', file_id=strm_file['id']))
        
        # 如果未找到，显示错误页面
        flash(f"找不到对应视频ID为 {video_id} 的STRM文件。", "warning")
        return render_template('error.html', 
                              error_title="STRM文件未找到", 
                              error_message=f"无法找到视频ID为 {video_id} 的STRM文件，请确保您已将此影片添加到STRM库。")
    except Exception as e:
        logging.error(f"查找STRM文件时出错: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return render_template('error.html', 
                              error_title="查找错误", 
                              error_message=f"查找STRM文件时出错: {str(e)}")

@app.route('/strm/import', methods=['POST'])
def import_strm_file():
    """Import a STRM file"""
    if 'strm_file' not in request.files:
        return redirect(url_for('strm_library'))
        
    strm_file = request.files['strm_file']
    if strm_file.filename == '':
        return redirect(url_for('strm_library'))
    
    # Get form data
    title = request.form.get('title', '')
    category = request.form.get('category', 'movies')
    
    # Read file content (URL)
    url = strm_file.read().decode('utf-8').strip()
    
    if not url:
        return render_template('error.html', 
                              error_title="Import Failed", 
                              error_message="The STRM file appears to be empty"), 400
    
    # Use the filename as title if no title provided
    if not title:
        title = os.path.splitext(strm_file.filename)[0]
    
    # Create STRM file
    result = strm_lib.import_strm_url(url, title, category)
    
    # Redirect to library page
    if result:
        return redirect(url_for('strm_library', category=category))
    else:
        return render_template('error.html', 
                              error_title="STRM Import Failed", 
                              error_message="Failed to import STRM file"), 500

@app.route('/strm/sync_movie_info', methods=['POST'])
def sync_strm_movie_info():
    """Sync movie information for STRM files"""
    try:
        # 获取具有视频ID的STRM文件
        from strm_library import StrmLibrary
        strm_lib = StrmLibrary(db)
        result = strm_lib.sync_strm_movie_info()
        
        # 显示结果
        if result["success"] > 0:
            flash(f"成功获取了 {result['success']} 个文件的影片详情", "success")
        
        if result["failed"] > 0:
            flash(f"{result['failed']} 个文件获取影片详情失败", "warning")
            
        return redirect(url_for('strm_library'))
    except Exception as e:
        logging.error(f"同步STRM影片信息失败: {str(e)}")
        return render_template('error.html', 
                              error_title="同步失败", 
                              error_message=f"同步STRM影片信息失败: {str(e)}"), 500

@app.route('/strm/update/<int:file_id>', methods=['POST'])
def update_strm_file(file_id):
    """强制更新单个STRM文件的元数据"""
    try:
        # 获取STRM文件信息
        strm_file = db.get_strm_file(file_id)
        if not strm_file:
            flash("STRM文件不存在")
            return redirect(url_for('strm_library'))
        
        # 获取文件的video_id
        video_id = strm_file.get('video_id')
        if not video_id:
            flash("STRM文件没有关联的视频ID，无法更新")
            return redirect(url_for('strm_library'))
        
        # 获取电影信息
        movie_data = db.get_movie(video_id)
        if not movie_data:
            flash(f"无法找到影片ID为 {video_id} 的信息")
            return redirect(url_for('strm_library'))
        
        # 准备演员数据 (JSON格式)
        actors_data = []
        for actor in movie_data.get("actors", []):
            actors_data.append({
                "id": actor.get("id", ""),
                "name": actor.get("name", ""),
                "image_url": actor.get("image_url", "")
            })
        
        # 将演员数据序列化为JSON字符串
        actors_json = json.dumps(actors_data)
        
        # 获取封面图片URL
        cover_image = movie_data.get("image_url", "")
        
        # 获取电影标题和发布日期
        movie_title = movie_data.get("title", "")
        movie_date = movie_data.get("date", "")
        
        # 更新元数据
        db.update_strm_metadata(
            file_id=file_id,
            video_id=video_id,
            cover_image=cover_image,
            actors=actors_json
        )
        
        # 更新标题和日期
        db.update_strm_movie_info(
            file_id=file_id,
            title=movie_title,
            date=movie_date
        )
        
        # 显示成功消息
        flash(f"成功更新了STRM文件 {movie_title} 的元数据")
        
        # 获取分类用于重定向
        category = strm_file.get('category', '')
        
        # 重定向回库页面
        return redirect(url_for('strm_library', category=category))
    except Exception as e:
        logging.error(f"更新STRM文件元数据时出错: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return render_template('error.html', 
                              error_title="更新失败", 
                              error_message=f"更新STRM文件元数据失败: {str(e)}"), 500

@app.route('/strm/update_video_id', methods=['POST'])
def update_strm_video_id():
    """更新STRM文件的视频ID并获取新的元数据"""
    try:
        file_id = request.form.get('file_id')
        video_id = request.form.get('video_id')
        
        if not file_id or not video_id:
            flash('文件ID和视频ID都必须提供', 'danger')
            return redirect(url_for('strm_library'))
        
        # 转换为整数
        file_id = int(file_id)
        
        # 获取数据库连接
        db = JavbusDatabase()
        
        # 获取STRM文件
        strm_file = db.get_strm_file(file_id)
        if not strm_file:
            flash('找不到指定的STRM文件', 'danger')
            return redirect(url_for('strm_library'))
        
        # 更新视频ID
        db.update_strm_video_id(file_id, video_id)
        
        # 获取电影信息
        try:
            # 使用get_movie_data函数获取电影信息
            movie_data = get_movie_data(video_id)
            
            if movie_data:
                # 格式化电影数据
                formatted_data = format_movie_data(movie_data)
                
                # 保存电影信息到数据库
                db.save_movie(formatted_data)
                
                # 下载封面图片
                cover_url = formatted_data.get('cover')
                if cover_url:
                    cover_path = os.path.join(config['image_directory'], 'covers', f"{video_id}.jpg")
                    download_image(cover_url, cover_path)
                
                # 更新STRM文件的元数据
                update_data = {
                    'title': formatted_data.get('title', strm_file['title']),
                    'date': formatted_data.get('date'),
                    'actors': json.dumps(formatted_data.get('actresses', [])),
                    'description': formatted_data.get('description', '')
                }
                
                # 更新STRM元数据
                db.update_strm_movie_info(file_id, update_data.get('title'), update_data.get('date'))
                
                # 更新带演员信息的元数据
                db.update_strm_metadata(file_id, 
                                       video_id=video_id, 
                                       cover_image=formatted_data.get('cover', ''), 
                                       actors=update_data.get('actors'))
                
                flash(f'成功更新视频ID并获取元数据: {video_id}', 'success')
            else:
                flash(f'无法获取视频ID为 {video_id} 的元数据', 'warning')
        except Exception as e:
            app.logger.error(f"获取电影信息出错: {str(e)}")
            flash(f'已更新视频ID，但获取元数据失败: {str(e)}', 'warning')
        
        # 从引用URL中获取参数，而不是request.args
        # 使用refer获取来源页面
        referrer = request.referrer
        
        # 默认重定向到库主页
        redirect_url = url_for('strm_library')
        
        # 获取原来的分类和查询参数
        category = strm_file.get('category', '')
        
        if referrer and 'strm_library_search' in referrer:
            # 是搜索页面
            return redirect(url_for('strm_library_search', category=category))
        else:
            # 是普通库页面
            return redirect(url_for('strm_library', category=category))
            
    except Exception as e:
        app.logger.error(f"更新STRM视频ID时出错: {str(e)}")
        app.logger.error(traceback.format_exc())
        flash(f'更新视频ID时出错: {str(e)}', 'danger')
        return redirect(url_for('strm_library'))

# 115云盘库路由
# 初始化115云盘库
cloud115_lib = Cloud115Library(db)

@app.route('/cloud115')
def cloud115_library_route():
    """Show 115 cloud library page"""
    return redirect(url_for('cloud115_library'))

@app.route('/cloud115/offline_downloads')
def cloud115_offline_downloads():
    """115离线下载管理页面"""
    return render_template('cloud115_offline_downloads.html')

@app.route('/cloud115/search')
def cloud115_library_search():
    """Search 115 cloud library"""
    query = request.args.get('query', '')
    category = request.args.get('category', '')
    page = request.args.get('page', '1')
    sort_by = request.args.get('sort_by', 'added_time')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Convert page to int and handle errors
    try:
        page = int(page)
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    
    # Items per page
    per_page = 24
    
    # Step 1: Get all records from both tables
    # Get cloud115 files matching search query
    cloud115_files = db.search_cloud115_files(
        query, 
        category=category if category else None, 
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    # Add source flag to cloud115 files
    for file in cloud115_files:
        file['is_cloud115'] = True
        file['is_jellyfin'] = False
    
    # Get jellyfin movies matching search query
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create SQL query for jellyfin movies with search
    search_terms = f"%{query}%"
    if category:
        cursor.execute("""
            SELECT * FROM jelmovie 
            WHERE (title LIKE ? OR video_id LIKE ?) AND library_name = ?
        """, (search_terms, search_terms, category))
    else:
        cursor.execute("""
            SELECT * FROM jelmovie 
            WHERE title LIKE ? OR video_id LIKE ?
        """, (search_terms, search_terms))
    
    jellyfin_movies = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Add source flag to jellyfin movies
    for movie in jellyfin_movies:
        movie['is_cloud115'] = False
        movie['is_jellyfin'] = True
    
    # Step 2: Merge and deduplicate records by video_id
    all_files = []
    video_id_map = {}
    
    # Process cloud115 files first
    for file in cloud115_files:
        video_id = file.get('video_id')
        if video_id:
            if video_id not in video_id_map:
                video_id_map[video_id] = file
            else:
                # If already exists, just update the flags
                video_id_map[video_id]['is_cloud115'] = True
        else:
            # Files without video_id go directly to the list
            all_files.append(file)
    
    # Process jellyfin movies
    for movie in jellyfin_movies:
        video_id = movie.get('video_id')
        if video_id:
            if video_id in video_id_map:
                # Update existing record
                existing = video_id_map[video_id]
                existing['is_jellyfin'] = True
                existing['jellyfin_id'] = movie.get('id')
                existing['item_id'] = movie.get('item_id')
                existing['library_name'] = movie.get('library_name')
                # Don't override other fields if we already have a cloud115 record
            else:
                # New record
                video_id_map[video_id] = movie
        else:
            # Movies without video_id go directly to the list
            all_files.append(movie)
    
    # Add all deduplicated video_id records to the list
    all_files.extend(video_id_map.values())
    
    # Step 3: Sort the merged list
    if sort_by == 'added_time':
        all_files.sort(key=lambda x: x.get('date_added', 0), reverse=(sort_order == 'desc'))
    elif sort_by == 'video_id':
        all_files.sort(key=lambda x: x.get('video_id', ''), reverse=(sort_order == 'desc'))
    elif sort_by == 'title':
        all_files.sort(key=lambda x: x.get('title', ''), reverse=(sort_order == 'desc'))
    elif sort_by == 'date':
        all_files.sort(key=lambda x: x.get('date', ''), reverse=(sort_order == 'desc'))
    
    # Step 4: Randomize if requested
    if sort_order == 'random':
        import random
        random.shuffle(all_files)
    
    # Step 5: Apply pagination
    total_count = len(all_files)
    offset = (page - 1) * per_page
    cloud115_files = all_files[offset:offset + per_page]
    
    # Get all categories (combine from both sources)
    cloud115_categories = db.get_cloud115_categories()
    
    # Get jellyfin library names
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT library_name FROM jelmovie")
    jellyfin_categories = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    # Combine categories without duplicates
    categories = list(set(cloud115_categories + jellyfin_categories))
    
    # Calculate pagination
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
    
    # Generate page numbers
    max_visible_pages = 10
    if total_pages <= max_visible_pages:
        pages = list(range(1, total_pages + 1))
    else:
        # Show pages around current page
        pages = list(range(
            max(1, min(page - max_visible_pages // 2, total_pages - max_visible_pages + 1)),
            min(total_pages + 1, max(page + max_visible_pages // 2 + 1, max_visible_pages + 1))
        ))
    
    # Pagination info
    pagination = {
        'current_page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'next_page': min(page + 1, total_pages),
        'pages': pages
    }
    
    # Get the display text for the current sort option
    sort_display = get_sort_display(sort_by, sort_order)
    
    return render_template('cloud115_library.html', 
                          cloud115_files=cloud115_files, 
                          categories=categories, 
                          current_category=category,
                          pagination=pagination if total_pages > 1 else None,
                          search_query=query,
                          sort_by=sort_by,
                          sort_order=sort_order,
                          sort_display=sort_display)

@app.route('/cloud115/library')
def cloud115_library():
    """Show 115 cloud library page"""
    category = request.args.get('category', '')
    page = request.args.get('page', '1')
    sort_by = request.args.get('sort_by', 'added_time')
    sort_order = request.args.get('sort_order', 'desc')
    
    # Convert page to int and handle errors
    try:
        page = int(page)
        if page < 1:
            page = 1
    except ValueError:
        page = 1
    
    # Items per page
    per_page = 24
    
    # Step 1: Get all records from both tables
    # Get all cloud115 files 
    cloud115_files = db.get_cloud115_files(
        category=category if category else None, 
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    # Add source flag to cloud115 files
    for file in cloud115_files:
        file['is_cloud115'] = True
        file['is_jellyfin'] = False
    
    # Get all jellyfin movies
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Create SQL query for jellyfin movies
    if category:
        cursor.execute("SELECT * FROM jelmovie WHERE library_name = ?", (category,))
    else:
        cursor.execute("SELECT * FROM jelmovie")
    
    jellyfin_movies = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Add source flag to jellyfin movies
    for movie in jellyfin_movies:
        movie['is_cloud115'] = False
        movie['is_jellyfin'] = True
    
    # Step 2: Merge and deduplicate records by video_id
    all_files = []
    video_id_map = {}
    
    # Process cloud115 files first
    for file in cloud115_files:
        video_id = file.get('video_id')
        if video_id:
            if video_id not in video_id_map:
                video_id_map[video_id] = file
            else:
                # If already exists, just update the flags
                video_id_map[video_id]['is_cloud115'] = True
        else:
            # Files without video_id go directly to the list
            all_files.append(file)
    
    # Process jellyfin movies
    for movie in jellyfin_movies:
        video_id = movie.get('video_id')
        if video_id:
            if video_id in video_id_map:
                # Update existing record
                existing = video_id_map[video_id]
                existing['is_jellyfin'] = True
                existing['jellyfin_id'] = movie.get('id')
                existing['item_id'] = movie.get('item_id')
                existing['library_name'] = movie.get('library_name')
                # Don't override other fields if we already have a cloud115 record
            else:
                # New record
                video_id_map[video_id] = movie
        else:
            # Movies without video_id go directly to the list
            all_files.append(movie)
    
    # Add all deduplicated video_id records to the list
    all_files.extend(video_id_map.values())
    
    # Step 3: Sort the merged list
    if sort_by == 'added_time':
        all_files.sort(key=lambda x: x.get('date_added', 0), reverse=(sort_order == 'desc'))
    elif sort_by == 'video_id':
        all_files.sort(key=lambda x: x.get('video_id', ''), reverse=(sort_order == 'desc'))
    elif sort_by == 'title':
        all_files.sort(key=lambda x: x.get('title', ''), reverse=(sort_order == 'desc'))
    elif sort_by == 'date':
        all_files.sort(key=lambda x: x.get('date', ''), reverse=(sort_order == 'desc'))
    
    # Step 4: Randomize if requested
    if sort_order == 'random':
        import random
        random.shuffle(all_files)
    
    # Step 5: Apply pagination
    total_count = len(all_files)
    offset = (page - 1) * per_page
    cloud115_files = all_files[offset:offset + per_page]
    
    # Get all categories (combine from both sources)
    cloud115_categories = db.get_cloud115_categories()
    
    # Get jellyfin library names
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT library_name FROM jelmovie")
    jellyfin_categories = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    # Combine categories without duplicates
    categories = list(set(cloud115_categories + jellyfin_categories))
    
    # Calculate pagination
    total_pages = (total_count + per_page - 1) // per_page  # Ceiling division
    
    # Generate page numbers
    max_visible_pages = 10
    if total_pages <= max_visible_pages:
        pages = list(range(1, total_pages + 1))
    else:
        # Show pages around current page
        pages = list(range(
            max(1, min(page - max_visible_pages // 2, total_pages - max_visible_pages + 1)),
            min(total_pages + 1, max(page + max_visible_pages // 2 + 1, max_visible_pages + 1))
        ))
    
    # Pagination info
    pagination = {
        'current_page': page,
        'total_pages': total_pages,
        'has_next': page < total_pages,
        'next_page': min(page + 1, total_pages),
        'pages': pages
    }
    
    # Get the display text for the current sort option
    sort_display = get_sort_display(sort_by, sort_order)
    
    return render_template('cloud115_library.html', 
                          cloud115_files=cloud115_files, 
                          categories=categories, 
                          current_category=category,
                          pagination=pagination if total_pages > 1 else None,
                          sort_by=sort_by,
                          sort_order=sort_order,
                          sort_display=sort_display)

@app.route('/cloud115/id_extractor')
def cloud115_id_extractor_page():
    """显示115云盘视频ID提取器页面"""
    # 获取分类列表
    categories = db.get_cloud115_categories()
    
    # 获取默认字典
    dictionary = cloud115_lib.get_default_dictionary() if hasattr(cloud115_lib, 'get_default_dictionary') else []
    
    # 如果字典为空且strm_lib可用，从strm_lib获取字典
    if not dictionary and 'strm_lib' in globals():
        dictionary = strm_lib.get_default_dictionary()
    
    # 如果字典还是空的，使用预设值
    if not dictionary:
        # 将默认字典保存到文件
        os.makedirs("config", exist_ok=True)
        with open("config/filter_dictionary.txt", 'w', encoding='utf-8') as f:
            for line in [
                # 常见前缀/后缀
                "[Thz.la]", "720p", "720P", "1080p", "1080P", "FHD", "[FHD]",
                # 演员前缀
                "1pondo-", "1Pon", "1Pondo", "1pon", "1pondo", "Carib", "carib", 
                "-pacopacomama", "]Caribbean", "Caribbean", "paco-", "paco_",
                # 网站标识
                "YA88.CC", "dioguitar23", "avav77.xyz", "Myxav.Pw",
                "hjd2048", "fun2048", "Vol", "_hd_", "_hd", "QJ530", 
                "boy999", "play999", "av9", "xv9",
                # 分辨率标识
                "00Kbps", "000Kbps", "-4K", "-4k", "PP168", "chd1080", "-3D"
            ]:
                f.write(f"{line}\n")
        
        # 读取字典
        dictionary = []
        try:
            with open("config/filter_dictionary.txt", 'r', encoding='utf-8') as f:
                dictionary = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logging.error(f"读取云盘过滤字典出错: {str(e)}")
    
    return render_template('115_id_extractor.html', 
                          categories=categories, 
                          dictionary=dictionary,
                          selected_only_missing=False)

# 添加一个兼容路由，与HTML文件中使用的url_for('cloud115_id_extractor')一致
@app.route('/cloud115_id_extractor')
def cloud115_id_extractor():
    """重定向到正确的115云盘ID提取器页面"""
    return redirect(url_for('cloud115_id_extractor_page'))

@app.route('/cloud115/save_dictionary', methods=['POST'])
def save_cloud115_dictionary():
    """保存115云盘视频ID提取器的过滤字典"""
    dictionary_text = request.form.get('dictionary', '')
    dictionary_list = [line.strip() for line in dictionary_text.split('\n') if line.strip()]
    
    # 保存到文件
    try:
        os.makedirs("config", exist_ok=True)
        with open("config/filter_dictionary.txt", 'w', encoding='utf-8') as f:
            for item in dictionary_list:
                f.write(f"{item}\n")
        flash("过滤字典保存成功")
    except Exception as e:
        flash(f"过滤字典保存失败: {str(e)}", "error")
        logging.error(f"保存115云盘过滤字典出错: {str(e)}")
    
    # 重定向回提取器页面
    return redirect(url_for('cloud115_id_extractor_page'))

@app.route('/cloud115/extract_ids', methods=['POST'])
def extract_cloud115_ids():
    """执行115云盘视频ID提取"""
    category = request.form.get('category', '')
    preview_only = request.form.get('preview_only', '1') == '1'
    only_missing = request.form.get('only_missing', '0') == '1'
    
    # 获取字典
    dictionary = []
    try:
        with open("config/filter_dictionary.txt", 'r', encoding='utf-8') as f:
            dictionary = [line.strip() for line in f if line.strip()]
    except Exception as e:
        logging.error(f"读取115云盘过滤字典出错: {str(e)}")
    
    results = []
    updated_count = 0
    
    try:
        # 导入VideoIDMatcher模块
        from modules.video_id_matcher import VideoIDMatcher
        matcher = VideoIDMatcher()
        
        # 加载字典
        if dictionary:
            matcher.load_dictionary_from_json(dictionary)
            
        # 获取文件
        cloud115_files = db.get_cloud115_files(category=category if category else None)
        
        # 如果只处理没有video_id的文件
        if only_missing:
            cloud115_files = [f for f in cloud115_files if not f.get('video_id')]
            
        if not cloud115_files:
            flash("未找到任何115云盘文件。", "warning")
            # 渲染结果页面
            categories = db.get_cloud115_categories()
            return render_template('115_id_extractor.html', 
                                  categories=categories, 
                                  dictionary=dictionary,
                                  results=[],
                                  updated_count=0,
                                  preview_only=preview_only,
                                  selected_category=category,
                                  selected_only_missing=only_missing)
        
        # 处理文件
        processed_files = matcher.process_strm_files(cloud115_files)
        
        # 如果预览模式，只生成结果
        if preview_only:
            # 准备预览结果
            for file in processed_files:
                file_id = file.get('id')
                video_id = file.get('video_id')
                original_title = file.get('title', '')
                
                # 预览标题更新
                updated_title = matcher.update_strm_title(file, video_id)
                
                results.append({
                    'id': file_id,
                    'video_id': video_id,
                    'title': updated_title,
                    'original_title': original_title
                })
        else:
            # 实际更新模式
            # 更新数据库
            for file in processed_files:
                file_id = file.get('id')
                video_id = file.get('video_id')
                original_title = file.get('title', '')
                
                # 获取更新后的标题
                updated_title = matcher.update_strm_title(file, video_id)
                
                # 更新数据库
                if db.update_cloud115_video_id(file_id, video_id, updated_title):
                    updated_count += 1
                    
                    # 尝试获取影片信息
                    movie_data = get_movie_data(video_id)
                    if movie_data:
                        # 更新文件的封面和演员信息
                        actors = [star.get('name', '') for star in movie_data.get('stars', [])]
                        db.update_cloud115_metadata(
                            file_id,
                            cover_image=movie_data.get('img', ''),
                            actors=actors
                        )
                
                results.append({
                    'id': file_id,
                    'video_id': video_id,
                    'title': updated_title,
                    'original_title': original_title
                })
            
            flash(f"已成功更新 {updated_count} 个文件的影片ID。", "success")
    except Exception as e:
        logging.error(f"提取115云盘文件影片ID错误: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        flash(f"提取视频ID时出错: {str(e)}", "error")
    
    # 渲染结果页面
    categories = db.get_cloud115_categories()
    
    return render_template('115_id_extractor.html', 
                          categories=categories, 
                          dictionary=dictionary,
                          results=results,
                          updated_count=updated_count,
                          preview_only=preview_only,
                          selected_category=category,
                          selected_only_missing=only_missing)

@app.route('/cloud115/player/<int:file_id>', methods=['GET'])
def cloud115_player(file_id):
    """115云盘视频播放页面"""
    try:
        from urllib.parse import urlparse
        t_cfg = CURRENT_CONFIG.get("transcription") or {}
        base = (t_cfg.get("api_base_url") or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        parsed = urlparse(base) if base else None
        transcription_ws_host = parsed.hostname if parsed else ""
        transcription_ws_port = str(parsed.port) if parsed and parsed.port else "8001"
        # 获取文件信息
        file_info = db.get_cloud115_file(file_id)
        if not file_info:
            flash('找不到指定的视频文件', 'danger')
            return redirect(url_for('cloud115_library'))
        
        # 更新播放次数
        db.update_cloud115_play_count(file_id)
        relative_path = resolve_cloud115_relative_path(file_info)
        if relative_path:
            file_info['filepath'] = relative_path
        alist_available = is_alist_configured()
        pickcode = _extract_pickcode_from_file_info(file_info)

        initial_mode = 'alist' if alist_available else ('direct' if pickcode else 'cloud115')
        
        # 获取关联的电影详细信息
        movie_detail = None
        video_id = file_info.get('video_id')
        app.logger.info(f"云盘文件ID={file_id}, video_id={video_id}")
        
        if video_id:
            # 直接查询数据库获取完整记录
            try:
                import json
                db.ensure_connection()
                db.local.cursor.execute('''
                    SELECT id, cover, date, data 
                    FROM movies 
                    WHERE id = ?
                ''', (video_id,))
                result = db.local.cursor.fetchone()
                
                if result:
                    movie_detail = dict(result)
                    app.logger.info(f"找到电影记录: id={movie_detail.get('id')}, cover={movie_detail.get('cover')[:50] if movie_detail.get('cover') else 'None'}, date={movie_detail.get('date')}")
                    
                    # 解析 data 字段中的 JSON 数据
                    if movie_detail.get('data'):
                        try:
                            movie_data = json.loads(movie_detail['data']) if isinstance(movie_detail['data'], str) else movie_detail['data']
                            movie_detail['parsed_data'] = movie_data
                            app.logger.info(f"解析电影数据成功: stars={len(movie_data.get('stars', []))}, genres={len(movie_data.get('genres', []))}")
                        except Exception as e:
                            app.logger.warning(f"解析电影数据失败: {e}")
                else:
                    app.logger.warning(f"未找到video_id={video_id}的电影记录")
            except Exception as e:
                app.logger.error(f"获取电影信息失败: {e}", exc_info=True)
        
        # 返回播放页面
        return render_template(
            'cloud115_player.html',
            title=file_info.get('title', '未命名视频'),
            file_id=file_id,
            file_info=file_info,
            movie_detail=movie_detail,
            file_path=relative_path,
            alist_enabled=alist_available,
            initial_mode=initial_mode,
            play_mode='library',
            direct_pickcode=pickcode,
            direct_path=relative_path or file_info.get('filepath') or file_info.get('title'),
            fwh_ws_host=transcription_ws_host,
            fwh_ws_port=transcription_ws_port,
            fwh_model=(CURRENT_CONFIG.get("transcription") or {}).get("model"),
            fwh_language=(CURRENT_CONFIG.get("transcription") or {}).get("language"),
            fwh_chunk_secs=(CURRENT_CONFIG.get("transcription") or {}).get("chunk_secs", 4.0),
            fwh_overlap_secs=(CURRENT_CONFIG.get("transcription") or {}).get("overlap_secs", 0.7),
            fwh_prefix_chars=(CURRENT_CONFIG.get("transcription") or {}).get("prefix_chars", 0),
            fwh_segmenter=(CURRENT_CONFIG.get("transcription") or {}).get("segmenter", "vad"),
            fwh_vad_max_window=(CURRENT_CONFIG.get("transcription") or {}).get("vad_max_window_secs", 15.0),
            fwh_vad_overlap=(CURRENT_CONFIG.get("transcription") or {}).get("vad_overlap_secs", 0.35),
            fwh_vad_min_silence=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_silence_secs", 0.4),
            fwh_vad_min_speech=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_speech_secs", 0.6),
            fwh_vad_frame=(CURRENT_CONFIG.get("transcription") or {}).get("vad_frame_secs", 0.03),
            fwh_vad_energy=(CURRENT_CONFIG.get("transcription") or {}).get("vad_energy_threshold", 0.001),
        )
    except Exception as e:
        app.logger.error(f"Error rendering 115 player page: {str(e)}", exc_info=True)
        flash(f'加载播放器失败: {str(e)}', 'danger')
        return redirect(url_for('cloud115_library'))


@app.route('/cloud115/player/direct', methods=['GET'])
def cloud115_player_direct():
    """115网盘直接播放页面（浏览器模式）"""
    from urllib.parse import urlparse
    t_cfg = CURRENT_CONFIG.get("transcription") or {}
    base = (t_cfg.get("api_base_url") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    parsed = urlparse(base) if base else None
    transcription_ws_host = parsed.hostname if parsed else ""
    transcription_ws_port = str(parsed.port) if parsed and parsed.port else "8001"
    pickcode = (request.args.get('pickcode') or '').strip()
    file_id_115 = (request.args.get('file_id') or '').strip()
    file_name = (request.args.get('name') or '未命名视频').strip() or '未命名视频'
    file_path = (request.args.get('path') or '').strip()

    if not pickcode:
        flash('缺少必要的播放参数', 'danger')
        return redirect(url_for('cloud115_library'))

    # 尝试从cloud115_library中查找对应的记录
    library_file = db.find_cloud115_file_by_file_id_or_pickcode(
        file_id=file_id_115 if file_id_115 else None,
        pickcode=pickcode
    )
    
    # 如果在library中找到了对应的记录，跳转到带有影片信息的播放器
    if library_file and library_file.get('id'):
        app.logger.info(f"在cloud115_library中找到记录 (id={library_file.get('id')}), 跳转到cloud115_player")
        return redirect(url_for('cloud115_player', file_id=library_file.get('id')))
    
    # 如果未找到，按照原有方式直接播放
    app.logger.info(f"未在cloud115_library中找到记录 (file_id={file_id_115}, pickcode={pickcode}), 使用直接播放模式")

    alist_available = is_alist_configured()
    initial_mode = 'direct' if pickcode else ('alist' if alist_available else 'cloud115')

    return render_template(
        'cloud115_player.html',
        title=file_name,
        file_id=None,
        file_info={'title': file_name, 'pickcode': pickcode, 'file_id': file_id_115},
        movie_detail=None,
        file_path=file_path or file_name,
        alist_enabled=alist_available,
        initial_mode=initial_mode,
        play_mode='direct',
        direct_pickcode=pickcode,
        direct_path=file_path or file_name,
        fwh_ws_host=transcription_ws_host,
        fwh_ws_port=transcription_ws_port,
        fwh_model=(CURRENT_CONFIG.get("transcription") or {}).get("model"),
        fwh_language=(CURRENT_CONFIG.get("transcription") or {}).get("language"),
        fwh_chunk_secs=(CURRENT_CONFIG.get("transcription") or {}).get("chunk_secs", 4.0),
        fwh_overlap_secs=(CURRENT_CONFIG.get("transcription") or {}).get("overlap_secs", 0.7),
        fwh_prefix_chars=(CURRENT_CONFIG.get("transcription") or {}).get("prefix_chars", 0),
        fwh_segmenter=(CURRENT_CONFIG.get("transcription") or {}).get("segmenter", "vad"),
        fwh_vad_max_window=(CURRENT_CONFIG.get("transcription") or {}).get("vad_max_window_secs", 15.0),
        fwh_vad_overlap=(CURRENT_CONFIG.get("transcription") or {}).get("vad_overlap_secs", 0.35),
        fwh_vad_min_silence=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_silence_secs", 0.4),
        fwh_vad_min_speech=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_speech_secs", 0.6),
        fwh_vad_frame=(CURRENT_CONFIG.get("transcription") or {}).get("vad_frame_secs", 0.03),
        fwh_vad_energy=(CURRENT_CONFIG.get("transcription") or {}).get("vad_energy_threshold", 0.001),
    )


@app.route('/cloud115/import_directory', methods=['POST'])
def cloud115_import_directory_deprecated():
    """已移除此方法，使用/api/cloud115/import_directory替代
    
    此方法的实现与/api/cloud115/import_directory重复，已移除以避免Flask路由冲突。
    请使用/api/cloud115/import_directory路由。
    """
    return jsonify({
        'success': False,
        'message': '此接口已弃用，请使用/api/cloud115/import_directory'
    })



@app.route('/api/update_cloud115_video_id', methods=['POST'])
def update_cloud115_video_id():
    """更新115云盘文件的影片ID和标题"""
    file_id = request.form.get('file_id')
    video_id = request.form.get('video_id', '').strip()
    title = request.form.get('title')  # 获取标题参数
    
    try:
        # 检查数据
        if not file_id:
            return jsonify({'success': False, 'message': '缺少文件ID'}), 400
        
        file_id = int(file_id)
        
        # 更新数据库
        if db.update_cloud115_video_id(file_id, video_id, title):
            # 如果提供了影片ID，尝试获取影片信息
            if video_id:
                # 获取影片数据
                movie_data = get_movie_data(video_id)
                if movie_data:
                    # 格式化电影数据
                    formatted_movie = format_movie_data(movie_data)
                    
                    # 准备演员数据 (JSON格式)
                    actors_data = []
                    for actor in formatted_movie.get("actors", []):
                        actors_data.append({
                            "id": actor.get("id", ""),
                            "name": actor.get("name", ""),
                            "image_url": actor.get("image_url", "")
                        })
                    
                    # 将演员数据序列化为JSON字符串
                    actors_json = json.dumps(actors_data)
                    
                    # 获取封面图片URL
                    cover_image = formatted_movie.get("image_url", "")
                    
                    # 获取电影标题和发布日期
                    movie_title = formatted_movie.get("title", "")
                    movie_date = formatted_movie.get("date", "")
                    
                    # 更新元数据
                    db.update_cloud115_metadata(
                        file_id,
                        video_id=video_id,
                        cover_image=cover_image,
                        actors=actors_json
                    )
                    
                    # 更新标题和日期 (如果没有提供标题，使用影片标题)
                    if not title:
                        db.update_cloud115_movie_info(
                            file_id,
                            title=movie_title,
                            date=movie_date
                        )
                    else:
                        # 如果有自定义标题，仅更新日期
                        db.update_cloud115_movie_info(
                            file_id,
                            date=movie_date
                        )
            
            return redirect(url_for('cloud115_library'))
        else:
            return render_template('error.html', 
                                 error_title="更新错误", 
                                 error_message="更新影片ID失败"), 500
    except Exception as e:
        logging.error(f"更新115云盘文件影片ID错误: {str(e)}")
        return render_template('error.html', 
                             error_title="更新错误", 
                             error_message=f"发生错误: {str(e)}"), 500

@app.route('/api/extract_cloud115_video_ids', methods=['POST'])
def extract_cloud115_video_ids():
    """从115云盘文件名中提取视频ID"""
    try:
        # 获取请求数据
        data = request.json
        category = data.get('category')
        dictionary = data.get('dictionary')
        only_missing = data.get('only_missing', False)
        
        # 调用提取方法
        result = cloud115_lib.extract_video_ids(category=category, dictionary=dictionary, only_missing=only_missing)
        
        return jsonify(result)
    except Exception as e:
        logging.error(f"提取115云盘文件影片ID错误: {str(e)}")
        return jsonify({'success': {}, 'failed': [str(e)]}), 500

@app.route('/cloud115/delete/<int:file_id>', methods=['POST'])
def delete_cloud115_file(file_id):
    """删除一个115云盘文件"""
    # 获取文件信息（用于重定向）
    file_info = db.get_cloud115_file(file_id)
    category = file_info.get('category', '') if file_info else ''
    
    # 删除文件
    result = cloud115_lib.delete_cloud115_file(file_id)
    
    # 重定向到库页面
    if result:
        return redirect(url_for('cloud115_library', category=category))
    else:
        return render_template('error.html', 
                             error_title="删除失败", 
                             error_message=f"删除ID为 {file_id} 的115云盘文件失败"), 500

@app.route('/api/clear_cloud115_files', methods=['POST'])
def clear_cloud115_files():
    """删除所有115云盘记录"""
    try:
        # 删除所有文件
        count = cloud115_lib.delete_all_files()
        logging.info(f"已删除所有115云盘记录，共 {count} 个")
        
        return redirect(url_for('cloud115_library'))
    except Exception as e:
        logging.error(f"删除所有115云盘记录失败: {str(e)}")
        return render_template('error.html', 
                              error_title="删除失败", 
                              error_message=f"删除所有115云盘记录失败: {str(e)}"), 500

@app.route('/cloud115/delete_category/<category>', methods=['POST'])
def delete_cloud115_category(category):
    """删除指定分类的115云盘记录"""
    try:
        # 删除指定分类的文件
        count = cloud115_lib.delete_files_by_category(category)
        logging.info(f"已删除分类 '{category}' 的115云盘记录，共 {count} 个")
        
        return redirect(url_for('cloud115_library'))
    except Exception as e:
        logging.error(f"删除分类 '{category}' 的115云盘记录失败: {str(e)}")
        return render_template('error.html', 
                              error_title="删除失败", 
                              error_message=f"删除分类 '{category}' 的115云盘记录失败: {str(e)}"), 500

# 115云盘登录与授权相关API
# 预设的115 APP ID
CLOUD115_CLIENT_ID = "100196935"  # 请替换为实际的115 APP ID

# 存储用户token信息的文件
CLOUD115_TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'cloud115_token.json')

def load_cloud115_token():
    """加载115云盘Token信息"""
    if os.path.exists(CLOUD115_TOKEN_FILE):
        try:
            with open(CLOUD115_TOKEN_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None
    return None

def save_cloud115_token(token_data):
    """保存115云盘Token信息"""
    os.makedirs(os.path.dirname(CLOUD115_TOKEN_FILE), exist_ok=True)
    with open(CLOUD115_TOKEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(token_data, f)

def is_cloud115_token_valid():
    """检查115云盘Token是否有效"""
    token_data = load_cloud115_token()
    if not token_data or 'access_token' not in token_data:
        app.logger.debug("No access token available")
        return False
    
    # 如果token过期时间小于当前时间，则认为token已过期
    current_time = time.time()
    if 'expires_at' in token_data and token_data['expires_at'] < current_time:
        app.logger.debug(f"Token expired. Expiry: {token_data['expires_at']}, Current: {current_time}")
        # 尝试使用refresh_token刷新
        app.logger.info("Token expired, attempting to refresh")
        refreshed = refresh_cloud115_token()
        return refreshed
    
    # 如果距离过期时间小于1小时，预先刷新token
    if 'expires_at' in token_data and token_data['expires_at'] - current_time < 3600:
        app.logger.info("Token expiring soon, refreshing proactively")
        refresh_cloud115_token()
        
    app.logger.debug("Token is valid")
    return True

def refresh_cloud115_token():
    """刷新115云盘Token"""
    try:
        # 获取当前保存的token信息
        token_data = load_cloud115_token()
        if not token_data or 'refresh_token' not in token_data:
            app.logger.warning("No refresh token available")
            return False
            
        refresh_token = token_data['refresh_token']
        app.logger.debug(f"Attempting to refresh token with refresh_token")
        
        # 构建请求参数
        params = {
            'refresh_token': refresh_token
        }
        
        # 请求刷新token
        response = requests.post(
            'https://passportapi.115.com/open/refreshToken',
            data=params,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        app.logger.debug(f"Refresh token response status: {response.status_code}")
        
        # 解析响应
        try:
            response_json = response.json()
            app.logger.debug(f"Refresh token response: {response_json}")
            
            # 如果刷新成功，更新token信息
            if response_json.get('state') == 1 and 'data' in response_json and 'access_token' in response_json['data']:
                new_token_data = response_json['data']
                
                # 更新过期时间
                if 'expires_in' in new_token_data:
                    new_token_data['expires_at'] = time.time() + new_token_data['expires_in']
                
                # 保存新token
                save_cloud115_token(new_token_data)
                app.logger.info("Token refreshed successfully")
                return True
            else:
                app.logger.warning(f"Failed to refresh token: {response_json}")
                return False
        except Exception as e:
            app.logger.error(f"Error parsing refresh token response: {str(e)}", exc_info=True)
            return False
    except Exception as e:
        app.logger.error(f"Error refreshing token: {str(e)}", exc_info=True)
        return False

@app.route('/api/cloud115/auth_device_code', methods=['POST'])
def cloud115_auth_device_code():
    """获取115云盘设备码和二维码"""
    try:
        data = request.get_json()
        app.logger.debug(f"Received auth device code request with data: {data}")
        
        if not data or 'code_challenge' not in data or 'code_challenge_method' not in data:
            app.logger.error("Missing required parameters for auth device code")
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '参数错误'
            })
        
        # 构建请求参数
        params = {
            'client_id': CLOUD115_CLIENT_ID,
            'code_challenge': data['code_challenge'],
            'code_challenge_method': data['code_challenge_method']
        }
        
        app.logger.debug(f"Sending auth device code request with params: {params}")
        
        # 请求115授权服务器获取设备码
        response = requests.post(
            'https://passportapi.115.com/open/authDeviceCode',
            data=params,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        response_json = response.json()
        app.logger.debug(f"Auth device code response: {response_json}")
        
        # 返回115授权服务器的响应
        return response_json
    except Exception as e:
        app.logger.error(f"获取115设备码错误: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取设备码失败: {str(e)}'
        })

@app.route('/api/cloud115/poll_auth_status', methods=['GET'])
def cloud115_poll_auth_status():
    """轮询115云盘授权状态"""
    try:
        # 获取请求参数
        uid = request.args.get('uid')
        time_param = request.args.get('time')
        sign = request.args.get('sign')
        
        app.logger.debug(f"Poll auth status request with params: uid={uid}, time={time_param}, sign={sign}")
        
        if not uid or not time_param or not sign:
            app.logger.error("Missing required parameters for poll auth status")
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '参数错误'
            })
        
        # 请求115授权服务器获取授权状态
        request_url = f'https://qrcodeapi.115.com/get/status/?uid={uid}&time={time_param}&sign={sign}'
        app.logger.debug(f"Requesting auth status from URL: {request_url}")
        
        response = requests.get(request_url)
        
        # 记录返回值
        response_json = response.json()
        app.logger.debug(f"Poll auth status response: {response_json}")
        
        # 返回115授权服务器的响应
        return response_json
    except Exception as e:
        app.logger.error(f"轮询115授权状态错误: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'轮询授权状态失败: {str(e)}'
        })

@app.route('/api/cloud115/device_code_to_token', methods=['POST'])
def cloud115_device_code_to_token():
    """用设备码换取115云盘访问令牌"""
    try:
        data = request.get_json()
        app.logger.debug(f"Device code to token request with data: {data}")
        
        if not data or 'uid' not in data or 'code_verifier' not in data:
            app.logger.error("Missing required parameters for device code to token")
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '参数错误'
            })
        
        # 构建请求参数 - 根据官方API文档
        params = {
            'uid': data['uid'],
            'code_verifier': data['code_verifier']
        }
        
        app.logger.debug(f"Sending device code to token request with params: {params}")
        
        # 使用官方API文档中的正确URL
        auth_url = 'https://passportapi.115.com/open/deviceCodeToToken'
        app.logger.debug(f"Requesting token from URL: {auth_url}")
        
        response = requests.post(
            auth_url,
            data=params,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        # 输出响应状态和内容
        app.logger.debug(f"Token response status: {response.status_code}")
        app.logger.debug(f"Token response text: {response.text}")
        
        # 解析响应数据
        try:
            response_json = response.json()
        except Exception as json_error:
            app.logger.error(f"Failed to parse JSON response: {str(json_error)}")
            app.logger.error(f"Response text: {response.text}")
            return jsonify({
                'state': 0,
                'code': 500,
                'message': f'解析响应失败: {str(json_error)}'
            })
            
        app.logger.debug(f"Device code to token response: {response_json}")
        
        # 如果获取token成功，保存token信息
        if response_json.get('state') == 1 and 'data' in response_json and 'access_token' in response_json['data']:
            token_data = response_json['data']
            # 计算过期时间
            if 'expires_in' in token_data:
                token_data['expires_at'] = time.time() + token_data['expires_in']
            
            # 保存token路径
            token_file_path = CLOUD115_TOKEN_FILE
            app.logger.debug(f"Saving token to file: {token_file_path}")
            
            # 确保目录存在
            token_dir = os.path.dirname(token_file_path)
            if not os.path.exists(token_dir):
                app.logger.debug(f"Creating token directory: {token_dir}")
                os.makedirs(token_dir, exist_ok=True)
            
            # 保存token
            try:
                save_cloud115_token(token_data)
                app.logger.debug("Token saved successfully")
            except Exception as save_error:
                app.logger.error(f"Error saving token: {str(save_error)}", exc_info=True)
        else:
            app.logger.warning(f"Failed to get access token: {response_json}")
        
        # 返回115授权服务器的响应
        return response_json
    except Exception as e:
        app.logger.error(f"获取115访问令牌错误: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取访问令牌失败: {str(e)}'
        })

@app.route('/api/cloud115/check_auth_status', methods=['GET'])
def cloud115_check_auth_status():
    """检查115云盘登录状态（统一检查 OpenAPI Token 和 Driver Cookie）"""
    try:
        auth_status = {
            'openapi': {'logged_in': False, 'method': 'token'},
            'driver': {'logged_in': False, 'method': 'cookie'},
            'current_mode': CURRENT_CONFIG.get('cloud115', {}).get('auth_mode', 'openapi'),
        }
        
        # 检查 OpenAPI Token
        try:
            is_valid = is_cloud115_token_valid()
            auth_status['openapi']['logged_in'] = bool(is_valid)
        except Exception as e:
            app.logger.debug(f"OpenAPI token 检查失败: {e}")
        
        # 检查 Driver Cookie
        if CLOUD115_CLIENT and CLOUD115_CLIENT.driver:
            try:
                CLOUD115_CLIENT.driver.ensure_login(force=True)
                auth_status['driver']['logged_in'] = True
            except Exception as e:
                app.logger.debug(f"Driver cookie 检查失败: {e}")
                auth_status['driver']['logged_in'] = False
        
        # 判断整体登录状态
        current_mode = auth_status['current_mode']
        if current_mode in ('driver', 'auto'):
            logged_in = auth_status['driver']['logged_in'] or auth_status['openapi']['logged_in']
            active_method = 'driver' if auth_status['driver']['logged_in'] else 'openapi'
        else:
            logged_in = auth_status['openapi']['logged_in']
            active_method = 'openapi'
        
        return jsonify({
            'state': 1,
            'code': 0,
            'message': '已登录' if logged_in else '未登录',
            'data': {
                'logged_in': logged_in,
                'active_method': active_method,
                'auth_status': auth_status
            }
        })
    except Exception as e:
        app.logger.error(f"检查115登录状态错误: {str(e)}")
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'检查登录状态失败: {str(e)}'
        })

@app.route('/api/cloud115/update_cookie', methods=['POST'])
def cloud115_update_cookie():
    """更新 115driver Cookie（热更新，无需重启）"""
    global CLOUD115_CLIENT, CURRENT_CONFIG
    
    try:
        data = request.get_json() or {}
        new_cookie = data.get('cookie', '').strip()
        auth_mode = data.get('auth_mode', '').strip()
        cookie_file = data.get('cookie_file')
        
        cloud115_config = CURRENT_CONFIG.setdefault('cloud115', {})
        driver_config = cloud115_config.setdefault('driver', {})
        
        if not new_cookie:
            new_cookie = driver_config.get('cookie', '').strip()
        
        if not new_cookie:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '缺少 Cookie 参数'
            })
        
        # 验证 Cookie 格式
        from modules.cloud115_client import DriverCredential, Cloud115ConfigError
        try:
            credential = DriverCredential.from_cookie(new_cookie)
        except Cloud115ConfigError as e:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': f'Cookie 格式错误: {str(e)}'
            })
        
        # 更新配置文件
        driver_config['cookie'] = new_cookie
        driver_config['enabled'] = True
        if cookie_file is not None:
            driver_config['cookie_file'] = cookie_file.strip()
        
        if auth_mode:
            cloud115_config['auth_mode'] = auth_mode
        elif cloud115_config.get('auth_mode') == 'openapi':
            cloud115_config['auth_mode'] = 'auto'
        
        # 保存到配置文件
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(CURRENT_CONFIG, f, ensure_ascii=False, indent=2)
            app.logger.info("配置文件已更新")
        except Exception as e:
            app.logger.error(f"保存配置文件失败: {e}")
        
        # 重新初始化客户端（热更新）
        try:
            from modules.cloud115_client import Cloud115Client
            
            CLOUD115_CLIENT = Cloud115Client(
                token_file=cloud115_config.get('token_file', 'data/cloud115_token.json'),
                driver_cookie=new_cookie,
                driver_cookie_file=driver_config.get('cookie_file', '').strip() or None,
                mode=cloud115_config.get('auth_mode', 'auto'),
                timeout=int(cloud115_config.get('request_timeout', 15)),
                driver_user_agent=driver_config.get('user_agent', 'Mozilla/5.0 115Browser/27.0.5.7'),
                driver_api_urls=driver_config.get('api_urls'),
                driver_login_check_interval=int(driver_config.get('login_check_interval', 300)),
                logger=logging.getLogger('Cloud115Client'),
            )
            
            # 测试新 Cookie 是否有效（通过实际调用API验证）
            if CLOUD115_CLIENT.driver:
                try:
                    # 直接调用115 API验证
                    import requests
                    params = {"_": str(int(time.time() * 1000))}
                    response = CLOUD115_CLIENT.driver.session.get(
                        CLOUD115_CLIENT.driver.STATUS_CHECK_URL, 
                        params=params, 
                        timeout=15
                    )
                    
                    if response.status_code in (401, 511):
                        raise Exception("Cookie已失效（HTTP 401/511）")
                    
                    payload = response.json()
                    if payload.get("state") in (False, 0):
                        raise Exception("Cookie已失效（API返回未登录状态）")
                    
                    # 再验证文件列表访问
                    api_url = CLOUD115_CLIENT.driver.file_api_urls[0] if CLOUD115_CLIENT.driver.file_api_urls else "https://webapi.115.com/files"
                    params = {
                        "aid": "1",
                        "cid": "0",
                        "limit": "1",
                        "show_dir": "1",
                        "format": "json"
                    }
                    response = CLOUD115_CLIENT.driver.session.get(api_url, params=params, timeout=15)
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("state") in (False, 0):
                        raise Exception("无法访问文件列表")
                        
                except Exception as e:
                    app.logger.error(f"Cookie验证失败: {e}")
                    raise Exception(f"Cookie无效或已过期: {str(e)}")
            
            app.logger.info("115 客户端热更新成功")
            
            return jsonify({
                'state': 1,
                'code': 0,
                'message': 'Cookie 更新成功，无需重启',
                'data': {
                    'auth_mode': cloud115_config.get('auth_mode'),
                    'driver_enabled': True
                }
            })
        except Exception as e:
            app.logger.error(f"Cookie 验证失败: {e}")
            return jsonify({
                'state': 0,
                'code': 400,
                'message': f'Cookie 无效或已过期: {str(e)}'
            })
            
    except Exception as e:
        app.logger.error(f"更新 Cookie 失败: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'更新失败: {str(e)}'
        })

@app.route('/api/cloud115/get_current_cookie', methods=['GET'])
def cloud115_get_current_cookie():
    """获取当前生效的115 driver cookie"""
    try:
        # 从配置中获取cookie
        cloud115_config = CURRENT_CONFIG.get('cloud115', {})
        driver_config = cloud115_config.get('driver', {})
        current_cookie = driver_config.get('cookie', '').strip()
        
        return jsonify({
            'state': 1,
            'code': 0,
            'message': '获取成功',
            'data': {
                'cookie': current_cookie,
                    'auth_mode': cloud115_config.get('auth_mode', 'openapi'),
                    'cookie_file': driver_config.get('cookie_file', '').strip()
            }
        })
    except Exception as e:
        app.logger.error(f"获取当前cookie失败: {str(e)}")
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取失败: {str(e)}'
        })

@app.route('/api/cloud115/verify_cookie', methods=['POST'])
def cloud115_verify_cookie():
    """验证115 cookie是否真实有效（通过调用实际API测试）"""
    try:
        data = request.get_json() or {}
        cookie = data.get('cookie', '').strip()
        
        if not cookie:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '缺少 Cookie 参数'
            })
        
        # 验证 Cookie 格式
        from modules.cloud115_client import DriverCredential, DriverClient, Cloud115ConfigError
        try:
            credential = DriverCredential.from_cookie(cookie)
        except Cloud115ConfigError as e:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': f'Cookie 格式错误: {str(e)}',
                'data': {'valid': False}
            })
        
        # 创建临时客户端测试Cookie是否真实有效
        try:
            driver_config = CURRENT_CONFIG.get('cloud115', {}).get('driver', {})
            temp_client = DriverClient(
                credential,
                timeout=15,
                user_agent=driver_config.get('user_agent', 'Mozilla/5.0 115Browser/27.0.5.7'),
                api_urls=driver_config.get('api_urls'),
                login_check_interval=300,
            )
            
            # 直接调用115 API验证cookie，不使用list_files因为它会normalize结果
            # 使用status check接口来验证登录状态
            import requests
            params = {"_": str(int(time.time() * 1000))}
            response = temp_client.session.get(
                temp_client.STATUS_CHECK_URL, 
                params=params, 
                timeout=15
            )
            
            # 检查HTTP状态码
            if response.status_code in (401, 511):
                raise Exception("Cookie已失效（HTTP 401/511）")
            
            # 检查返回的JSON
            try:
                payload = response.json()
                # 115的status接口，state为False或0表示未登录
                if payload.get("state") in (False, 0):
                    raise Exception("Cookie已失效（API返回未登录状态）")
                
                # 额外检查errno字段（某些接口用这个表示错误）
                errno = payload.get('errno')
                if errno and errno != 0:
                    error_msg = payload.get('error') or payload.get('err_msg') or f'错误码: {errno}'
                    raise Exception(f"API返回错误: {error_msg}")
                    
            except ValueError as e:
                # JSON解析失败
                raise Exception(f"API返回数据格式错误: {str(e)}")
            
            # 再次验证：尝试列出根目录
            try:
                api_url = temp_client.file_api_urls[0] if temp_client.file_api_urls else "https://webapi.115.com/files"
                params = {
                    "aid": "1",
                    "cid": "0",
                    "limit": "1",
                    "show_dir": "1",
                    "format": "json"
                }
                response = temp_client.session.get(api_url, params=params, timeout=15)
                response.raise_for_status()
                
                payload = response.json()
                # 检查原始API响应的state字段
                if payload.get("state") in (False, 0):
                    raise Exception("无法访问文件列表")
                    
            except Exception as e:
                raise Exception(f"文件列表访问失败: {str(e)}")
            
            return jsonify({
                'state': 1,
                'code': 0,
                'message': 'Cookie 有效',
                'data': {'valid': True}
            })
        except Exception as e:
            app.logger.debug(f"Cookie验证失败: {e}")
            return jsonify({
                'state': 0,
                'code': 400,
                'message': f'Cookie 无效或已过期: {str(e)}',
                'data': {'valid': False}
            })
            
    except Exception as e:
        app.logger.error(f"验证 Cookie 失败: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'验证失败: {str(e)}',
            'data': {'valid': False}
        })

@app.route('/api/cloud115/update_auth_mode', methods=['POST'])
def cloud115_update_auth_mode():
    """单独更新认证模式，不修改cookie"""
    global CLOUD115_CLIENT, CURRENT_CONFIG
    
    try:
        data = request.get_json() or {}
        auth_mode = data.get('auth_mode', '').strip()
        
        if not auth_mode or auth_mode not in ['driver', 'auto', 'openapi']:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '无效的认证模式'
            })
        
        # 更新配置
        cloud115_config = CURRENT_CONFIG.setdefault('cloud115', {})
        cloud115_config['auth_mode'] = auth_mode
        
        # 保存到配置文件
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(CURRENT_CONFIG, f, ensure_ascii=False, indent=2)
            app.logger.info(f"认证模式已更新为: {auth_mode}")
        except Exception as e:
            app.logger.error(f"保存配置文件失败: {e}")
        
        # 更新客户端模式
        if CLOUD115_CLIENT:
            CLOUD115_CLIENT.mode = auth_mode
            app.logger.info("115 客户端模式已更新")
        
        return jsonify({
            'state': 1,
            'code': 0,
            'message': f'认证模式已切换为: {auth_mode}',
            'data': {'auth_mode': auth_mode}
        })
            
    except Exception as e:
        app.logger.error(f"更新认证模式失败: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'更新失败: {str(e)}'
        })

@app.route('/api/cloud115/logout', methods=['POST'])
def cloud115_logout():
    """退出115云盘登录"""
    try:
        # 删除token文件
        if os.path.exists(CLOUD115_TOKEN_FILE):
            os.remove(CLOUD115_TOKEN_FILE)
        
        return jsonify({
            'state': 1,
            'code': 0,
            'message': '已退出登录'
        })
    except Exception as e:
        app.logger.error(f"退出115登录错误: {str(e)}")
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'退出登录失败: {str(e)}'
        })

def get_cloud115_valid_token():
    """获取有效的115云盘access token
    
    返回:
        str: 有效的access token，如果没有则返回None
    """
    # 检查token是否有效，如需要会自动刷新
    if not is_cloud115_token_valid():
        app.logger.warning("Failed to get valid 115 token")
        return None
        
    # 返回token
    token_data = load_cloud115_token()
    if token_data and 'access_token' in token_data:
        return token_data['access_token']
    
    return None
    

# Alist 辅助方法
def get_alist_config():
    """返回Alist相关配置"""
    return (CURRENT_CONFIG.get("cloud115", {}) or {}).get("alist", {}) or {}


def is_alist_configured():
    """判断Alist是否已启用且配置完整"""
    config = get_alist_config()
    if not config.get("enabled", True):
        return False
    required = [config.get("base_url"), config.get("username"), config.get("password")]
    return all(item and str(item).strip() for item in required)


def _normalize_alist_root(root_path):
    if not root_path:
        return "/"
    root = str(root_path).strip()
    if not root:
        return "/"
    root = root.replace("\\", "/")
    if not root.startswith("/"):
        root = f"/{root}"
    return root.rstrip("/") or "/"


def build_alist_path(relative_path, config=None):
    """组合Alist中的完整访问路径"""
    if not relative_path:
        return None
    if config is None:
        config = get_alist_config()
    root_path = _normalize_alist_root(config.get("root_path", "/"))
    relative = str(relative_path).replace("\\", "/").strip("/")
    if not relative:
        return root_path or "/"
    combined = posixpath.join(root_path, relative)
    if not combined.startswith("/"):
        combined = f"/{combined}"
    return combined


def clear_alist_token():
    with ALIST_CACHE_LOCK:
        ALIST_AUTH_CACHE["token"] = None
        ALIST_AUTH_CACHE["expires_at"] = 0


def get_alist_token(force_refresh=False):
    """获取Alist访问令牌"""
    if not is_alist_configured():
        return None
    config = get_alist_config()
    now = time.time()
    with ALIST_CACHE_LOCK:
        if (not force_refresh and ALIST_AUTH_CACHE.get("token") and
                now < ALIST_AUTH_CACHE.get("expires_at", 0) - 30):
            return ALIST_AUTH_CACHE["token"]
    login_url = config.get("base_url", "").rstrip('/') + '/api/auth/login'
    payload = {
        "username": config.get("username"),
        "password": config.get("password")
    }
    timeout = config.get("timeout", 30)
    try:
        response = requests.post(login_url, json=payload, timeout=timeout)
        response.raise_for_status()
        response_data = response.json()
    except Exception as exc:
        app.logger.error(f"获取Alist令牌失败: {exc}")
        return None

    token = None
    expires_in = 3600
    if isinstance(response_data, dict):
        if response_data.get('code') == 200 and response_data.get('data'):
            data = response_data['data']
            token = data.get('token') or data.get('access_token')
            if not token and isinstance(data.get('user'), dict):
                token = data['user'].get('token')
            expires_in = data.get('token_expires') or data.get('expire') or expires_in
        elif 'data' in response_data and isinstance(response_data['data'], dict):
            data = response_data['data']
            token = data.get('token')
            expires_in = data.get('token_expires') or data.get('expire') or expires_in
        else:
            message = response_data.get('message') or response_data.get('msg') or '未知错误'
            app.logger.error(f"Alist登录失败: {message}")
            return None

    if not token:
        app.logger.error("Alist登录未返回有效token")
        return None

    expires_at = now + max(int(expires_in), 300)
    with ALIST_CACHE_LOCK:
        ALIST_AUTH_CACHE["token"] = token
        ALIST_AUTH_CACHE["expires_at"] = expires_at

    return token


def get_alist_file_info(alist_path, force_refresh=False):
    """获取Alist中文件的原始URL等信息"""
    if not alist_path or not is_alist_configured():
        return {'error': 'Alist未配置或路径为空'}
    config = get_alist_config()
    cache_key = alist_path
    now = time.time()
    if not force_refresh:
        with ALIST_CACHE_LOCK:
            cached = ALIST_FILE_URL_CACHE.get(cache_key)
        if cached and now < cached.get('expires_at', 0):
            cached['error'] = None
            return cached

    token = get_alist_token(force_refresh=force_refresh)
    if not token:
        return {'error': '无法获取Alist访问令牌'}

    headers = {
        "Authorization": token
    }
    payload = {
        "path": alist_path,
        "password": "",
        "force": True
    }
    timeout = config.get("timeout", 30)
    api_url = config.get("base_url", "").rstrip('/') + '/api/fs/get'

    try:
        response = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
        if response.status_code in (401, 403):
            if force_refresh:
                clear_alist_token()
                return {'error': 'Alist认证失败，已清除缓存令牌'}
            clear_alist_token()
            return get_alist_file_info(alist_path, force_refresh=True)
        response.raise_for_status()
        response_data = response.json()
    except Exception as exc:
        app.logger.error(f"请求Alist文件信息失败: {exc}")
        return {'error': f"请求Alist文件信息失败: {exc}"}

    if response_data.get('code') != 200:
        message = response_data.get('message') or response_data.get('msg') or '未知错误'
        app.logger.error(f"Alist返回错误: {message}")
        if response_data.get('code') in (401, 403) and not force_refresh:
            clear_alist_token()
            return get_alist_file_info(alist_path, force_refresh=True)
        return {'error': message}

    data = response_data.get('data') or {}
    raw_url = data.get('raw_url') or data.get('url')
    cache_entry = {
        'raw_url': raw_url,
        'data': data,
        'error': None
    }

    expires_in = config.get('url_cache_seconds', 300)
    cache_entry['expires_at'] = now + max(int(expires_in), 60)

    with ALIST_CACHE_LOCK:
        ALIST_FILE_URL_CACHE[cache_key] = cache_entry

    return cache_entry


def clear_alist_file_cache(alist_path):
    if not alist_path:
        return
    with ALIST_CACHE_LOCK:
        if alist_path in ALIST_FILE_URL_CACHE:
            del ALIST_FILE_URL_CACHE[alist_path]


def resolve_cloud115_relative_path(file_record, force_refresh=False):
    """根据115文件记录获取或推断相对路径"""
    if not file_record:
        return None

    existing = (file_record.get('filepath') or '').strip()
    # 如果现有路径看起来不完整(只有文件名,没有目录),强制刷新
    if existing and not force_refresh:
        # 检查是否包含路径分隔符,如果没有说明只是文件名
        if '/' in existing or '\\' in existing:
            return existing.replace('\\', '/')
        else:
            # 只有文件名,需要获取完整路径
            app.logger.info(f"文件路径不完整(只有文件名): {existing}, 将获取完整路径")
            force_refresh = True

    file_id_115 = file_record.get('file_id')
    if not file_id_115:
        return existing or None

    # 优先使用 OpenAPI，如不可用则回退到 driver（cookie）
    info = None
    access_token = get_cloud115_valid_token()
    if access_token:
        try:
            resp = requests.get(
                'https://proapi.115.com/open/folder/get_info',
                params={'file_id': file_id_115},
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=15
            )
            resp.raise_for_status()
            response_data = resp.json() if resp.content else {}
            info = response_data.get('data', {}) if isinstance(response_data, dict) else {}
            if not isinstance(info, dict):
                app.logger.warning(f"115 API返回的data不是dict类型: {type(info)}")
                info = None
        except Exception as exc:
            app.logger.warning(f"OpenAPI 获取文件路径失败，将尝试使用 driver: {exc}")
            info = None

    # 如果 OpenAPI 不可用或失败，尝试使用 driver（cookie）
    if info is None:
        try:
            if CLOUD115_CLIENT is not None:
                driver_info = CLOUD115_CLIENT.get_folder_info(str(file_id_115))
                # 兼容不同返回结构，统一为 info dict
                if isinstance(driver_info, dict):
                    # driver 规范化后通常直接就是我们需要的结构
                    info = driver_info.get('data') or driver_info
            else:
                app.logger.debug("CLOUD115_CLIENT 未初始化，无法使用 driver 获取路径")
        except Exception as exc:
            app.logger.error(f"driver 获取文件路径失败: {exc}")
            info = None

    if not isinstance(info, dict):
        return existing or None

    segments = []
    paths = info.get('paths') or info.get('path') or []
    if isinstance(paths, dict):
        paths = paths.values()
    if isinstance(paths, list):
        for entry in paths:
            if not isinstance(entry, dict):
                continue
            file_id_val = entry.get('file_id') or entry.get('cid')
            name = entry.get('file_name') or entry.get('name') or ''
            name = str(name).strip()
            if not name:
                continue
            # 过滤根目录或占位符
            if str(file_id_val) in ('0', '') or name in ('根目录', '根目錄', '/', '\\'):
                continue
            if name:
                segments.append(name)
    file_name = info.get('file_name') or info.get('name') or file_record.get('title') or ''
    if file_name:
        segments.append(str(file_name).strip())

    normalized = "/".join(seg.strip('/\\') for seg in segments if seg)
    if not normalized:
        normalized = existing.replace('\\', '/') if existing else None

    if normalized and file_record.get('id'):
        try:
            db.update_cloud115_filepath(file_record['id'], normalized)
            file_record['filepath'] = normalized
        except Exception as exc:
            app.logger.warning(f"更新115文件路径失败(ID={file_record['id']}): {exc}")

    return normalized


def format_size_from_alist(data, fallback=None):
    size_value = None
    if isinstance(data, dict):
        size_value = data.get('size')
    if size_value is None:
        return fallback
    try:
        size_int = int(size_value)
    except (TypeError, ValueError):
        return fallback
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    size_float = float(size_int)
    for unit in units:
        if size_float < 1024 or unit == units[-1]:
            return f"{size_float:.2f}{unit}"
        size_float /= 1024
    return fallback


def _get_alist_info_from_pickcode(pickcode, file_path=None, force_refresh=False):
    """根据 pickcode 获取 Alist 播放信息。

    Args:
        pickcode (str): 115 文件 pickcode。
        file_path (str, optional): 相对路径，缺省则调用 115 接口获取。
        force_refresh (bool): 是否强制刷新 Alist 缓存。

    Returns:
        (dict, int): (结果字典, HTTP 状态码)
    """

    if not is_alist_configured():
        return ({'success': False, 'message': 'Alist未配置或未启用'}, 400)

    if not pickcode:
        return ({'success': False, 'message': '缺少pickcode参数'}, 400)

    relative_path = None
    file_name = None
    file_size_bytes = None

    try:
        if file_path:
            relative_path = str(file_path).replace('\\', '/').strip('/')
            file_name = relative_path.split('/')[-1] if relative_path else None
        else:
            access_token = get_cloud115_valid_token()
            if not access_token:
                return ({'success': False, 'message': '未授权，请先登录115云盘'}, 401)

            resp = requests.get(
                'https://proapi.115.com/open/file/info',
                params={'pick_code': pickcode},
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=15
            )
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
            data = payload.get('data', {}) if isinstance(payload, dict) else {}

            file_name = str(data.get('file_name') or data.get('name') or '').strip()
            relative_path = str(data.get('path') or '').replace('\\', '/').strip('/')
            if not relative_path:
                parent_name = str(data.get('parent_name') or data.get('parent_path') or '').replace('\\', '/').strip('/')
                if parent_name:
                    relative_path = f"{parent_name}/{file_name}".strip('/')
                else:
                    relative_path = file_name

            file_size_bytes = data.get('file_size') or data.get('size')
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        return ({'success': False, 'message': f'获取115文件信息失败: {exc}'}, status)
    except Exception as exc:
        return ({'success': False, 'message': f'获取115文件信息失败: {exc}'}, 500)

    relative_path = (relative_path or '').strip()
    if not relative_path:
        return ({'success': False, 'message': '无法确定文件路径'}, 500)

    relative_path = relative_path.strip('/')
    alist_path = build_alist_path(relative_path)
    if not alist_path:
        return ({'success': False, 'message': '无法构建Alist路径'}, 500)

    info = get_alist_file_info(alist_path, force_refresh=force_refresh)
    if info.get('error'):
        if not force_refresh:
            # 尝试强制刷新一次
            return _get_alist_info_from_pickcode(pickcode, relative_path, force_refresh=True)
        return ({'success': False, 'message': f"从Alist获取播放信息失败: {info.get('error')}"}, 502)

    raw_url = info.get('raw_url')
    if not raw_url:
        if not force_refresh:
            # 缺少原始地址时尝试刷新一次
            return _get_alist_info_from_pickcode(pickcode, relative_path, force_refresh=True)
        return ({'success': False, 'message': 'Alist未返回原始播放地址'}, 502)

    info_data = info.get('data') or {}

    if file_name is None or not file_name:
        file_name = info_data.get('name') or relative_path.split('/')[-1]

    try:
        file_size_bytes = int(file_size_bytes)
    except (TypeError, ValueError):
        try:
            file_size_bytes = int(info_data.get('size')) if info_data.get('size') is not None else None
        except (TypeError, ValueError):
            file_size_bytes = None

    file_size_text = format_size_from_alist(info_data, None)
    if not file_size_text and file_size_bytes:
        file_size_text = format_size_from_alist({'size': file_size_bytes}, None)

    result = {
        'success': True,
        'data': {
            'proxy_url': None,
            'raw_url': raw_url,
            'alist_path': alist_path,
            'relative_path': relative_path,
            'file_name': file_name,
            'file_size': file_size_text,
            'file_size_bytes': file_size_bytes,
            'updated_at': info_data.get('modified') or info_data.get('mtime'),
            'mode': 'alist'
        }
    }

    return (result, 200)


def _extract_pickcode_from_file_info(file_info):
    if not isinstance(file_info, dict):
        return ''

    pickcode = (
        file_info.get('pickcode')
        or file_info.get('pick_code')
        or file_info.get('pc')
    )

    if not pickcode:
        url = file_info.get('url') or ''
        if url:
            match = re.search(r'pickcode=([^&]+)', url)
            if match:
                pickcode = match.group(1)

    return (pickcode or '').strip()


def _format_download_size(size_value):
    if size_value in (None, ''):
        return (None, None)

    size_int = None
    if isinstance(size_value, (int, float)):
        size_int = int(size_value)
    else:
        try:
            size_int = int(size_value)
        except (TypeError, ValueError):
            try:
                size_int = int(float(size_value))
            except (TypeError, ValueError):
                size_int = None

    if size_int is None:
        return (None, None)

    size_text = format_size_from_alist({'size': size_int}, None)
    if not size_text:
        size_text = f"{size_int} B"

    return (size_int, size_text)


def get_cloud115_transcode_config():
    config = CURRENT_CONFIG.get("cloud115", {}).get("transcode")
    if isinstance(config, dict) and config:
        return config
    if isinstance(CLOUD115_TRANSCODE_CONFIG, dict):
        return CLOUD115_TRANSCODE_CONFIG
    return {}


def is_cloud115_transcode_enabled():
    config = get_cloud115_transcode_config()
    if not config.get("enabled"):
        return False
    if not shutil.which("ffmpeg"):
        TRANSCODE_LOGGER.warning("检测到未安装 ffmpeg，转码功能不可用")
        return False
    return True


def _build_http_headers_for_transcode(download_data, *, pickcode=None, extra_headers=None):
    headers = {}
    request_headers = download_data.get("request_headers")
    if isinstance(request_headers, dict):
        for key, value in request_headers.items():
            if value is None:
                continue
            headers[str(key)] = str(value)

    auth_cookie = download_data.get("auth_cookie")
    if auth_cookie:
        if isinstance(auth_cookie, dict):
            cookie_pairs = []
            name = auth_cookie.get("name")
            value = auth_cookie.get("value")
            if name and value:
                cookie_pairs.append(f"{name}={value}")
            extra = auth_cookie.get("extra")
            if isinstance(extra, dict):
                for k, v in extra.items():
                    if k and v:
                        cookie_pairs.append(f"{k}={v}")
            if cookie_pairs:
                headers["Cookie"] = "; ".join(cookie_pairs)
        elif isinstance(auth_cookie, list):
            cookie_pairs = []
            for item in auth_cookie:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                value = item.get("value")
                if name and value:
                    cookie_pairs.append(f"{name}={value}")
            if cookie_pairs:
                headers["Cookie"] = "; ".join(cookie_pairs)
        else:
            headers["Cookie"] = str(auth_cookie)

    if "User-Agent" not in headers or not headers.get("User-Agent"):
        headers["User-Agent"] = CLOUD115_DRIVER_USER_AGENT

    if "Referer" not in headers:
        if pickcode:
            headers["Referer"] = f"https://115.com/?ct=play&pickcode={pickcode}"
        else:
            headers["Referer"] = "https://115.com/"
    if "Origin" not in headers:
        headers["Origin"] = "https://115.com"
    if "Accept" not in headers:
        headers["Accept"] = "*/*"

    if extra_headers:
        for key, value in extra_headers.items():
            if value is None:
                continue
            headers[str(key)] = str(value)

    return headers


def _build_ffmpeg_header_string(headers):
    if not headers:
        return None
    header_lines = []
    for key, value in headers.items():
        if not value:
            continue
        header_lines.append(f"{key}: {value}")
    if not header_lines:
        return None
    return "".join(line + "\r\n" for line in header_lines)


def _probe_media_info(pickcode, download_url, headers):
    if not download_url:
        return {}

    cache_key = pickcode or hashlib.sha1(download_url.encode("utf-8", errors="ignore")).hexdigest()
    with TRANSCODE_TASKS_LOCK:
        cached = TRANSCODE_PROBE_CACHE.get(cache_key)
        if cached and (time.time() - cached.get("timestamp", 0)) < 3600:
            return cached

    if not shutil.which("ffprobe"):
        TRANSCODE_LOGGER.warning("检测到未安装 ffprobe，无法分析媒体信息")
        return {}

    header_string = _build_ffmpeg_header_string(headers)
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-loglevel",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
    ]
    if header_string:
        cmd += ["-headers", header_string]
    cmd.append(download_url)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CLOUD115_TRANSCODE_PROBE_TIMEOUT,
            check=False,
        )
    except Exception as exc:
        TRANSCODE_LOGGER.warning(f"调用 ffprobe 失败: {exc}")
        return {}

    if proc.returncode != 0:
        stderr_text = (proc.stderr or b"").decode("utf-8", errors="ignore")
        TRANSCODE_LOGGER.warning(f"ffprobe 返回错误({proc.returncode}): {stderr_text[:200]}")
        return {}

    try:
        result = json.loads((proc.stdout or b"").decode("utf-8", errors="ignore") or "{}")
    except json.JSONDecodeError:
        TRANSCODE_LOGGER.warning("解析 ffprobe 输出失败")
        return {}

    info = {
        "timestamp": time.time(),
        "video_codec": None,
        "audio_codec": None,
        "width": None,
        "height": None,
        "pix_fmt": None,
        "format": None,
        "duration": None,
        "bit_rate": None,
    }

    for stream in result.get("streams", []):
        if stream.get("codec_type") == "video":
            info["video_codec"] = (stream.get("codec_name") or "").lower()
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
            info["pix_fmt"] = stream.get("pix_fmt")
            info["profile"] = stream.get("profile")
        elif stream.get("codec_type") == "audio":
            info["audio_codec"] = (stream.get("codec_name") or "").lower()
            info["audio_channels"] = stream.get("channels")
            info["audio_sample_rate"] = stream.get("sample_rate")

    fmt = result.get("format") or {}
    if isinstance(fmt, dict):
        info["format"] = fmt.get("format_name")
        info["duration"] = fmt.get("duration")
        info["bit_rate"] = fmt.get("bit_rate")

    with TRANSCODE_TASKS_LOCK:
        TRANSCODE_PROBE_CACHE[cache_key] = info

    return info


def _cloud115_should_transcode(file_name, download_data, *, pickcode=None, file_info=None):
    config = get_cloud115_transcode_config()
    if not config.get("enabled"):
        return (False, {})

    normalized_name = (file_name or "").strip()
    extension = ""
    if normalized_name and "." in normalized_name:
        extension = normalized_name.rsplit(".", 1)[-1].lower()

    trigger_extensions = {
        str(ext).lower()
        for ext in config.get("trigger_on_extensions", [])
        if isinstance(ext, str) and ext.strip()
    }

    trigger_codecs = {
        str(codec).lower()
        for codec in config.get("trigger_on_codecs", [])
        if isinstance(codec, str) and codec.strip()
    }

    reasons = []

    if extension and extension in trigger_extensions:
        reasons.append(f"extension:{extension}")

    lowered_name = normalized_name.lower()
    if not reasons and ("hevc" in lowered_name or "h265" in lowered_name):
        if {"hevc", "h265"} & trigger_codecs:
            reasons.append("filename:hevc")

    media_info = {}
    if not reasons and trigger_codecs:
        headers = _build_http_headers_for_transcode(download_data, pickcode=pickcode)
        media_info = _probe_media_info(pickcode, download_data.get("download_url"), headers)
        video_codec = (media_info.get("video_codec") or "").lower()
        if video_codec and (
            video_codec in trigger_codecs
            or ({"hevc", "h265"} & trigger_codecs and any(token in video_codec for token in ("hevc", "h265")))
        ):
            reasons.append(f"codec:{video_codec}")

    should_transcode = bool(reasons)
    return should_transcode, {
        "reasons": reasons,
        "extension": extension,
        "media_info": media_info,
        "file_name": normalized_name,
        "file_info": file_info,
    }


def _get_active_transcode_count():
    # V2: 优先使用 V2 管理器的计数
    if TRANSCODE_V2_MANAGER is not None:
        summary = TRANSCODE_V2_MANAGER.get_status_summary()
        return summary.get("active_tasks", 0)

    # 旧系统：保持原有逻辑
    with TRANSCODE_TASKS_LOCK:
        return sum(
            1 for task in TRANSCODE_TASKS.values()
            if task.get("status") in TRANSCODE_ACTIVE_STATUSES
        )


def _generate_transcode_token(task_id):
    """为转码任务生成访问 token。"""
    # 生成一个 32 字节的随机 token
    token = secrets.token_hex(32)
    with TRANSCODE_TOKENS_LOCK:
        TRANSCODE_TOKENS[token] = {
            "task_id": task_id,
            "created_at": time.time()
        }
    TRANSCODE_LOGGER.info(f"为任务 {task_id} 生成访问 token")
    return token


def _verify_transcode_token(token):
    """验证转码流访问 token，返回 task_id 或 None。"""
    if not token:
        return None
    with TRANSCODE_TOKENS_LOCK:
        token_info = TRANSCODE_TOKENS.get(token)
        if not token_info:
            return None
        # 检查 token 是否过期（24小时）
        if time.time() - token_info.get("created_at", 0) > 86400:
            TRANSCODE_TOKENS.pop(token, None)
            return None
        return token_info.get("task_id")
    return None


def _cleanup_transcode_tokens():
    """清理过期的 token。"""
    now = time.time()
    with TRANSCODE_TOKENS_LOCK:
        expired_tokens = [
            token for token, info in TRANSCODE_TOKENS.items()
            if now - info.get("created_at", 0) > 86400
        ]
        for token in expired_tokens:
            TRANSCODE_TOKENS.pop(token, None)
        if expired_tokens:
            TRANSCODE_LOGGER.info(f"清理了 {len(expired_tokens)} 个过期 token")


def _serialize_transcode_task(task):
    """序列化转码任务信息（V2 兼容版本）"""
    if not task:
        return {}

    task_id = task.get("id")

    # V2: 使用新的 API 端点
    playlist_url = f"/api/cloud115/transcode/playlist/{task_id}"
    status_url = f"/api/cloud115/transcode/status/{task_id}"

    result = {
        "task_id": task_id,
        "status": task.get("status"),
        "reason": task.get("reason"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "ready_at": task.get("ready_at"),
        "stream_url": playlist_url,
        "playlist_url": playlist_url,  # V2 新增
        "abs_stream_url": playlist_url,  # V2 简化
        "status_url": status_url,
        "ready": task.get("status") in ("ready", "completed", "running"),  # V2 扩展
        "file_name": task.get("file_name"),
    }

    # 包含视频时长（如果已检测到）
    duration = task.get("duration")
    if duration is not None:
        result["duration"] = float(duration)

    # 包含媒体信息（如果存在）
    media_info = task.get("media_info")
    if media_info:
        result["media_info"] = media_info

    # V2: 不再需要 start_offset 偏移计算
    # 保留字段兼容性，但始终为 0
    result["start_offset"] = 0

    return result


def _parse_m3u8_duration(m3u8_path):
    """解析 m3u8 文件，计算已转码的总时长"""
    if not m3u8_path or not os.path.exists(m3u8_path):
        return 0.0
    
    try:
        total_duration = 0.0
        with open(m3u8_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # 解析 #EXTINF 行，格式: #EXTINF:duration,[title]
                if line.startswith('#EXTINF:'):
                    try:
                        # 提取时长部分（冒号后到逗号前）
                        duration_str = line.split(':')[1].split(',')[0]
                        duration = float(duration_str)
                        total_duration += duration
                    except (ValueError, IndexError):
                        continue
        return total_duration
    except Exception as exc:
        TRANSCODE_LOGGER.warning(f"解析 m3u8 文件失败 {m3u8_path}: {exc}")
        return 0.0


def _stop_transcode_task(task_id, reason="manual"):
    """停止指定的转码任务（V2 兼容版本）"""
    # V2: 优先使用 V2 管理器
    if TRANSCODE_V2_MANAGER is not None:
        v2_task = TRANSCODE_V2_MANAGER.get_task(task_id)
        if v2_task:
            return TRANSCODE_V2_MANAGER.stop_task(task_id, reason=reason)

    # 旧系统：保持原有逻辑
    with TRANSCODE_TASKS_LOCK:
        task = TRANSCODE_TASKS.get(task_id)
        if not task:
            TRANSCODE_LOGGER.warning(f"尝试停止不存在的转码任务: {task_id}")
            return False
        
        status = task.get("status")
        if status in ("error", "completed", "cancelled"):
            TRANSCODE_LOGGER.info(f"转码任务 {task_id} 已经处于终止状态: {status}")
            return True  # 已经停止
        
        TRANSCODE_LOGGER.info(f"正在停止转码任务 {task_id} (原因: {reason})")
        
        # 先更新状态为 cancelled，这样运行循环可以检测到
        task["status"] = "cancelled"
        task["updated_at"] = time.time()
        task["error"] = f"任务已停止: {reason}"
        
        process = task.get("process")
        if process:
            # 在锁外执行进程终止操作，避免长时间持有锁
            task["process"] = None
    
    # 在锁外执行进程终止操作
    if process:
        try:
            # 检查进程是否还在运行
            if process.poll() is None:
                TRANSCODE_LOGGER.info(f"正在终止转码任务 {task_id} 的 FFmpeg 进程 (PID: {process.pid})")
                # 尝试优雅停止
                process.terminate()
                try:
                    process.wait(timeout=5)
                    TRANSCODE_LOGGER.info(f"转码任务 {task_id} 的进程已优雅停止")
                except subprocess.TimeoutExpired:
                    # 如果5秒内没有停止，强制杀死
                    TRANSCODE_LOGGER.warning(f"转码任务 {task_id} 的进程在5秒内未停止，强制终止")
                    process.kill()
                    process.wait()
                    TRANSCODE_LOGGER.info(f"转码任务 {task_id} 的进程已强制终止")
            else:
                TRANSCODE_LOGGER.info(f"转码任务 {task_id} 的进程已经结束 (返回码: {process.returncode})")
        except ProcessLookupError:
            TRANSCODE_LOGGER.info(f"转码任务 {task_id} 的进程已经不存在")
        except Exception as exc:
            TRANSCODE_LOGGER.error(f"停止转码任务 {task_id} 的进程时出错: {exc}", exc_info=True)
    
    TRANSCODE_LOGGER.info(f"转码任务 {task_id} 已成功停止")
    return True


def _cleanup_transcode_tasks(force=False):
    now = time.time()
    cutoff = now - (CLOUD115_TRANSCODE_CLEANUP_MINUTES * 60)
    # 长时间未访问的运行中任务，也视为需要停止（使用配置的清理时间）
    idle_cutoff = now - (CLOUD115_TRANSCODE_CLEANUP_MINUTES * 60)

    with TRANSCODE_TASKS_LOCK:
        global TRANSCODE_LAST_CLEANUP
        if not force and (now - TRANSCODE_LAST_CLEANUP) < 120:
            return

        removal_ids = []
        stop_ids = []
        
        # 收集所有活跃任务的输出目录
        active_output_dirs = set()
        
        for task_id, task in list(TRANSCODE_TASKS.items()):
            status = task.get("status")
            last_access = task.get("last_access") or task.get("updated_at") or task.get("created_at") or now
            process = task.get("process")
            output_dir = task.get("output_dir")
            if output_dir:
                active_output_dirs.add(os.path.abspath(output_dir))

            # 检查进程是否已结束
            if process and getattr(process, "poll", None):
                ret = process.poll()
                if ret is not None:
                    task["returncode"] = ret
                    task["process"] = None
                    task["updated_at"] = now
                    if status not in ("error", "completed", "cancelled"):
                        task["status"] = "completed" if ret == 0 else "error"

            # 对于长时间未访问的运行中任务，标记为需要停止
            if status in TRANSCODE_ACTIVE_STATUSES and last_access < idle_cutoff:
                stop_ids.append(task_id)
            
            # 对于已完成/错误/取消的任务，如果超过清理时间，标记为需要删除
            if status in ("error", "completed", "cancelled"):
                if force or last_access < cutoff:
                    removal_ids.append(task_id)

        # 停止长时间未访问的运行中任务
        for task_id in stop_ids:
            _stop_transcode_task(task_id, reason="长时间未访问自动停止")
            # 停止后也标记为需要删除
            removal_ids.append(task_id)

        # 清理已停止的任务
        for task_id in removal_ids:
            task = TRANSCODE_TASKS.pop(task_id, None)
            if not task:
                continue
            key = task.get("task_key")
            if key:
                TRANSCODE_TASK_KEYS.pop(key, None)
            output_dir = task.get("output_dir")
            try:
                if output_dir and os.path.isdir(output_dir):
                    TRANSCODE_LOGGER.info(f"清理转码任务目录: {output_dir}")
                    shutil.rmtree(output_dir, ignore_errors=True)
            except Exception as exc:
                TRANSCODE_LOGGER.warning(f"清理转码目录失败 ({output_dir}): {exc}")

        TRANSCODE_LAST_CLEANUP = now

    # 清理工作目录中的孤立文件（不在任务字典中的目录）
    try:
        if os.path.isdir(TRANSCODE_WORK_DIR):
            for item in os.listdir(TRANSCODE_WORK_DIR):
                item_path = os.path.abspath(os.path.join(TRANSCODE_WORK_DIR, item))
                if os.path.isdir(item_path):
                    # 检查是否在活跃任务目录中
                    if item_path not in active_output_dirs:
                        # 检查目录的最后修改时间
                        try:
                            mtime = os.path.getmtime(item_path)
                            if now - mtime > (CLOUD115_TRANSCODE_CLEANUP_MINUTES * 60):
                                TRANSCODE_LOGGER.info(f"清理孤立转码目录: {item_path}")
                                shutil.rmtree(item_path, ignore_errors=True)
                        except Exception as exc:
                            TRANSCODE_LOGGER.warning(f"检查孤立目录失败 ({item_path}): {exc}")
    except Exception as exc:
        TRANSCODE_LOGGER.warning(f"扫描工作目录失败: {exc}")


def _get_or_create_transcode_task(pickcode, file_name, download_data, *, reason="manual", force=False):
    """获取或创建转码任务（V2 兼容版本）

    优先使用 V2 管理器，如果不可用则使用旧系统。
    """
    if not is_cloud115_transcode_enabled():
        return (False, "transcode_disabled", None)

    # V2: 优先使用 V2 管理器
    if TRANSCODE_V2_MANAGER is not None:
        download_url = download_data.get("download_url")
        media_info = download_data.get("media_info") or {}

        # 获取 115 API 提供的播放时长作为备用
        known_duration = None
        play_duration = download_data.get("play_duration") or media_info.get("play_duration")
        if play_duration:
            try:
                known_duration = float(play_duration)
                TRANSCODE_LOGGER.info(f"Using 115 API play_duration: {known_duration}s for {file_name}")
            except (ValueError, TypeError):
                pass

        # 构建请求头
        headers = _build_http_headers_for_transcode(download_data, pickcode=pickcode)
        header_string = _build_ffmpeg_header_string(headers)

        # 使用 V2 管理器创建任务
        success, message, task = TRANSCODE_V2_MANAGER.get_or_create_task(
            pickcode=pickcode,
            file_name=file_name,
            source_url=download_url,
            header_string=header_string,
            start_time=0,
            known_duration=known_duration,
        )

        if success and task:
            # 转换为旧格式以保持兼容性
            legacy_task = {
                "id": task.task_id,
                "status": task.status.value,
                "reason": reason,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "file_name": file_name,
                "pickcode": pickcode,
                "media_info": media_info,
                "duration": task.duration,
                "current_seek_time": task.current_seek_time,
            }
            return (True, "created", legacy_task)

        return (False, message or "v2_error", None)

    # 旧系统：保持原有逻辑（V2 不可用时的后备方案）
    task_key = pickcode or hashlib.sha1(
        (download_data.get("download_url") or "").encode("utf-8", errors="ignore")
    ).hexdigest()

    headers = _build_http_headers_for_transcode(download_data, pickcode=pickcode)
    header_string = _build_ffmpeg_header_string(headers)

    _cleanup_transcode_tasks()

    # 在创建转码任务前，先检测视频时长
    download_url = download_data.get("download_url")
    media_info = download_data.get("media_info") or {}
    duration = None
    
    # 如果 media_info 中已有 duration，直接使用
    if media_info.get("duration"):
        try:
            duration = float(media_info.get("duration"))
        except (TypeError, ValueError):
            duration = None
    
    # 如果没有 duration，使用 ffprobe 检测
    if duration is None and download_url:
        TRANSCODE_LOGGER.info(f"转码任务创建前检测视频时长 (pickcode={pickcode})")
        probe_info = _probe_media_info(pickcode, download_url, headers)
        if probe_info.get("duration"):
            try:
                duration = float(probe_info.get("duration"))
                TRANSCODE_LOGGER.info(f"检测到视频时长: {duration:.2f} 秒")
            except (TypeError, ValueError):
                duration = None
        # 将检测到的信息合并到 media_info
        if probe_info:
            media_info.update(probe_info)

    with TRANSCODE_TASKS_LOCK:
        existing_id = TRANSCODE_TASK_KEYS.get(task_key)
        if existing_id:
            existing_task = TRANSCODE_TASKS.get(existing_id)
            if existing_task and existing_task.get("status") not in ("error", "cancelled"):
                TRANSCODE_LOGGER.info(f"命中已存在的转码任务: key={task_key}, task_id={existing_id}, status={existing_task.get('status')}")
                return (True, "existing", existing_task)
            elif existing_task:
                TRANSCODE_TASK_KEYS.pop(task_key, None)

        active_count = sum(
            1 for task in TRANSCODE_TASKS.values()
            if task.get("status") in TRANSCODE_ACTIVE_STATUSES
        )
        if not force and active_count >= CLOUD115_TRANSCODE_MAX_TASKS:
            TRANSCODE_LOGGER.warning(f"并发限制，拒绝创建新任务: active={active_count}, limit={CLOUD115_TRANSCODE_MAX_TASKS}, pickcode={pickcode}")
            return (False, "concurrency_limit", {
                "active": active_count,
                "limit": CLOUD115_TRANSCODE_MAX_TASKS,
            })

        task_id = uuid.uuid4().hex
        output_dir = os.path.join(TRANSCODE_WORK_DIR, task_id)
        os.makedirs(output_dir, exist_ok=True)
        playlist_name = CLOUD115_TRANSCODE_PLAYLIST
        playlist_path = os.path.join(output_dir, playlist_name)
        log_path = os.path.join(output_dir, "transcode.log")

        task = {
            "id": task_id,
            "task_key": task_key,
            "status": "queued",
            "reason": reason,
            "created_at": time.time(),
            "updated_at": time.time(),
            "file_name": file_name,
            "pickcode": pickcode,
            "download_url": download_url,
            "http_headers": headers,
            "header_string": header_string,
            "output_dir": output_dir,
            "playlist_path": playlist_path,
            "playlist_filename": playlist_name,
            "segment_template": CLOUD115_TRANSCODE_SEGMENT_TEMPLATE,
            "log_path": log_path,
            "process": None,
            "ready_at": None,
            "returncode": None,
            "media_info": media_info,
            "duration": duration,  # 保存视频时长
            "current_seek_time": 0,  # 当前转码的起始时间点（秒）
        }
        TRANSCODE_TASKS[task_id] = task
        TRANSCODE_TASK_KEYS[task_key] = task_id

    thread = threading.Thread(target=_run_transcode_task, args=(task_id,), daemon=True)
    thread.start()
    return (True, "created", task)


def _run_transcode_task(task_id, start_time=0):
    """
    运行转码任务
    
    Args:
        task_id: 任务ID
        start_time: 开始转码的时间点（秒），默认为0（从头开始）
    """
    with TRANSCODE_TASKS_LOCK:
        task = TRANSCODE_TASKS.get(task_id)
        if not task:
            return
        task["status"] = "starting"
        task["updated_at"] = time.time()
        # 记录当前转码的起始时间
        task["current_seek_time"] = start_time

    headers_string = task.get("header_string")
    download_url = task.get("download_url")
    log_path = task.get("log_path")

    if not download_url:
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                task["status"] = "error"
                task["error"] = "缺少下载地址"
                task["updated_at"] = time.time()
        return

    config = get_cloud115_transcode_config()
    use_hwaccel = bool(config.get("use_hwaccel", True))
    
    # 根据 use_hwaccel 选择合适的编码器
    if use_hwaccel:
        video_encoder = config.get("video_encoder", "h264_qsv")
    else:
        video_encoder = config.get("video_encoder_sw", "libx264")
    
    audio_encoder = config.get("audio_encoder", "aac")
    video_bitrate = config.get("video_bitrate")
    maxrate = config.get("maxrate")
    bufsize = config.get("bufsize")
    audio_bitrate = config.get("audio_bitrate")
    audio_channels = config.get("audio_channels")
    audio_sample_rate = config.get("audio_sample_rate")
    segment_duration = max(2, int(config.get("segment_duration", 2) or 2))
    gop_size = int(config.get("gop_size", 120) or 120)
    hls_mode = config.get("hls_mode", "streaming")  # "streaming" 或 "vod"
    config_hls_flags = config.get("hls_flags", "")
    
    # 获取hls_list_size配置（默认值为0，不限制）
    config_hls_list_size = config.get("hls_list_size")
    hls_list_size = 0 if config_hls_list_size is None else int(config_hls_list_size or 0)
    
    # 根据HLS模式自动设置合适的flags
    # 如果config中的flags与当前模式匹配，则使用它；否则自动设置
    if hls_mode == "vod":
        # VOD模式：不应该包含omit_endlist和append_list
        # 添加temp_file标志确保m3u8实时更新（边转码边生成m3u8）
        if config_hls_flags and "omit_endlist" not in config_hls_flags and "append_list" not in config_hls_flags:
            # 用户已经设置了VOD模式的flags，检查是否包含temp_file
            if "temp_file" not in config_hls_flags:
                # 添加temp_file标志以确保实时更新
                hls_flags = config_hls_flags + ("+" if config_hls_flags else "") + "temp_file"
            else:
                hls_flags = config_hls_flags
        else:
            # 自动设置为VOD模式推荐的flags（保留所有片段用于跳转，实时更新m3u8）
            hls_flags = "temp_file"  # temp_file确保m3u8实时更新
    else:
        # Streaming模式：应该包含omit_endlist和append_list
        # 不包含delete_segments，允许保留所有片段
        if config_hls_flags and "omit_endlist" in config_hls_flags and "append_list" in config_hls_flags:
            # 用户已经设置了Streaming模式的flags，使用它
            hls_flags = config_hls_flags
        else:
            # 自动设置为Streaming模式推荐的flags（不包含delete_segments）
            hls_flags = "append_list+omit_endlist"
    preset_value = str(config.get("qsv_preset", "7")) if use_hwaccel else str(config.get("x264_preset", "medium"))
    qsv_device = config.get("qsv_device")

    ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-loglevel", config.get("loglevel", "warning")]

    if headers_string:
        ffmpeg_cmd += ["-headers", headers_string]

    # 判断是否为旧编码/容器，需要“软解 + 硬编”（禁用硬件解码，仅上传到 QSV）
    legacy_sw_decode = False
    try:
        media_info_for_detect = (task.get("media_info") or {})
        detected_vcodec = (media_info_for_detect.get("video_codec") or "").lower()
        detected_format = (media_info_for_detect.get("format") or "").lower()
        file_name_for_detect = (task.get("file_name") or "").lower()
        file_ext_for_detect = file_name_for_detect.rsplit(".", 1)[-1] if "." in file_name_for_detect else ""
        legacy_codecs = {"mpeg4", "msmpeg4v2", "msmpeg4v3", "mpeg1video"}
        legacy_containers = {"avi", "asf"}
        if (detected_vcodec and detected_vcodec in legacy_codecs) or \
           (file_ext_for_detect and file_ext_for_detect in legacy_containers) or \
           (detected_format and any(tok in detected_format for tok in legacy_containers)):
            legacy_sw_decode = True
    except Exception:
        legacy_sw_decode = False

    if use_hwaccel:
        # 初始化 QSV 设备（不一定启用硬解）
        qsv_init_method = config.get("qsv_init_method", "vaapi")  # 可选: "direct", "vaapi"
        if qsv_init_method == "vaapi":
            if qsv_device:
                ffmpeg_cmd += ["-init_hw_device", f"vaapi=va:{qsv_device}"]
            else:
                ffmpeg_cmd += ["-init_hw_device", "vaapi=va"]
            ffmpeg_cmd += ["-init_hw_device", "qsv=qsv@va"]
        else:
            if qsv_device:
                ffmpeg_cmd += ["-init_hw_device", f"qsv=hw:{qsv_device}"]
            else:
                ffmpeg_cmd += ["-init_hw_device", "qsv=hw"]
        # 对于非旧编码，启用硬件解码；旧编码仅进行软解并上传到 QSV
        if not legacy_sw_decode:
            ffmpeg_cmd += ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"]
        # 供 hwupload 过滤器使用的设备
        ffmpeg_cmd += ["-filter_hw_device", "qsv"]

    default_user_agent = task.get("http_headers", {}).get("User-Agent")
    if default_user_agent:
        ffmpeg_cmd += ["-user_agent", default_user_agent]

    # 如果指定了开始时间，使用 -ss 参数（必须在 -i 之前）
    if start_time > 0:
        TRANSCODE_LOGGER.info(f"转码任务 {task_id} 从时间点 {start_time:.2f} 秒开始")
        ffmpeg_cmd += ["-ss", str(start_time)]

    ffmpeg_cmd += ["-i", download_url]

    if use_hwaccel:
        if legacy_sw_decode:
            # 软解 + 硬编：先转 nv12 再上传到 QSV
            ffmpeg_cmd += ["-vf", "format=nv12,hwupload=extra_hw_frames=64"]
        else:
            # 硬解 + 硬编：保持硬帧链路，使用 vpp_qsv 统一为 nv12
            ffmpeg_cmd += ["-vf", "vpp_qsv=format=nv12"]

    ffmpeg_cmd += ["-c:v", video_encoder]
    if video_bitrate:
        ffmpeg_cmd += ["-b:v", str(video_bitrate)]
    if maxrate:
        ffmpeg_cmd += ["-maxrate", str(maxrate)]
    if bufsize:
        ffmpeg_cmd += ["-bufsize", str(bufsize)]
    if preset_value:
        ffmpeg_cmd += ["-preset", preset_value]
    if gop_size:
        ffmpeg_cmd += ["-g", str(gop_size)]

    ffmpeg_cmd += ["-c:a", audio_encoder]
    if audio_bitrate:
        ffmpeg_cmd += ["-b:a", str(audio_bitrate)]
    if audio_channels:
        ffmpeg_cmd += ["-ac", str(audio_channels)]
    if audio_sample_rate:
        ffmpeg_cmd += ["-ar", str(audio_sample_rate)]

    segment_template = os.path.join(task["output_dir"], task.get("segment_template") or CLOUD115_TRANSCODE_SEGMENT_TEMPLATE)
    ffmpeg_cmd += [
        "-f", "hls",
        "-hls_time", str(segment_duration),
        "-hls_segment_filename", segment_template,
    ]
    
    # 只有当hls_flags不为空时才添加-hls_flags参数
    if hls_flags:
        ffmpeg_cmd += ["-hls_flags", hls_flags]
    
    # 根据HLS模式设置不同的参数
    if hls_mode == "vod":
        # VOD模式：使用hls_playlist_type vod
        # 这样m3u8会在第一个片段生成后立即创建，并随着转码进行逐步更新
        ffmpeg_cmd += [
            "-hls_playlist_type", "vod",
            "-hls_list_size", str(hls_list_size),  # 默认0表示不限制，包含所有片段
        ]
    else:
        # Streaming模式：使用hls_list_size（默认0表示不限制）
        ffmpeg_cmd += [
            "-hls_list_size", str(hls_list_size),  # 默认0表示不限制，包含所有片段
        ]
    
    ffmpeg_cmd += [task["playlist_path"]]

    TRANSCODE_LOGGER.info("启动转码任务 %s", task_id)
    try:
        log_file = open(log_path, "w", encoding="utf-8")
    except Exception as exc:
        TRANSCODE_LOGGER.error(f"创建转码日志文件失败: {exc}")
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                task["status"] = "error"
                task["error"] = f"创建日志文件失败: {exc}"
                task["updated_at"] = time.time()
        return

    try:
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )
        TRANSCODE_LOGGER.info(f"已启动 ffmpeg 进程: task_id={task_id}, pid={process.pid}")
    except FileNotFoundError:
        TRANSCODE_LOGGER.error("未找到 ffmpeg 可执行文件")
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                task["status"] = "error"
                task["error"] = "未安装 ffmpeg"
                task["updated_at"] = time.time()
        log_file.close()
        return
    except Exception as exc:
        TRANSCODE_LOGGER.error(f"启动 ffmpeg 失败: {exc}")
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                task["status"] = "error"
                task["error"] = str(exc)
                task["updated_at"] = time.time()
        log_file.close()
        return

    with TRANSCODE_TASKS_LOCK:
        task = TRANSCODE_TASKS.get(task_id)
        if task:
            task["process"] = process
            task["status"] = "running"
            task["updated_at"] = time.time()

    playlist_ready = False
    try:
        while True:
            # 检查任务是否已被取消
            with TRANSCODE_TASKS_LOCK:
                task = TRANSCODE_TASKS.get(task_id)
                if not task or task.get("status") in ("error", "cancelled"):
                    TRANSCODE_LOGGER.info(f"转码任务 {task_id} 已被取消，停止等待")
                    if process.poll() is None:
                        try:
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                        except Exception as exc:
                            TRANSCODE_LOGGER.warning(f"停止已取消的任务 {task_id} 的进程失败: {exc}")
                    break
            
            if os.path.exists(task["playlist_path"]) and os.path.getsize(task["playlist_path"]) > 0:
                playlist_ready = True
                with TRANSCODE_TASKS_LOCK:
                    task = TRANSCODE_TASKS.get(task_id)
                    if task and task.get("status") not in ("error", "cancelled"):
                        task["status"] = "ready"
                        task["ready_at"] = time.time()
                        task["updated_at"] = time.time()
                        TRANSCODE_LOGGER.info(f"转码任务 {task_id} 播放列表已就绪: {task['playlist_path']}")
                break

            if process.poll() is not None:
                break

            time.sleep(0.5)
    finally:
        log_file.close()

    return_code = process.wait()

    with TRANSCODE_TASKS_LOCK:
        task = TRANSCODE_TASKS.get(task_id)
        if task:
            task["returncode"] = return_code
            task["process"] = None
            task["updated_at"] = time.time()
            current_status = task.get("status")
            
            # 如果任务已被取消，不再更新状态
            if current_status == "cancelled":
                TRANSCODE_LOGGER.info("转码任务 %s 已取消，返回码=%s", task_id, return_code)
            elif current_status not in ("error", "cancelled"):
                if return_code == 0:
                    if not playlist_ready and os.path.exists(task["playlist_path"]):
                        task["status"] = "ready"
                        task["ready_at"] = time.time()
                    task["status"] = "completed"
                    TRANSCODE_LOGGER.info("转码任务 %s 完成", task_id)
                else:
                    task["status"] = "error"
                    task["error"] = f"ffmpeg exited with code {return_code}"
                    TRANSCODE_LOGGER.warning("转码任务 %s 失败，返回码=%s", task_id, return_code)
            else:
                TRANSCODE_LOGGER.info("转码任务 %s 结束，状态=%s，返回码=%s", task_id, current_status, return_code)


def _get_direct_download_data_by_pickcode(pickcode, *, use_android_api=False, user_agent=None):
    if not pickcode:
        return ({'success': False, 'message': '缺少pickcode参数'}, 400)

    if CLOUD115_CLIENT is None or getattr(CLOUD115_CLIENT, 'driver', None) is None:
        return ({'success': False, 'message': '115 driver 未配置或未登录，请先设置 Cookie 并确保账户有效'}, 503)

    try:
        info = CLOUD115_CLIENT.get_download_info(
            pickcode,
            user_agent=user_agent,
            use_android_api=use_android_api,
        )
    except Cloud115AuthError as exc:
        app.logger.warning(f"115 driver 鉴权失败 (pickcode={pickcode}): {exc}")
        return ({'success': False, 'message': f'115 认证失败: {exc}'}, 401)
    except Exception as exc:
        app.logger.error(f"115 driver 获取直链失败 (pickcode={pickcode}): {exc}", exc_info=True)
        return ({'success': False, 'message': f'获取直链失败: {exc}'}, 500)

    download_url = info.get('url')
    if not download_url:
        app.logger.warning(f"115 driver 返回的数据中没有下载链接 (pickcode={pickcode})")
        return ({'success': False, 'message': '115 未返回下载链接，请稍后重试'}, 502)

    file_size_bytes, file_size_text = _format_download_size(info.get('file_size'))

    raw_data = info.get('raw') if isinstance(info.get('raw'), dict) else {}
    url_info = raw_data.get('url') if isinstance(raw_data.get('url'), dict) else {}
    auth_cookie = url_info.get('auth_cookie') if isinstance(url_info, dict) else None

    # 尝试获取视频播放时长（用于 ffprobe 失败时的备用）
    # 注意：driver download API 不返回时长，需要通过 file info API 获取
    play_duration = None
    try:
        app.logger.info(f"尝试从 115 API 获取视频时长: pickcode={pickcode}")
        # 优先使用 driver 的 files/get_info API（支持 pickcode 参数，返回 play_duration）
        # 如果 driver 不可用，回退到 OpenAPI
        file_info = CLOUD115_CLIENT.get_file_info_by_pickcode(pickcode)
        app.logger.info(f"115 API 响应: state={file_info.get('state') if file_info else None}, data keys={list(file_info.get('data', {}).keys()) if isinstance(file_info.get('data'), dict) else 'N/A'}")

        if file_info and file_info.get('state'):
            data = file_info.get('data')
            if isinstance(data, dict):
                # driver API 返回 play_duration，OpenAPI 返回 play_long
                play_duration = data.get('play_duration') or data.get('play_long')
                app.logger.info(f"从 115 API data dict 解析: play_duration={data.get('play_duration')}, play_long={data.get('play_long')}")
            elif isinstance(data, list) and len(data) > 0:
                play_duration = data[0].get('play_duration') or data[0].get('play_long')
                app.logger.info(f"从 115 API data list 解析: play_duration={data[0].get('play_duration')}, play_long={data[0].get('play_long')}")
            else:
                app.logger.warning(f"115 API 返回的 data 格式无法识别: {type(data)}")

            if play_duration:
                app.logger.info(f"从 115 API 获取到时长: {play_duration}s for {pickcode}")
            else:
                app.logger.warning(f"115 API 返回的 data 中没有 play_duration 或 play_long 字段")
        else:
            app.logger.warning(f"115 API 返回状态异常: file_info={file_info}")
    except Exception as exc:
        # API 可能未登录（没有 token），这是正常情况
        exc_str = str(exc)
        if "未登录" in exc_str or "Cookie 已失效" in exc_str:
            app.logger.info(f"115 API 未登录，无法获取视频时长")
        else:
            app.logger.warning(f"获取 115 API 文件信息失败: {exc}", exc_info=True)

    payload = {
        'success': True,
        'data': {
            'download_url': download_url,
            'file_name': info.get('file_name'),
            'file_size': file_size_text,
            'file_size_bytes': file_size_bytes,
            'pickcode': info.get('pick_code') or pickcode,
            'play_duration': play_duration,  # 视频播放时长（秒）
            'media_info': {'play_duration': play_duration},  # 兼容路径
            'client': info.get('client'),
            'oss_id': info.get('oss_id'),
            'android_api': bool(use_android_api),
            'auth_cookie': auth_cookie,
            'request_headers': info.get('request_headers'),
            'response_payload': info.get('response_payload'),
            'decoded_data': info.get('decoded_data'),
            'raw': raw_data,
            'encoded_data': info.get('encoded_data'),
        }
    }

    app.logger.info(f"成功通过115 driver获取直链 (pickcode={pickcode})")
    return (payload, 200)


def _get_video_play_data_by_pickcode(pickcode):
    """直接通过 pickcode 获取 115 视频播放信息。
    
    优先使用 115 客户端（支持 Token 或 Cookie 认证），失败时回退到 Alist。
    """

    if not pickcode:
        return ({'success': False, 'message': '缺少pickcode参数'}, 400)

    # 尝试使用115客户端（支持OpenAPI Token 或 driver Cookie）
    if CLOUD115_CLIENT is not None:
        try:
            payload = CLOUD115_CLIENT.get_video_play(pickcode)
            
            # 检查返回数据
            data = payload.get('data') if isinstance(payload, dict) else None
            if payload.get('state') and isinstance(data, dict) and 'video_url' in data:
                app.logger.info(f"成功通过115客户端获取视频播放地址 (pickcode={pickcode})")
                return ({'success': True, 'data': data}, 200)
            
            # 返回格式不符合预期
            message = ''
            if isinstance(payload, dict):
                message = payload.get('message') or payload.get('msg') or ''
            message = message or '获取播放地址失败'
            app.logger.warning(f"115客户端返回数据格式异常: {message}")
            
        except Exception as exc:
            # 115客户端调用失败，记录日志并继续尝试Alist
            app.logger.warning(f"115客户端获取视频播放地址失败: {exc}")
    else:
        app.logger.debug("CLOUD115_CLIENT未初始化")

    # 回退到 Alist 原始地址，封装为与 OpenAPI 相同的数据结构
    if is_alist_configured():
        app.logger.info(f"尝试通过Alist获取视频播放地址 (pickcode={pickcode})")
        alist_payload, alist_status = _get_alist_info_from_pickcode(pickcode)
        if alist_status == 200 and alist_payload.get('success'):
            data = alist_payload.get('data') or {}
            raw_url = data.get('raw_url') or data.get('proxy_url')
            if raw_url:
                app.logger.info(f"成功通过Alist获取视频播放地址 (pickcode={pickcode})")
                return ({
                    'success': True,
                    'data': {
                        # 用一个"原画(100)"清晰度承载 raw_url，兼容前端 UI
                        'video_url': [
                            {
                                'definition': 100,
                                'title': '原画',
                                'url': raw_url
                            }
                        ]
                    }
                }, 200)
    
    # 所有方法都失败，提示需要登录
    return ({'success': False, 'message': '未登录115云盘或Alist不可用，请使用 Cookie 或扫码登录'}, 401)


# 数据库辅助方法
def add_cloud115_file(file_data):
    """添加115云盘文件记录到数据库
    
    Args:
        file_data: 文件数据字典，包含必要字段
            - file_id: 115文件ID
            - title: 文件标题
            - path: 文件路径
            - size: 文件大小 (可以是整数字节数或字符串如"1.1GB")
            - category: 分类
            - video_id: 可选，影片ID
            
    Returns:
        int: 新添加的记录ID，失败则返回None
    """
    try:
        # 确保必要字段存在
        required_fields = ['file_id', 'title', 'path', 'size', 'category']
        for field in required_fields:
            if field not in file_data:
                app.logger.error(f"添加115云盘文件记录失败：缺少必要字段 {field}")
                return None
        
        # 准备数据
        now = int(time.time())
        category = file_data.get('category', '未分类')
        
        # 在115云盘中，file_id 不是 pickcode，使用专门的pickcode字段
        pickcode = file_data['pick_code']
        
        # 确保size字段是字符串格式
        size = file_data['size']
        # 如果size是数字，转换为字符串格式
        if isinstance(size, (int, float)):
            # 转换为人类可读格式
            if size > 1024 * 1024 * 1024:  # > 1GB
                size = f"{size / (1024 * 1024 * 1024):.2f}GB"
            elif size > 1024 * 1024:  # > 1MB
                size = f"{size / (1024 * 1024):.2f}MB"
            elif size > 1024:  # > 1KB
                size = f"{size / 1024:.2f}KB"
            else:
                size = f"{size}B"
        
        # 构建数据库记录
        db_record = {
            'title': file_data['title'],
            'filepath': file_data['path'],  # 使用路径作为filepath
            'url': f"https://115.com/?ct=file&ac=view&pickcode={pickcode}",  # 构建URL
            'thumbnail': file_data.get('thumbnail', ''),
            'description': file_data.get('description', ''),
            'category': category,
            'file_id': file_data['file_id'],  # 115文件ID
            'pickcode': pickcode,  # 明确存储pickcode
            'size': size,  # 使用字符串格式的文件大小
            'date_added': now,
            'play_count': 0,
            'last_played': 0,
            'video_id': file_data.get('video_id')  # 添加video_id字段
        }
        
        # 保存到数据库
        return db.save_cloud115_file(db_record)
    except Exception as e:
        app.logger.error(f"添加115云盘文件记录失败：{str(e)}", exc_info=True)
        return None

# 115云盘文件操作相关API

@app.route('/api/cloud115/list', methods=['GET'])
def cloud115_list():
    """获取115云盘文件列表（兼容path参数）"""
    try:
        # 支持path参数，映射到cid
        path = request.args.get('path', '0')
        # 处理根目录路径
        if path == '/' or path == '':
            cid = '0'
        else:
            cid = path

        try:
            limit = int(request.args.get('limit', '1150'))
            offset = int(request.args.get('offset', '0'))
        except ValueError:
            return jsonify({
                'success': False,
                'message': '参数格式错误'
            })

        # 使用统一客户端
        if CLOUD115_CLIENT is None:
            return jsonify({
                'success': False,
                'message': '115客户端未初始化'
            })

        from modules.cloud115_client import Cloud115AuthError, Cloud115RateLimitError
        try:
            result = CLOUD115_CLIENT.list_files(
                cid=cid,
                limit=limit,
                offset=offset,
                show_dir=1,
                aid=1,
                order='user_utime',
                asc=0
            )

            # 转换响应格式以匹配前端期望
            data = result.get('data', [])
            if isinstance(data, dict) and 'list' in data:
                # 处理返回格式为 {list: [...], count: ...} 的情况
                files = data.get('list', [])
            elif isinstance(data, list):
                files = data
            else:
                files = []

            # 转换字段名以匹配前端期望
            transformed_files = []
            for item in files:
                if not isinstance(item, dict):
                    continue

                # fc: "0" = 文件夹, "1" = 文件
                is_folder = item.get('fc') == '0' or item.get('fc') == 0

                transformed_files.append({
                    'type': 'folder' if is_folder else 'file',
                    'file_id': item.get('cid') if is_folder else item.get('fid', item.get('cid')),
                    'file_name': item.get('n') or item.get('fn') or item.get('name', ''),
                    'size': item.get('s') or item.get('size', 0),
                    'pick_code': item.get('pc') or item.get('pick_code', ''),
                })

            return jsonify({
                'success': True,
                'data': {
                    'files': transformed_files,
                    'path': path
                }
            })
        except Cloud115AuthError as e:
            app.logger.warning(f"115 认证失败: {e}")
            return jsonify({
                'success': False,
                'message': f'未授权: {str(e)}'
            })
        except Cloud115RateLimitError as e:
            app.logger.warning(f"115 限流: {e}")
            return jsonify({
                'success': False,
                'message': f'请求过于频繁: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"获取115文件列表失败: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'获取文件列表失败: {str(e)}'
        })

@app.route('/api/cloud115/files', methods=['GET'])
def cloud115_files():
    """获取115云盘文件列表"""
    try:
        # 参数获取
        cid = request.args.get('cid', '0')
        try:
            limit = int(request.args.get('limit', '1150'))
            offset = int(request.args.get('offset', '0'))
        except ValueError:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '参数格式错误'
            })
        
        # 使用统一客户端
        if CLOUD115_CLIENT is None:
            return jsonify({
                'state': 0,
                'code': 503,
                'message': '115客户端未初始化'
            })
        
        from modules.cloud115_client import Cloud115AuthError, Cloud115RateLimitError
        try:
            result = CLOUD115_CLIENT.list_files(
                cid=cid,
                limit=limit,
                offset=offset,
                show_dir=1,
                aid=1,
                order='user_utime',
                asc=0
            )
            
            return jsonify({
                'state': 1,
                'code': 0,
                'message': '',
                'data': result.get('data', [])
            })
        except Cloud115AuthError as e:
            app.logger.warning(f"115 认证失败: {e}")
            return jsonify({
                'state': 0,
                'code': 401,
                'message': f'未授权: {str(e)}'
            })
        except Cloud115RateLimitError as e:
            app.logger.warning(f"115 限流: {e}")
            return jsonify({
                'state': 0,
                'code': 429,
                'message': f'请求过于频繁: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"获取115文件列表失败: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取文件列表失败: {str(e)}'
        })

@app.route('/api/cloud115/folder_info', methods=['GET'])
def cloud115_folder_info():
    """获取115云盘文件夹信息"""
    try:
        file_id = request.args.get('file_id')
        if not file_id:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '缺少文件夹ID参数'
            })
        
        # 使用统一客户端
        if CLOUD115_CLIENT is None:
            return jsonify({
                'state': 0,
                'code': 503,
                'message': '115客户端未初始化'
            })
        
        from modules.cloud115_client import Cloud115AuthError
        try:
            result = CLOUD115_CLIENT.get_folder_info(file_id)
            return jsonify({
                'state': 1,
                'code': 0,
                'message': '',
                'data': result.get('data', {})
            })
        except Cloud115AuthError as e:
            app.logger.warning(f"115 认证失败: {e}")
            return jsonify({
                'state': 0,
                'code': 401,
                'message': f'未授权: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"获取115文件夹信息失败: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取文件夹信息失败: {str(e)}'
        })

@app.route('/api/cloud115/delete', methods=['POST'])
def cloud115_delete():
    """删除115云盘文件或文件夹"""
    try:
        data = request.get_json()
        if not data or 'file_id' not in data:
            return jsonify({
                'success': False,
                'message': '缺少文件ID参数'
            })

        file_id = data.get('file_id', '').strip()
        if not file_id:
            return jsonify({
                'success': False,
                'message': '文件ID不能为空'
            })

        # 使用统一客户端
        if CLOUD115_CLIENT is None:
            return jsonify({
                'success': False,
                'message': '115客户端未初始化'
            })

        from modules.cloud115_client import Cloud115AuthError, Cloud115RateLimitError
        try:
            app.logger.info(f"尝试删除115文件: file_id={file_id}")
            result = CLOUD115_CLIENT.delete_file(file_id)
            app.logger.info(f"115删除响应: {result}")

            # 检查删除结果
            # 115返回格式: {"state": true, "error": "", "errno": ""}
            # state为true/1表示成功，error和errno为空字符串或0都表示成功
            state = result.get('state')

            if state is True or state == 1 or state == '1':
                return jsonify({
                    'success': True,
                    'message': '删除成功'
                })
            else:
                error_msg = result.get('msg') or result.get('message') or '删除失败'
                return jsonify({
                    'success': False,
                    'message': error_msg
                })
        except Cloud115AuthError as e:
            app.logger.warning(f"115 认证失败: {e}")
            return jsonify({
                'success': False,
                'message': f'未授权: {str(e)}'
            })
        except Cloud115RateLimitError as e:
            app.logger.warning(f"115 限流: {e}")
            return jsonify({
                'success': False,
                'message': f'请求过于频繁: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"删除115文件失败: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'删除文件失败: {str(e)}'
        })

@app.route('/api/cloud115/move', methods=['POST'])
def cloud115_move():
    """移动115云盘文件或文件夹到指定目录"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': '缺少请求参数'
            })

        file_id = data.get('file_id', '').strip()
        target_cid = data.get('target_cid', '').strip()

        if not file_id:
            return jsonify({
                'success': False,
                'message': '缺少文件ID参数'
            })

        if not target_cid:
            return jsonify({
                'success': False,
                'message': '缺少目标目录ID参数'
            })

        # 使用统一客户端
        if CLOUD115_CLIENT is None:
            return jsonify({
                'success': False,
                'message': '115客户端未初始化'
            })

        from modules.cloud115_client import Cloud115AuthError, Cloud115RateLimitError
        try:
            app.logger.info(f"尝试移动115文件: file_id={file_id}, target_cid={target_cid}")
            result = CLOUD115_CLIENT.move_file(file_id, target_cid)
            app.logger.info(f"115移动响应: {result}")

            # 检查移动结果
            state = result.get('state')

            if state is True or state == 1 or state == '1':
                return jsonify({
                    'success': True,
                    'message': '移动成功'
                })
            else:
                error_msg = result.get('msg') or result.get('message') or '移动失败'
                return jsonify({
                    'success': False,
                    'message': error_msg
                })
        except Cloud115AuthError as e:
            app.logger.warning(f"115 认证失败: {e}")
            return jsonify({
                'success': False,
                'message': f'未授权: {str(e)}'
            })
        except Cloud115RateLimitError as e:
            app.logger.warning(f"115 限流: {e}")
            return jsonify({
                'success': False,
                'message': f'请求过于频繁: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"移动115文件失败: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'移动文件失败: {str(e)}'
        })

@app.route('/cloud115/login')
def cloud115_login_view():
    """115云盘登录管理页面"""
    return render_template('cloud115_login.html')


@app.route('/explorer')
def explorer_view():
    """115网盘浏览器页面"""
    return render_template('explorer.html')


@app.route('/explorer/api/files', methods=['GET'])
def explorer_api_files():
    """115网盘浏览器文件列表API"""
    return cloud115_files()


@app.route('/explorer/api/folder_info', methods=['GET'])
def explorer_api_folder_info():
    """115网盘浏览器文件夹信息API"""
    return cloud115_folder_info()


@app.route('/api/cloud115/import_directory', methods=['POST'])
def cloud115_import_directory_api():
    """导入115云盘目录中的视频文件"""
    try:
        data = request.get_json()
        if not data or 'folder_id' not in data:
            return jsonify({
                'success': False,
                'message': '缺少文件夹ID'
            })

        folder_id = data['folder_id']
        min_size_mb = data.get('min_size_mb', 50)  # 默认最小文件大小为50MB
        category_type = data.get('category_type', 'movies')  # 默认分类为电影
        
        # 将MB转换为字节
        min_size_bytes = min_size_mb * 1024 * 1024

        app.logger.debug(f"导入115目录，文件夹ID：{folder_id}，最小文件大小：{min_size_mb}MB, 导入类别：{category_type}")
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录115云盘'
            })
        
        # 获取文件夹信息
        folder_info_response = requests.get(
            'https://proapi.115.com/open/folder/get_info',
            params={'file_id': folder_id},
            headers={
                'Authorization': f'Bearer {access_token}'
            }
        )
        
        folder_info = folder_info_response.json().get('data', {})
        folder_name = folder_info.get('file_name', '未命名文件夹')

        # 获取数据库中已有的115云盘文件ID列表，用于去重
        db.ensure_connection()
        db.local.cursor.execute('SELECT file_id FROM cloud115_library')
        existing_file_ids = {row['file_id'] for row in db.local.cursor.fetchall() if row['file_id']}

        # 递归获取文件夹内所有视频文件
        video_files = []
        skipped_files = 0
        skipped_existing_files = 0

        def get_videos_in_folder(folder_id, path=""):
            """递归获取文件夹中的视频文件"""
            nonlocal skipped_files, skipped_existing_files
            offset = 0
            limit = 100
            while True:
                # 获取文件列表
                files_response = requests.get(
                    'https://proapi.115.com/open/ufile/files',
                    params={
                        'cid': folder_id,
                        'limit': limit,
                        'offset': offset,
                        'show_dir': 1,
                        'aid': 1
                    },
                    headers={
                        'Authorization': f'Bearer {access_token}'
                    }
                )

                files_data = files_response.json()
                files = files_data.get('data', [])
                
                if not files:
                    break

                for file in files:
                    current_path = f"{path}/{file['fn']}" if path else file['fn']
                    
                    if file['fc'] == '0':  # 文件夹
                        # 递归处理子文件夹
                        get_videos_in_folder(file['fid'], current_path)
                    elif file['fc'] == '1':  # 文件
                        # 检查是否为视频文件
                        if file.get('isv') == 1 or file['ico'].lower() in ['mp4', 'mkv', 'avi', 'wmv', 'mov', 'flv', 'm4v', 'rmvb', 'rm']:
                            # 检查文件是否已存在于数据库中
                            if file['fid'] in existing_file_ids:
                                skipped_existing_files += 1
                                app.logger.debug(f"跳过已存在文件：{file['fn']}，ID：{file['fid']}")
                                continue

                            # 获取文件详情，获取正确的pickcode和文件大小
                            file_details_response = requests.get(
                                'https://proapi.115.com/open/folder/get_info',
                                params={'file_id': file['fid']},
                                headers={
                                    'Authorization': f'Bearer {access_token}'
                                }
                            )
                            
                            file_details = file_details_response.json().get('data', {})
                            pick_code = file_details.get('pick_code', '')
                            file_size_str = file_details.get('size', '')  # 获取字符串格式的文件大小
                            
                            # 需要检查文件大小是否大于最小值
                            if min_size_mb > 0:
                                try:
                                    # 将字符串格式的文件大小转换为字节数
                                    file_size_bytes = convert_human_size_to_bytes(file_size_str)
                                    
                                    # 检查文件大小
                                    if file_size_bytes < min_size_bytes:
                                        skipped_files += 1
                                        app.logger.debug(f"跳过小文件：{file['fn']}，大小：{file_size_str}")
                                        continue
                                except Exception as e:
                                    app.logger.error(f"转换文件大小失败 '{file_size_str}': {str(e)}")
                                    # 如果转换失败，尝试使用fs字段
                                    if 'fs' in file:
                                        file_size_bytes = int(file['fs'])
                                        if file_size_bytes < min_size_bytes:
                                            skipped_files += 1
                                            app.logger.debug(f"跳过小文件：{file['fn']}，大小：{file_size_bytes/1024/1024:.2f}MB")
                                            continue

                            video_files.append({
                                'file_id': file['fid'],
                                'title': file['fn'],
                                'path': current_path,
                                'size': file_size_str,  # 使用字符串格式的文件大小
                                'category': category_type, # 使用导入类别
                                'thumbnail': file.get('thumb', ''),
                                'pick_code': pick_code  # 添加pick_code字段
                            })

                # 更新偏移量
                offset += len(files)
                if len(files) < limit:
                    break

        # 开始收集视频文件
        get_videos_in_folder(folder_id)
        
        # 导入视频文件到数据库
        imported_count = 0
        for video in video_files:
            try:
                # 添加到数据库
                if add_cloud115_file(video):
                    imported_count += 1
            except Exception as e:
                app.logger.error(f"Error importing video {video['title']}: {str(e)}", exc_info=True)

        return jsonify({
            'success': True,
            'message': f'成功导入{imported_count}个视频文件，跳过{skipped_files}个小于{min_size_mb}MB的文件，跳过{skipped_existing_files}个已存在的文件',
            'total': len(video_files),
            'imported': imported_count,
            'skipped_size': skipped_files,
            'skipped_existing': skipped_existing_files
        })

    except Exception as e:
        app.logger.error(f"Error importing 115 directory: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'导入目录失败: {str(e)}'
        })


def _run_import_115_directory_task(task_id, folder_id, min_size_mb, category_type, access_token):
    """后台执行115目录导入任务"""
    try:
        task = IMPORT_115_TASKS.get(task_id)
        if not task:
            IMPORT_115_LOGGER.error(f"任务 {task_id} 不存在")
            return

        task['status'] = 'running'
        task['progress'] = 0
        task['current_step'] = '正在获取文件列表...'
        task['imported'] = 0
        task['failed'] = 0
        task['skipped_size'] = 0
        task['skipped_existing'] = 0
        task['total_files'] = 0
        task['current_file'] = ''
        task['error_files'] = []
        task['imported_files'] = []
        task['logs'] = ['开始导入任务...']

        def update_progress(step, progress, current_file='', log_message=None):
            task['current_step'] = step
            task['progress'] = progress
            if current_file:
                task['current_file'] = current_file
            if log_message:
                task['logs'].append(log_message)
                if len(task['logs']) > 500:  # 限制日志数量
                    task['logs'] = task['logs'][-500:]

        update_progress('正在获取文件夹信息...', 5, '初始化', '获取文件夹信息...')

        # 获取文件夹信息
        try:
            folder_info_response = requests.get(
                'https://proapi.115.com/open/folder/get_info',
                params={'file_id': folder_id},
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=30
            )
            folder_info = folder_info_response.json().get('data', {})
            folder_name = folder_info.get('file_name', '未命名文件夹')
            task['folder_name'] = folder_name
            update_progress('正在获取文件列表...', 10, folder_name, f'目标文件夹: {folder_name}')
        except Exception as e:
            task['status'] = 'error'
            task['error'] = str(e)
            update_progress('获取文件夹信息失败', 0, '', f'错误: {str(e)}')
            IMPORT_115_LOGGER.error(f"获取文件夹信息失败: {e}")
            return

        # 获取数据库中已有的115云盘文件ID列表
        try:
            db.ensure_connection()
            db.local.cursor.execute('SELECT file_id FROM cloud115_library')
            existing_file_ids = {row['file_id'] for row in db.local.cursor.fetchall() if row['file_id']}
            update_progress('正在扫描文件...', 15, '', f'已存在 {len(existing_file_ids)} 个文件记录')
        except Exception as e:
            IMPORT_115_LOGGER.error(f"获取现有文件列表失败: {e}")
            existing_file_ids = set()

        # 递归获取文件夹内所有视频文件
        video_files = []
        skipped_files = 0
        skipped_existing_files = 0
        scanned_folders = 0
        max_folders_to_scan = 10000  # 防止无限递归的安全限制

        def get_videos_in_folder(fid, path="", folder_name=""):
            """递归获取文件夹中的视频文件"""
            nonlocal skipped_files, skipped_existing_files, scanned_folders

            # 安全限制
            scanned_folders += 1
            if scanned_folders > max_folders_to_scan:
                IMPORT_115_LOGGER.warning(f"已达到最大扫描目录数限制 ({max_folders_to_scan})")
                task['error_files'].append({
                    'title': folder_name or path,
                    'error': f'达到最大扫描目录数限制 ({max_folders_to_scan})'
                })
                return

            # 更新扫描进度
            if scanned_folders % 5 == 0:  # 每5个目录更新一次
                update_progress('正在扫描目录...', 20, folder_name or path,
                              f'已扫描 {scanned_folders} 个目录，找到 {len(video_files)} 个视频文件')

            offset = 0
            limit = 100

            while True:
                try:
                    files_response = requests.get(
                        'https://proapi.115.com/open/ufile/files',
                        params={
                            'cid': fid,
                            'limit': limit,
                            'offset': offset,
                            'show_dir': 1,
                            'aid': 1
                        },
                        headers={'Authorization': f'Bearer {access_token}'},
                        timeout=60  # 增加超时到60秒
                    )
                    files_data = files_response.json()

                    # 检查响应状态
                    if files_data.get('state') != 1:
                        IMPORT_115_LOGGER.error(f"API返回错误: {files_data.get('message', '未知错误')}")
                        task['error_files'].append({
                            'title': folder_name or path,
                            'error': f'API错误: {files_data.get("message", "未知错误")}'
                        })
                        break

                    files = files_data.get('data', [])

                    if not files:
                        break

                    for file in files:
                        current_path = f"{path}/{file['fn']}" if path else file['fn']

                        if file['fc'] == '0':  # 文件夹
                            get_videos_in_folder(file['fid'], current_path, file['fn'])
                        elif file['fc'] == '1':  # 文件
                            # 检查是否为视频文件
                            if file.get('isv') == 1 or file['ico'].lower() in ['mp4', 'mkv', 'avi', 'wmv', 'mov', 'flv', 'm4v', 'rmvb', 'rm', 'mpg', 'mpeg', 'webm', 'ts']:
                                # 检查文件是否已存在
                                if file['fid'] in existing_file_ids:
                                    skipped_existing_files += 1
                                    task['skipped_existing'] = skipped_existing_files
                                    continue

                                try:
                                    # 优先使用文件列表中已返回的信息，减少API调用
                                    pick_code = file.get('pc', '')

                                    # 使用文件列表中的大小信息（如果有）
                                    if 'fs' in file:
                                        file_size_bytes = int(file['fs'])
                                        file_size_str = f"{file_size_bytes / 1024 / 1024:.2f} MB"

                                        # 检查文件大小
                                        min_size_bytes = min_size_mb * 1024 * 1024
                                        if min_size_mb > 0 and file_size_bytes < min_size_bytes:
                                            skipped_files += 1
                                            task['skipped_size'] = skipped_files
                                            continue
                                    else:
                                        # 只有在没有大小信息时才调用get_info API
                                        file_details_response = requests.get(
                                            'https://proapi.115.com/open/folder/get_info',
                                            params={'file_id': file['fid']},
                                            headers={'Authorization': f'Bearer {access_token}'},
                                            timeout=15  # 单个文件详情请求超时设为15秒
                                        )
                                        file_details = file_details_response.json().get('data', {})
                                        pick_code = pick_code or file_details.get('pick_code', '')
                                        file_size_str = file_details.get('size', '')

                                        # 检查文件大小
                                        min_size_bytes = min_size_mb * 1024 * 1024
                                        if min_size_mb > 0:
                                            try:
                                                file_size_bytes = convert_human_size_to_bytes(file_size_str)
                                                if file_size_bytes < min_size_bytes:
                                                    skipped_files += 1
                                                    task['skipped_size'] = skipped_files
                                                    continue
                                            except Exception:
                                                continue

                                    video_files.append({
                                        'file_id': file['fid'],
                                        'title': file['fn'],
                                        'path': current_path,
                                        'size': file_size_str if 'file_size_str' in locals() else '',
                                        'category': category_type,
                                        'thumbnail': file.get('thumb', ''),
                                        'pick_code': pick_code
                                    })
                                except requests.Timeout:
                                    task['error_files'].append({
                                        'title': file['fn'],
                                        'error': '获取文件详情超时，已跳过'
                                    })
                                except Exception as e:
                                    task['error_files'].append({
                                        'title': file['fn'],
                                        'error': f'处理失败: {str(e)[:100]}'
                                    })

                    offset += len(files)
                    if len(files) < limit:
                        break
                except requests.Timeout:
                    IMPORT_115_LOGGER.error(f"获取目录列表超时: {folder_name or path}")
                    task['error_files'].append({
                        'title': folder_name or path,
                        'error': '获取目录列表超时，已跳过'
                    })
                    break
                except Exception as e:
                    IMPORT_115_LOGGER.error(f"获取文件列表失败 {folder_name or path}: {e}")
                    task['error_files'].append({
                        'title': folder_name or path,
                        'error': f'扫描失败: {str(e)[:100]}'
                    })
                    break

        # 开始扫描文件
        update_progress('正在扫描目录...', 20, '', '开始递归扫描文件夹...')
        get_videos_in_folder(folder_id, '', task.get('folder_name', ''))

        task['total_files'] = len(video_files)
        update_progress('正在导入文件...', 30, '', f'扫描完成，扫描了 {scanned_folders} 个目录，找到 {len(video_files)} 个新视频文件')
        IMPORT_115_LOGGER.info(f"扫描完成: {scanned_folders} 个目录, {len(video_files)} 个新视频文件")

        # 导入视频文件到数据库
        imported_count = 0
        failed_count = 0

        for idx, video in enumerate(video_files):
            try:
                task['current_file'] = video['title']
                progress = 30 + int((idx / len(video_files)) * 65)  # 30-95%
                task['progress'] = progress

                if add_cloud115_file(video):
                    imported_count += 1
                    task['imported'] = imported_count
                    task['imported_files'].append(video['title'])
                    if idx % 10 == 0:  # 每10个文件记录一次日志
                        update_progress('正在导入文件...', progress, video['title'],
                                      f'已导入 {imported_count}/{len(video_files)} 个文件')
                else:
                    failed_count += 1
                    task['failed'] = failed_count
                    task['error_files'].append({
                        'title': video['title'],
                        'error': '添加到数据库失败'
                    })
            except Exception as e:
                failed_count += 1
                task['failed'] = failed_count
                task['error_files'].append({
                    'title': video.get('title', 'Unknown'),
                    'error': str(e)
                })
                IMPORT_115_LOGGER.error(f"导入文件失败 {video.get('title')}: {e}")

        # 任务完成
        task['status'] = 'completed'
        task['progress'] = 100
        task['current_step'] = '导入完成'
        task['end_time'] = time.time()
        task['logs'].append(f'导入完成! 成功: {imported_count}, 失败: {failed_count}, '
                           f'跳过(小文件): {skipped_files}, 跳过(已存在): {skipped_existing_files}')
        IMPORT_115_LOGGER.info(f"导入任务 {task_id} 完成: 成功={imported_count}, 失败={failed_count}")

    except Exception as e:
        IMPORT_115_LOGGER.error(f"导入任务 {task_id} 执行失败: {e}", exc_info=True)
        task = IMPORT_115_TASKS.get(task_id)
        if task:
            task['status'] = 'error'
            task['error'] = str(e)
            task['end_time'] = time.time()
            task['logs'].append(f'任务执行失败: {str(e)}')


@app.route('/api/cloud115/import_directory_async', methods=['POST'])
def cloud115_import_directory_async():
    """异步导入115云盘目录中的视频文件（支持进度跟踪）"""
    try:
        data = request.get_json()
        if not data or 'folder_id' not in data:
            return jsonify({
                'success': False,
                'message': '缺少文件夹ID'
            })

        folder_id = data['folder_id']
        min_size_mb = data.get('min_size_mb', 50)
        category_type = data.get('category_type', 'movies')

        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录115云盘'
            })

        # 创建任务
        task_id = uuid.uuid4().hex

        IMPORT_115_TASKS[task_id] = {
            'id': task_id,
            'status': 'queued',
            'progress': 0,
            'current_step': '等待开始...',
            'current_file': '',
            'folder_id': folder_id,
            'min_size_mb': min_size_mb,
            'category_type': category_type,
            'imported': 0,
            'failed': 0,
            'skipped_size': 0,
            'skipped_existing': 0,
            'total_files': 0,
            'start_time': time.time(),
            'end_time': None,
            'error': None,
            'error_files': [],
            'imported_files': [],
            'logs': ['任务已创建，等待执行...']
        }

        # 启动后台线程
        thread = threading.Thread(
            target=_run_import_115_directory_task,
            args=(task_id, folder_id, min_size_mb, category_type, access_token),
            daemon=True
        )
        thread.start()

        IMPORT_115_LOGGER.info(f"创建导入任务 {task_id}")

        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '导入任务已创建',
            'status_url': url_for('cloud115_import_status', task_id=task_id)
        })

    except Exception as e:
        app.logger.error(f"创建导入任务失败: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'创建导入任务失败: {str(e)}'
        })


@app.route('/api/cloud115/import_status/<task_id>', methods=['GET'])
def cloud115_import_status(task_id):
    """获取115目录导入任务状态"""
    task = IMPORT_115_TASKS.get(task_id)
    if not task:
        return jsonify({
            'success': False,
            'message': '任务不存在'
        }), 404

    response = {
        'success': True,
        'task_id': task['id'],
        'status': task['status'],
        'progress': task['progress'],
        'current_step': task['current_step'],
        'current_file': task['current_file'],
        'imported': task['imported'],
        'failed': task['failed'],
        'skipped_size': task['skipped_size'],
        'skipped_existing': task['skipped_existing'],
        'total_files': task['total_files'],
        'error': task.get('error'),
        'logs': task['logs'][-50:]  # 只返回最近50条日志
    }

    # 只有在任务完成时才返回完整结果
    if task['status'] in ['completed', 'error']:
        response['error_files'] = task['error_files']
        response['imported_files'] = task['imported_files']
        if task['end_time']:
            response['duration'] = task['end_time'] - task['start_time']

    return jsonify(response)


@app.route('/api/cloud115/video_play_url', methods=['GET'])
def cloud115_video_play_url():
    """获取115云盘视频播放地址"""
    try:
        # 获取参数
        pickcode = (request.args.get('pickcode') or '').strip()
        if pickcode:
            payload, status = _get_video_play_data_by_pickcode(pickcode)
            if payload.get('success'):  # 补充标题信息（若前端有传 name 可复用）
                title = request.args.get('title') or request.args.get('name')
                if title:
                    payload['title'] = title
            return jsonify(payload), status

        file_id = request.args.get('file_id')
        if not file_id:
            return jsonify({
                'success': False,
                'message': '缺少文件ID或pickcode参数'
            })

        try:
            file_id_int = int(file_id)
        except (TypeError, ValueError):
            file_id_int = file_id
        
        # 获取数据库中的文件信息，获取pick_code
        file_info = db.get_cloud115_file(file_id_int)
        if not file_info:
            return jsonify({
                'success': False,
                'message': '文件不存在'
            })
        
        # 直接使用数据库中的pickcode字段
        pick_code = file_info.get('pickcode')
        
        # 如果pickcode为空，尝试从URL中提取
        if not pick_code:
            url = file_info.get('url', '')
            pick_code_match = re.search(r'pickcode=([^&]+)', url) if url else None
            if pick_code_match:
                pick_code = pick_code_match.group(1)
        
        # 如果还是找不到pickcode，尝试使用file_id字段
        if not pick_code:
            pick_code = file_info.get('file_id')
        
        if not pick_code:
            return jsonify({
                'success': False,
                'message': '无法获取文件的pick_code'
            })
        
        payload, status = _get_video_play_data_by_pickcode(pick_code)
        if payload.get('success'):
            try:
                db.update_cloud115_play_count(file_id_int)
            except Exception as exc:
                app.logger.warning(f"更新115播放次数失败: {exc}")
            payload['title'] = file_info.get('title', '')
        return jsonify(payload), status
    except Exception as e:
        app.logger.error(f"Error getting 115 video play URL: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'获取播放地址失败: {str(e)}'
        })


@app.route('/api/cloud115/direct_download', methods=['GET'])
def cloud115_direct_download():
    """获取115云盘文件的直链下载信息。"""
    try:
        pickcode = (request.args.get('pickcode') or '').strip()
        file_id = request.args.get('file_id')
        user_agent = (request.args.get('user_agent') or '').strip() or None
        use_android_api = (request.args.get('android_api') or '').strip().lower() in ('1', 'true', 'yes', 'on')
        include_debug = (request.args.get('debug') or '').strip().lower() in ('1', 'true', 'yes', 'on')

        file_info = None

        if not pickcode and file_id:
            try:
                file_id_value = int(file_id)
            except (TypeError, ValueError):
                file_id_value = file_id

            file_info = db.get_cloud115_file(file_id_value)
            if file_info:
                pickcode = _extract_pickcode_from_file_info(file_info)

        if not pickcode:
            return jsonify({
                'success': False,
                'message': '缺少pickcode或无法根据文件ID解析pickcode'
            }), 400

        payload, status = _get_direct_download_data_by_pickcode(
            pickcode,
            use_android_api=use_android_api,
            user_agent=user_agent,
        )

        if status == 200 and payload.get('success'):
            data = payload.get('data', {}) or {}

            if file_info:
                data.setdefault('file_name', file_info.get('title') or file_info.get('filepath'))
                if not data.get('file_size') and file_info.get('size'):
                    data['file_size'] = file_info.get('size')
            data.setdefault('pickcode', pickcode)

            transcode_config = get_cloud115_transcode_config()
            transcode_enabled = is_cloud115_transcode_enabled()
            download_info_for_task = dict(data)

            should_transcode, transcode_meta = _cloud115_should_transcode(
                download_info_for_task.get('file_name'),
                download_info_for_task,
                pickcode=pickcode,
                file_info=file_info
            )
            download_info_for_task["media_info"] = transcode_meta.get("media_info")

            transcode_payload = {
                "enabled": transcode_enabled,
                "should_transcode": should_transcode,
                "reasons": transcode_meta.get("reasons"),
                "meta": transcode_meta,
            }

            if should_transcode and transcode_enabled and transcode_config.get("auto_start", True):
                ok, note, task = _get_or_create_transcode_task(
                    pickcode,
                    download_info_for_task.get("file_name"),
                    download_info_for_task,
                    reason="auto",
                )
                if ok and task:
                    serialized = _serialize_transcode_task(task)
                    transcode_payload.update({
                        "task_id": task.get("id"),
                        "task_status": task.get("status"),
                        "task_created": note == "created",
                        "ready": serialized.get("ready"),
                        "stream_url": serialized.get("stream_url"),
                        "abs_stream_url": serialized.get("abs_stream_url"),
                        "status_url": serialized.get("status_url"),  # V2: 使用已序列化的 URL
                    })
                    # 包含检测到的视频时长
                    if serialized.get("duration") is not None:
                        transcode_payload["duration"] = serialized.get("duration")
                    # 包含完整的任务信息（前端可能需要）
                    transcode_payload["task"] = serialized
                else:
                    transcode_payload.update({
                        "task_created": False,
                        "error": note,
                        "task_info": task,
                    })
            elif should_transcode and not transcode_enabled:
                transcode_payload["error"] = "transcode_disabled"

            data["transcode"] = transcode_payload

            if not include_debug:
                for key in ('raw', 'request_headers', 'encoded_data'):
                    data.pop(key, None)

        return jsonify(payload), status
    except Exception as exc:
        app.logger.error(f"Error getting 115 direct download info: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'获取115直链失败: {str(exc)}'
        }), 500


@app.route('/api/cloud115/transcode/start', methods=['POST'])
def cloud115_transcode_start():
    """启动转码任务（V2 实现）

    使用新的 TranscodeManager，支持 ffprobe 预获取视频时长。
    """
    if TRANSCODE_V2_MANAGER is None:
        return jsonify({"error": "Transcode manager not initialized"}), 500

    data = request.get_json() or {}
    pickcode = data.get('pickcode')
    file_name = data.get('file_name')
    file_id_115 = data.get('file_id')  # 115 文件 ID，用于通过 OpenAPI 获取时长
    start_time = float(data.get('start_time', 0))

    if not pickcode or not file_name:
        return jsonify({"error": "Missing pickcode or file_name"}), 400

    # 尝试通过 115 OpenAPI 获取视频时长（使用 file_id）
    known_duration = None
    if file_id_115 and CLOUD115_CLIENT:
        try:
            app.logger.info(f"尝试从 115 OpenAPI 获取视频时长: file_id={file_id_115}")
            file_info = CLOUD115_CLIENT.openapi.get_folder_info(file_id_115)
            app.logger.info(f"115 OpenAPI 响应: state={file_info.get('state') if file_info else None}, data keys={list(file_info.get('data', {}).keys()) if isinstance(file_info.get('data'), dict) else 'N/A'}")

            if file_info and file_info.get('state'):
                data = file_info.get('data')
                if isinstance(data, dict):
                    # OpenAPI 返回 play_long（视频时长，单位：秒）
                    play_long = data.get('play_long')
                    if play_long:
                        known_duration = float(play_long)
                        app.logger.info(f"从 115 OpenAPI 获取到时长: {known_duration}s (file_id={file_id_115})")
                    else:
                        app.logger.warning(f"115 OpenAPI 返回的 data 中没有 play_long 字段")
            else:
                app.logger.warning(f"115 OpenAPI 返回状态异常: file_info={file_info}")
        except Exception as exc:
            app.logger.warning(f"获取 115 OpenAPI 文件信息失败: {exc}", exc_info=True)

    try:
        # 获取 115 直链（同时也获取 play_duration）
        response_payload, status = _get_direct_download_data_by_pickcode(pickcode)
        if status != 200 or not response_payload.get('success'):
            return jsonify({"error": response_payload.get('message', 'Failed to get download URL')}), status

        download_data = response_payload.get('data', {}) or {}
        source_url = download_data.get('download_url')
        if not source_url:
            return jsonify({"error": "No download URL in response"}), 502

        # 优先使用已获取的 play_duration（从 _get_direct_download_data_by_pickcode 返回）
        # 如果前端没有传递 file_id，这里会作为备用
        if not known_duration:
            play_duration = download_data.get('play_duration')
            if play_duration:
                known_duration = float(play_duration)
                app.logger.info(f"使用已获取的 play_duration 作为 known_duration: {known_duration}s")

        # 构建 HTTP 头
        headers = _build_http_headers_for_transcode(download_data, pickcode=pickcode)
        header_string = _build_ffmpeg_header_string(headers)

        # 创建或获取任务
        success, message, task = TRANSCODE_V2_MANAGER.get_or_create_task(
            pickcode=pickcode,
            file_name=file_name,
            source_url=source_url,
            header_string=header_string,
            start_time=start_time,
            known_duration=known_duration  # 传递从 OpenAPI 获取的时长
        )

        if success and task:
            task_dict = task.to_dict()
            # 添加兼容字段，让旧前端代码也能工作
            task_dict['status_url'] = f"/api/cloud115/transcode/status/{task.task_id}"
            task_dict['stream_url'] = task_dict.get('playlist_url', f"/api/cloud115/transcode/playlist/{task.task_id}")

            return jsonify({
                "success": True,
                "message": message,
                "task_id": task.task_id,
                "playlist_url": f"/api/cloud115/transcode/playlist/{task.task_id}",
                "duration": task.duration,
                "status": task.status.value,
                "task": task_dict
            })

        return jsonify({"error": message}), 400 if "Maximum" in message else 500

    except Exception as e:
        TRANSCODE_LOGGER.error(f"Error starting transcode: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# V2: 移除旧路由，使用 modules/transcode/api.py 中的 V2 版本
# @app.route('/api/cloud115/transcode/status/<task_id>', methods=['GET'])
# def api_cloud115_transcode_status(task_id):
    """查询转码任务状态。"""
    try:
        # 首先尝试在旧的 TRANSCODE_TASKS 中查找
        task = None
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                task['last_access'] = time.time()
                task['updated_at'] = time.time()
                serialized = _serialize_transcode_task(task)

        # 如果旧系统中没找到，尝试 V2 管理器
        if not task and TRANSCODE_V2_MANAGER is not None:
            v2_task = TRANSCODE_V2_MANAGER.get_task(task_id)
            if v2_task:
                # 将 V2 任务转换为兼容格式
                serialized = v2_task.to_dict()
                # 添加兼容字段，让旧前端代码能正常工作
                serialized['ready'] = v2_task.status.value in ('ready', 'completed')
                serialized['stream_url'] = f"/api/cloud115/transcode/playlist/{v2_task.task_id}"
                serialized['status_url'] = f"/api/cloud115/transcode/status/{v2_task.task_id}"
                return jsonify({
                    'success': True,
                    'task': serialized,
                    'active_tasks': len(TRANSCODE_V2_MANAGER.tasks) if TRANSCODE_V2_MANAGER else _get_active_transcode_count(),
                })

        if not task:
            return jsonify({'success': False, 'message': '任务不存在'}), 404

        debug = (request.args.get('debug') or '').strip().lower() in ('1', 'true', 'yes', 'on')
        log_tail = None
        if debug:
            log_path = task.get('log_path')
            if log_path and os.path.exists(log_path):
                try:
                    with open(log_path, 'r', encoding='utf-8', errors='ignore') as fh:
                        fh.seek(0, os.SEEK_END)
                        size = fh.tell()
                        fh.seek(max(size - 4096, 0))
                        log_tail = fh.read()
                except Exception as exc:
                    log_tail = f"读取日志失败: {exc}"

        payload = {
            'success': True,
            'task': serialized,
            'active_tasks': _get_active_transcode_count(),
        }
        if log_tail is not None:
            payload['log_tail'] = log_tail
        _cleanup_transcode_tasks()
        return jsonify(payload)
    except Exception as exc:
        app.logger.error(f"Error reading transcode status: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'查询任务状态失败: {str(exc)}'
        }), 500


# V2: 移除旧路由，使用 modules/transcode/api.py 中的 V2 版本
# @app.route('/api/cloud115/transcode/stop/<task_id>', methods=['POST', 'GET'])
# def api_cloud115_transcode_stop(task_id):
    """停止指定的转码任务。支持 POST 和 GET（用于 sendBeacon）。"""
    try:
        # 支持从 sendBeacon 发送的请求（可能是 GET 或 POST with FormData）
        reason = "手动停止"
        if request.method == 'POST':
            # 尝试从 JSON body 获取 reason
            json_data = request.get_json(silent=True)
            if json_data and json_data.get('reason'):
                reason = json_data.get('reason')
            # 或者从 FormData 获取
            elif request.form and request.form.get('reason'):
                reason = request.form.get('reason')
        client_ip = request.headers.get('X-Forwarded-For') or request.remote_addr
        ua = request.headers.get('User-Agent', '')[:200]
        has_body = bool(request.data) or bool(request.form) or bool(request.get_json(silent=True))
        TRANSCODE_LOGGER.info(f"收到停止转码任务请求: task_id={task_id}, method={request.method}, reason={reason}, ip={client_ip}, ua={ua}, has_body={has_body}")

        # 首先尝试停止旧系统的任务
        success = _stop_transcode_task(task_id, reason=reason)

        # 如果旧系统中没找到，尝试 V2 管理器
        if not success and TRANSCODE_V2_MANAGER is not None:
            v2_success = TRANSCODE_V2_MANAGER.stop_task(task_id, reason=reason)
            if v2_success:
                success = True

        if not success:
            return jsonify({
                'success': False,
                'message': '任务不存在或已停止'
            }), 404
        
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                serialized = _serialize_transcode_task(task)
            else:
                serialized = {}
        
        return jsonify({
            'success': True,
            'message': '任务已停止',
            'task': serialized,
            'active_tasks': _get_active_transcode_count(),
        })
    except Exception as exc:
        TRANSCODE_LOGGER.error(f"停止转码任务失败: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'停止任务失败: {str(exc)}'
        }), 500


# V2: 移除旧路由，使用 modules/transcode/api.py 中的 V2 版本
# @app.route('/api/cloud115/transcode/seek/<task_id>', methods=['POST'])
def api_cloud115_transcode_seek(task_id):
    """跳转到指定时间点，重启转码任务从该位置开始"""
    try:
        json_data = request.get_json(silent=True) or {}
        target_time = json_data.get('time')
        
        if target_time is None:
            return jsonify({
                'success': False,
                'message': '缺少时间参数 (time)'
            }), 400
        
        try:
            target_time = float(target_time)
            if target_time < 0:
                return jsonify({
                    'success': False,
                    'message': '时间参数必须大于等于0'
                }), 400
        except (TypeError, ValueError):
            return jsonify({
                'success': False,
                'message': '无效的时间参数'
            }), 400
        
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if not task:
                return jsonify({
                    'success': False,
                    'message': '转码任务不存在'
                }), 404

            # 检查任务状态
            status = task.get("status")
            current_seek_time = task.get("current_seek_time", 0)

            # 对于已停止/完成的任务，先检查是否在已转码范围内
            if status in ("cancelled", "completed", "error"):
                playlist_path = task.get("playlist_path")
                transcoded_duration = 0.0
                if playlist_path and os.path.exists(playlist_path):
                    transcoded_duration = _parse_m3u8_duration(playlist_path)
                    TRANSCODE_LOGGER.info(f"任务已{status}，已转码时长: {transcoded_duration:.2f} 秒，目标时间: {target_time:.2f} 秒")

                    # 如果目标时间在已转码范围内，允许播放
                    if target_time <= transcoded_duration + 2.0:
                        TRANSCODE_LOGGER.info(f"目标时间 {target_time:.2f} 在已转码范围内 ({transcoded_duration:.2f} 秒)，允许播放")
                        return jsonify({
                            'success': True,
                            'message': f'目标时间在已转码范围内，可以播放',
                            'task': _serialize_transcode_task(task),
                            'transcoded_duration': transcoded_duration,
                        })

                # 超出已转码范围，尝试重启转码（对 cancelled 和 completed 状态）
                if status in ("cancelled", "completed"):
                    TRANSCODE_LOGGER.info(f"任务已{status}，但目标时间 {target_time:.2f} 超出已转码范围 ({transcoded_duration:.2f} 秒)，将重启转码")
                    # 重置任务状态为 queued，继续下面的重启逻辑
                    task["status"] = "queued"
                    task["updated_at"] = time.time()
                    task["error"] = None
                    task["returncode"] = None
                    task["process"] = None
                    status = "queued"
                else:
                    # error 状态不自动重启
                    return jsonify({
                        'success': False,
                        'message': f'任务状态为 {status}，且目标时间 {target_time:.2f} 超出已转码范围 ({transcoded_duration:.2f} 秒)',
                        'transcoded_duration': transcoded_duration,
                    }), 400

            # 检查是否已经在转码该位置附近（±5秒内，避免频繁重启）
            if abs(target_time - current_seek_time) < 5:
                TRANSCODE_LOGGER.info(f"跳转目标时间 {target_time:.2f} 接近当前转码位置 {current_seek_time:.2f}，无需重启")
                return jsonify({
                    'success': True,
                    'message': '已在目标位置附近，无需跳转',
                    'task': _serialize_transcode_task(task),
                })

            # 检查视频总时长
            duration = task.get("duration")
            if duration and target_time > duration:
                return jsonify({
                    'success': False,
                    'message': f'目标时间 {target_time:.2f} 超过视频总时长 {duration:.2f}'
                }), 400

            # 检查 m3u8 文件，判断目标时间是否在已转码范围内
            playlist_path = task.get("playlist_path")
            transcoded_duration = 0.0
            if playlist_path and os.path.exists(playlist_path):
                transcoded_duration = _parse_m3u8_duration(playlist_path)
                TRANSCODE_LOGGER.info(f"已转码时长: {transcoded_duration:.2f} 秒，目标时间: {target_time:.2f} 秒")

                # 如果目标时间在已转码范围内（考虑一些缓冲，±2秒），不需要重启
                if target_time <= transcoded_duration + 2.0:
                    TRANSCODE_LOGGER.info(f"目标时间 {target_time:.2f} 在已转码范围内 ({transcoded_duration:.2f} 秒)，无需重启")
                    return jsonify({
                        'success': True,
                        'message': f'目标时间在已转码范围内，无需重启',
                        'task': _serialize_transcode_task(task),
                        'transcoded_duration': transcoded_duration,
                    })

            TRANSCODE_LOGGER.info(f"转码任务 {task_id} 跳转到时间点 {target_time:.2f} 秒 (当前: {current_seek_time:.2f} 秒, 已转码: {transcoded_duration:.2f} 秒)")
            
            # 获取进程引用和 PID（在锁内）
            process = task.get("process")
            process_pid = None
            if process:
                try:
                    process_pid = process.pid
                except Exception:
                    pass
            
            # 先设置状态为 cancelled，让运行循环检测到
            task["status"] = "cancelled"
            task["updated_at"] = time.time()
            task["error"] = f"任务已停止: 跳转到 {target_time:.2f} 秒"
            
            # 如果进程存在，先尝试终止（在锁内设置，但实际终止在锁外）
            if process:
                task["process"] = None  # 清除引用，避免其他地方使用
        
        # 在锁外执行进程终止操作
        if process:
            TRANSCODE_LOGGER.info(f"正在终止转码任务 {task_id} 的 FFmpeg 进程 (PID: {process_pid})")
            try:
                # 检查进程是否还在运行
                if process.poll() is None:
                    # 尝试优雅停止
                    process.terminate()
                    TRANSCODE_LOGGER.info(f"已发送 terminate 信号给进程 {process_pid}")

                    # 等待进程停止（最多2秒，加快跳转响应）
                    try:
                        process.wait(timeout=2)
                        TRANSCODE_LOGGER.info(f"转码进程 {process_pid} 已优雅停止")
                    except subprocess.TimeoutExpired:
                        # 如果2秒内没有停止，强制杀死
                        TRANSCODE_LOGGER.warning(f"转码进程 {process_pid} 在2秒内未停止，强制终止")
                        process.kill()
                        process.wait()
                        TRANSCODE_LOGGER.info(f"转码进程 {process_pid} 已强制终止")
                else:
                    TRANSCODE_LOGGER.info(f"转码进程 {process_pid} 已经结束 (返回码: {process.returncode})")
            except ProcessLookupError:
                TRANSCODE_LOGGER.info(f"转码进程 {process_pid} 已经不存在")
            except Exception as exc:
                TRANSCODE_LOGGER.error(f"停止转码任务 {task_id} 的进程时出错: {exc}", exc_info=True)
        
        TRANSCODE_LOGGER.info(f"转码任务 {task_id} 已成功停止，准备重新启动")
        
        # 清理旧的段文件（可选：保留已生成的段用于回放）
        # 这里我们选择清理，因为从新位置开始转码会产生新的段
        # 需要重新获取任务信息来获取 output_dir
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if not task:
                return jsonify({
                    'success': False,
                    'message': '转码任务在停止过程中被删除'
                }), 404
            output_dir = task.get("output_dir")
            playlist_path = task.get("playlist_path")
        
        if output_dir and os.path.isdir(output_dir):
            try:
                # 只清理 ts 段文件，保留 m3u8 和日志
                for filename in os.listdir(output_dir):
                    if filename.endswith('.ts'):
                        filepath = os.path.join(output_dir, filename)
                        try:
                            os.remove(filepath)
                        except Exception as exc:
                            TRANSCODE_LOGGER.warning(f"清理段文件失败 {filepath}: {exc}")
                
                # 删除旧的 m3u8，让 FFmpeg 重新生成
                if playlist_path and os.path.exists(playlist_path):
                    try:
                        os.remove(playlist_path)
                    except Exception as exc:
                        TRANSCODE_LOGGER.warning(f"清理播放列表失败 {playlist_path}: {exc}")
            except Exception as exc:
                TRANSCODE_LOGGER.warning(f"清理转码目录失败: {exc}")
            
        # 再次获取锁，重置任务状态
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if not task:
                return jsonify({
                    'success': False,
                    'message': '转码任务在停止过程中被删除'
                }), 404
            
            # 重置任务状态
            task["status"] = "queued"
            task["updated_at"] = time.time()
            task["ready_at"] = None
            task["returncode"] = None
            task["error"] = None
            task["process"] = None  # 确保进程引用已清除
            task["current_seek_time"] = target_time

        # 等待一小段时间，确保进程完全清理
        time.sleep(0.5)

        # 重新获取直链（旧的直链可能已过期）
        pickcode = task.get("pickcode")
        new_download_url = None
        new_headers = None
        new_header_string = None

        if pickcode and CLOUD115_CLIENT is not None:
            try:
                TRANSCODE_LOGGER.info(f"Seek 时重新获取直链 (pickcode={pickcode})")
                payload = CLOUD115_CLIENT.get_video_play(pickcode)
                data = payload.get('data') if isinstance(payload, dict) else None

                if isinstance(data, dict):
                    video_urls = data.get('video_url')
                    if isinstance(video_urls, list) and len(video_urls) > 0:
                        # 优先选择原画
                        video_urls.sort(key=lambda x: x.get('definition', 0), reverse=True)
                        new_download_url = video_urls[0].get('url')
                        if new_download_url:
                            TRANSCODE_LOGGER.info(f"Seek 时成功获取新直链: {new_download_url[:100]}...")
            except Exception as exc:
                TRANSCODE_LOGGER.warning(f"Seek 时重新获取直链失败: {exc}")

        # 更新任务中的直链信息
        if new_download_url:
            with TRANSCODE_TASKS_LOCK:
                task = TRANSCODE_TASKS.get(task_id)
                if task:
                    task["download_url"] = new_download_url
                    # 重建 headers 和 header_string
                    new_headers = _build_http_headers_for_transcode({"download_url": new_download_url}, pickcode=pickcode)
                    new_header_string = _build_ffmpeg_header_string(new_headers)
                    task["http_headers"] = new_headers
                    task["header_string"] = new_header_string

        # 重新启动转码任务，从目标时间开始
        TRANSCODE_LOGGER.info(f"重新启动转码任务 {task_id}，从 {target_time:.2f} 秒开始")
        thread = threading.Thread(target=_run_transcode_task, args=(task_id, target_time), daemon=True)
        thread.start()
        
        # 等待一下，确保任务状态更新
        time.sleep(0.2)
        
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if task:
                serialized = _serialize_transcode_task(task)
            else:
                serialized = {}
        
        return jsonify({
            'success': True,
            'message': f'已跳转到 {target_time:.2f} 秒，正在重新开始转码',
            'task': serialized,
            'target_time': target_time,
        })
        
    except Exception as exc:
        TRANSCODE_LOGGER.error(f"跳转转码任务失败: {exc}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'跳转失败: {str(exc)}'
        }), 500


# V2: 移除旧路由，使用 modules/transcode/api.py 中的 V2 版本
# @app.route('/api/cloud115/transcode/tasks', methods=['GET'])
# def api_cloud115_transcode_tasks():
    """列出当前所有转码任务（用于管理页面）。"""
    try:
        with TRANSCODE_TASKS_LOCK:
            tasks = list(TRANSCODE_TASKS.values())
            serialized = [_serialize_transcode_task(t) for t in tasks]
        return jsonify({
            'success': True,
            'tasks': serialized,
            'active_tasks': _get_active_transcode_count(),
        })
    except Exception as exc:
        TRANSCODE_LOGGER.error(f"列出转码任务失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500


# V2: 移除旧路由，使用 modules/transcode/api.py 中的 V2 版本
# @app.route('/api/cloud115/transcode/delete/<task_id>', methods=['POST'])
# def api_cloud115_transcode_delete(task_id):
    """强制删除转码任务（会尝试停止进程并清理任务目录）。"""
    try:
        reason = (request.get_json(silent=True) or {}).get('reason') or '管理页面删除'
        # 先尝试停止
        _stop_transcode_task(task_id, reason=reason)
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.pop(task_id, None)
            if task:
                key = task.get('task_key')
                if key:
                    TRANSCODE_TASK_KEYS.pop(key, None)
                output_dir = task.get('output_dir')
            else:
                output_dir = None
        if output_dir and os.path.isdir(output_dir):
            try:
                shutil.rmtree(output_dir, ignore_errors=True)
                TRANSCODE_LOGGER.info(f"已删除转码任务目录: {output_dir}")
            except Exception as exc:
                TRANSCODE_LOGGER.warning(f"删除转码任务目录失败 ({output_dir}): {exc}")
        return jsonify({'success': True, 'message': '任务已删除'})
    except Exception as exc:
        TRANSCODE_LOGGER.error(f"删除转码任务失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500


# 转码任务管理页面（保留，仅渲染模板）
@app.route('/cloud115/transcode/tasks', methods=['GET'])
def cloud115_transcode_tasks_view():
    """转码任务管理页面"""
    return render_template('cloud115_transcode_admin.html')


# V2: 移除旧路由，使用 modules/transcode/api.py 中的 V2 版本
# @app.route('/cloud115/transcode/<task_id>/<path:filename>', methods=['GET'])
# def cloud115_transcode_file(task_id, filename):
    """提供转码结果的HLS切片/播放列表。"""
    try:
        with TRANSCODE_TASKS_LOCK:
            task = TRANSCODE_TASKS.get(task_id)
            if not task:
                abort(404)
            task['last_access'] = time.time()
            output_dir = task.get('output_dir')
            duration = task.get('duration')  # 获取视频总时长
        if not output_dir or not os.path.isdir(output_dir):
            abort(404)

        safe_path = safe_join(output_dir, filename)
        if not safe_path or not os.path.isfile(safe_path):
            abort(404)

        # 如果是 m3u8 文件，添加总时长信息到响应头（不修改文件内容）
        if filename.endswith('.m3u8'):
            try:
                # 直接返回文件，让前端处理时长设置
                # 注意：不要强制添加 ENDLIST 标签，因为转码是实时进行的
                # HLS.js 会自动轮询 m3u8 文件获取新的分片
                response = send_from_directory(output_dir, filename, conditional=True)

                # 添加自定义头信息，传递总时长（前端可以通过 XHR 获取）
                if duration:
                    response.headers['X-Video-Duration'] = str(duration)

                return response
            except Exception as exc:
                TRANSCODE_LOGGER.warning(f"处理 m3u8 文件时出错 {task_id}/{filename}: {exc}")
                # 出错时仍然返回原文件
                return send_from_directory(output_dir, filename, conditional=True)
        else:
            # 非 m3u8 文件（.ts 分片等），直接返回
            return send_from_directory(output_dir, filename, conditional=True)
    except Exception as exc:
        app.logger.warning(f"读取转码文件失败 {task_id}/{filename}: {exc}")
        abort(404)


@app.route('/api/cloud115/alist_play_info', methods=['GET'])
def cloud115_alist_play_info():
    """获取Alist播放所需信息"""
    if not is_alist_configured():
        return jsonify({
            'success': False,
            'message': 'Alist未配置或未启用'
        }), 400

    pickcode = (request.args.get('pickcode') or '').strip()
    file_path = request.args.get('path')

    if pickcode:
        payload, status = _get_alist_info_from_pickcode(pickcode, file_path)
        if not payload.get('success'):
            return jsonify(payload), status

        data = payload.get('data', {})
        stream_params = {'pickcode': pickcode}
        if data.get('relative_path'):
            stream_params['path'] = data['relative_path']
        data['proxy_url'] = url_for('cloud115_alist_stream', _external=False, **stream_params)
        return jsonify({'success': True, 'data': data})

    file_id = request.args.get('file_id')
    if not file_id:
        return jsonify({
            'success': False,
            'message': '缺少文件ID参数'
        }), 400

    try:
        file_id_int = int(file_id)
    except (TypeError, ValueError):
        file_id_int = file_id

    file_info = db.get_cloud115_file(file_id_int)
    if not file_info:
        return jsonify({
            'success': False,
            'message': '找不到指定的115文件记录'
        }), 404

    relative_path = resolve_cloud115_relative_path(file_info)
    if not relative_path:
        return jsonify({
            'success': False,
            'message': '无法确定文件路径，请确认已登录（Token 或 Cookie）'
        }), 500

    alist_path = build_alist_path(relative_path)
    if not alist_path:
        return jsonify({
            'success': False,
            'message': '无法构建Alist路径'
        }), 500

    app.logger.info(f"请求Alist播放路径: {alist_path}")
    info = get_alist_file_info(alist_path)
    if info.get('error'):
        error_msg = str(info.get('error', '')).lower()
        if 'object not found' in error_msg or 'not found' in error_msg:
            app.logger.warning(f"Alist返回路径不存在,尝试从115 API刷新完整路径: {alist_path}")
            new_relative_path = resolve_cloud115_relative_path(file_info, force_refresh=True)
            if new_relative_path and new_relative_path != relative_path:
                app.logger.info(f"获取到新的完整路径: {new_relative_path}")
                alist_path = build_alist_path(new_relative_path)
                if alist_path:
                    info = get_alist_file_info(alist_path)
        else:
            info = get_alist_file_info(alist_path, force_refresh=True)
    if info.get('error'):
        return jsonify({
            'success': False,
            'message': f"从Alist获取播放信息失败: {info.get('error')}"
        }), 502

    if not info.get('raw_url'):
        return jsonify({
            'success': False,
            'message': 'Alist未返回原始播放地址'
        }), 502

    data = info.get('data') or {}
    file_name = data.get('name') or file_info.get('title') or file_info.get('filepath') or ''
    file_size_text = file_info.get('size') or format_size_from_alist(data)
    proxy_url = url_for('cloud115_alist_stream', file_id=file_id_int, _external=False)

    response_payload = {
        'success': True,
        'data': {
            'proxy_url': proxy_url,
            'raw_url': info.get('raw_url'),
            'alist_path': alist_path,
            'relative_path': relative_path,
            'file_name': file_name,
            'file_size': file_size_text,
            'file_size_bytes': data.get('size'),
            'updated_at': data.get('modified') or data.get('mtime'),
            'mode': 'alist'
        }
    }

    return jsonify(response_payload)


@app.route('/api/cloud115/alist/stream', methods=['GET'])
def cloud115_alist_stream():
    """通过WebServer代理Alist视频流,使用Alist的raw_url直接流式传输"""
    if not is_alist_configured():
        return jsonify({
            'error': 'Alist未配置或未启用'
        }), 400

    pickcode = (request.args.get('pickcode') or '').strip()
    file_path = request.args.get('path')

    raw_url = None
    alist_path = None
    relative_path = None
    filename_for_mime = None

    if pickcode:
        payload, status = _get_alist_info_from_pickcode(pickcode, file_path)
        if not payload.get('success'):
            return jsonify({'error': payload.get('message', '无法获取Alist播放信息')}), status
        data = payload.get('data', {})
        raw_url = data.get('raw_url')
        alist_path = data.get('alist_path')
        relative_path = data.get('relative_path')
        filename_for_mime = data.get('file_name') or (relative_path.split('/')[-1] if relative_path else '')
        app.logger.info(f"Alist流代理(直连模式)路径: {alist_path}")
    else:
        file_id = request.args.get('file_id')
        if not file_id:
            return jsonify({'error': '缺少文件ID或pickcode参数'}), 400

        try:
            file_id_int = int(file_id)
        except (TypeError, ValueError):
            file_id_int = file_id

        file_info = db.get_cloud115_file(file_id_int)
        if not file_info:
            return jsonify({'error': '找不到指定的115文件记录'}), 404

        relative_path = resolve_cloud115_relative_path(file_info)
        if not relative_path:
            return jsonify({'error': '无法确定文件路径'}), 500

        alist_path = build_alist_path(relative_path)
        if not alist_path:
            return jsonify({'error': '无法构建Alist路径'}), 500

        app.logger.info(f"Alist流代理路径: {alist_path}")
        info = get_alist_file_info(alist_path)
        if info.get('error'):
            error_msg = str(info.get('error', '')).lower()
            if 'object not found' in error_msg or 'not found' in error_msg:
                app.logger.warning(f"Alist返回路径不存在,尝试从115 API刷新完整路径: {alist_path}")
                new_relative_path = resolve_cloud115_relative_path(file_info, force_refresh=True)
                if new_relative_path and new_relative_path != relative_path:
                    app.logger.info(f"获取到新的完整路径: {new_relative_path}")
                    alist_path = build_alist_path(new_relative_path)
                    if alist_path:
                        info = get_alist_file_info(alist_path)
            else:
                info = get_alist_file_info(alist_path, force_refresh=True)
        if info.get('error'):
            return jsonify({'error': f"无法从Alist获取播放地址: {info.get('error')}"}), 502

        raw_url = info.get('raw_url')
        if not raw_url:
            return jsonify({'error': 'Alist未返回原始播放地址'}), 502

        filename_for_mime = file_info.get('title', '') or relative_path

    if not raw_url:
        return jsonify({'error': '无法获取Alist播放地址'}), 502

    config = get_alist_config()
    timeout = config.get('timeout', 30)

    app.logger.info(f"Alist raw_url代理: {raw_url[:100]}...")

    headers = {}
    range_header = request.headers.get('Range')
    if range_header:
        headers['Range'] = range_header

    method = request.method.upper()
    stream = method != 'HEAD'

    try:
        upstream = requests.request(
            method,
            raw_url,
            headers=headers,
            stream=stream,
            timeout=timeout,
            allow_redirects=True
        )
        app.logger.info(f"Alist upstream响应: status={upstream.status_code}")
    except requests.RequestException as exc:
        if alist_path:
            clear_alist_file_cache(alist_path)
        app.logger.error(f"代理Alist视频失败: {exc}")
        return jsonify({'error': f'代理Alist视频失败: {exc}'}), 502

    if upstream.status_code >= 400:
        try:
            error_preview = upstream.text[:500]
        except Exception:
            error_preview = '<non-text response>'
        app.logger.error(f"Alist上游返回错误: status={upstream.status_code}, body={error_preview}")
        if upstream.status_code in (403, 404) and alist_path:
            clear_alist_file_cache(alist_path)
        return jsonify({'error': f'Alist返回错误 {upstream.status_code}: {error_preview[:100]}'}), 502

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    if stream:
        response = Response(stream_with_context(generate()), status=upstream.status_code)
    else:
        upstream.close()
        response = Response(status=upstream.status_code)

    passthrough_headers = [
        'Content-Type',
        'Content-Length',
        'Content-Range',
        'Accept-Ranges',
        'Content-Disposition',
        'ETag',
        'Last-Modified',
        'Cache-Control',
        'Expires'
    ]
    for header in passthrough_headers:
        value = upstream.headers.get(header)
        if value:
            response.headers[header] = value

    original_content_type = response.headers.get('Content-Type')
    if 'Content-Type' not in response.headers or not original_content_type:
        filename = filename_for_mime or ''
        lower_name = filename.lower()
        if lower_name.endswith('.mp4'):
            response.headers['Content-Type'] = 'video/mp4'
        elif lower_name.endswith('.mkv'):
            response.headers['Content-Type'] = 'video/x-matroska'
        elif lower_name.endswith('.avi'):
            response.headers['Content-Type'] = 'video/x-msvideo'
        elif lower_name.endswith('.mov'):
            response.headers['Content-Type'] = 'video/quicktime'
        elif lower_name.endswith('.webm'):
            response.headers['Content-Type'] = 'video/webm'
        else:
            response.headers['Content-Type'] = 'video/mp4'
        app.logger.info(f"设置Content-Type为: {response.headers['Content-Type']} (文件名: {filename})")
    else:
        app.logger.info(f"使用原始Content-Type: {original_content_type}")

    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Expose-Headers'] = ', '.join(passthrough_headers)
    response.headers['Access-Control-Allow-Methods'] = 'GET, HEAD, OPTIONS'

    is_secure = request.is_secure
    request_scheme = request.scheme
    app.logger.info(f"请求协议: {request_scheme} (is_secure: {is_secure}), Content-Type: {response.headers.get('Content-Type')}, Accept-Ranges: {response.headers.get('Accept-Ranges')}")

    return response

@app.route('/find_cloud115_by_video_id/<video_id>', methods=['GET'])
def find_cloud115_by_video_id(video_id):
    """根据视频ID查找并播放115云盘文件"""
    try:
        # 获取所有云盘文件
        cloud115_files = db.get_cloud115_files()
        
        # 查找匹配指定视频ID的文件
        matching_files = [file for file in cloud115_files if file.get('video_id') == video_id]
        
        if matching_files:
            # 找到匹配的文件，使用第一个匹配项
            file_id = matching_files[0].get('id')
            app.logger.info(f"为视频ID {video_id} 找到115云盘文件，重定向到播放器")
            return redirect(url_for('cloud115_player', file_id=file_id))
        else:
            # 未找到匹配的文件，显示错误页面
            app.logger.warning(f"未找到视频ID为 {video_id} 的115云盘文件")
            return render_template('error.html', 
                                  error_title="115云盘文件未找到", 
                                  error_message=f"无法找到视频ID为 {video_id} 的115云盘文件，请确保您已将此影片添加到115云盘目录并导入。您可以在影片详情页面添加磁力链接到您的115云盘并再次导入该影片存储目录。")
    except Exception as e:
        app.logger.error(f"查找115云盘文件错误: {str(e)}", exc_info=True)
        return render_template('error.html', 
                              error_title="查找错误", 
                              error_message=f"查找115云盘文件时出错: {str(e)}")

@app.route('/api/cloud115/proxy')
def cloud115_proxy_stream():
    """专用于115云盘的视频流代理，处理HLS流和TS片段"""
    stream_url = request.args.get('url')
    cookie_name = request.args.get('cookie_name')
    cookie_value = request.args.get('cookie_value')
    cookie_path = request.args.get('cookie_path') or '/'
    cookie_expire = request.args.get('cookie_expire')
    pickcode = request.args.get('pickcode')
    logging.info(f"115云盘视频流代理请求: {stream_url}")
    
    if not stream_url:
        return jsonify({"error": "Missing URL parameter"}), 400
        
    try:
        # 解码URL并处理嵌套代理问题
        original_url = urllib.parse.unquote(stream_url)
        
        # 递归解析嵌套的代理URL
        while "/api/cloud115/proxy" in original_url or "/api/proxy/stream" in original_url:
            parsed = urllib.parse.urlparse(original_url)
            query = urllib.parse.parse_qs(parsed.query)
            if 'url' in query and query['url']:
                original_url = urllib.parse.unquote(query['url'][0])
                logging.info(f"115云盘代理解嵌套URL: {original_url}")
            else:
                break
        
        # 最终解码后的URL
        decoded_url = original_url
        logging.info(f"115云盘最终代理URL: {decoded_url}")
        
        # 获取URL的基本路径（用于解析相对路径）
        url_parts = urllib.parse.urlparse(decoded_url)
        base_url = f"{url_parts.scheme}://{url_parts.netloc}{os.path.dirname(url_parts.path)}/"
        base_domain = f"{url_parts.scheme}://{url_parts.netloc}"
        
        # 设置115云盘专用请求头
        session_headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://115.com",
            "Referer": f"https://115.com/?ct=play&pickcode={pickcode}" if pickcode else "https://115.com/",
        }

        target_headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://115.com",
            "Referer": f"https://115.com/?ct=play&pickcode={pickcode}" if pickcode else "https://115.com/",
        }
        
        # 传递一些重要的请求头
        important_headers = ["Range", "If-Modified-Since", "If-None-Match", "If-Range", "Cache-Control"]
        for header in important_headers:
            if header.lower() in request.headers:
                target_headers[header] = request.headers[header.lower()]
        
        # 检测是否为TS片段请求
        is_ts_segment = decoded_url.endswith('.ts') or '.ts?' in decoded_url
        
        # 构建会话并携带 driver cookies
        session = requests.Session()
        if CLOUD115_CLIENT is not None and getattr(CLOUD115_CLIENT, 'driver', None) is not None:
            driver_session = CLOUD115_CLIENT.driver.session
            session.headers.update(driver_session.headers)
            session.cookies.update(driver_session.cookies)
            session_headers["User-Agent"] = driver_session.headers.get("User-Agent", session_headers.get("User-Agent", "Mozilla/5.0 115Browser/27.0.5.7"))
        else:
            session_headers["User-Agent"] = "Mozilla/5.0 115Browser/27.0.5.7"

        target_headers["User-Agent"] = session_headers["User-Agent"]

        if cookie_name and cookie_value:
            try:
                session.cookies.set(
                    cookie_name,
                    cookie_value,
                    domain=url_parts.netloc,
                    path=cookie_path or '/',
                    expires=int(cookie_expire) if cookie_expire and cookie_expire.isdigit() else None,
                )
            except Exception as exc:
                logging.warning(f"设置115直链cookie失败: {exc}")

        session.headers.update(session_headers)

        # 发送请求
        response = session.get(
            decoded_url,
            headers=target_headers,
            stream=True,
            timeout=10,
            verify=False
        )
        
        # 检查响应状态
        if response.status_code >= 400:
            logging.error(f"115云盘代理请求失败: HTTP {response.status_code}")
            return jsonify({
                "error": f"Remote server returned HTTP {response.status_code}",
                "url": decoded_url
            }), response.status_code
            
        # 获取内容类型
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        
        # 特殊处理M3U8文件，修改其中的相对URL为代理URL
        if ("application/vnd.apple.mpegurl" in content_type or 
            "application/x-mpegurl" in content_type or 
            decoded_url.endswith(".m3u8") or 
            ".m3u8?" in decoded_url):
            
            logging.info("检测到115云盘M3U8文件，进行处理")
            content = response.text
            processed_content = ""
            
            # 处理每一行
            for line in content.splitlines():
                # 处理注释和空行
                if line.strip() == "" or line.startswith("#"):
                    processed_content += line + "\n"
                    continue
                
                # 对于有效的内容行（通常是视频片段路径）
                # 处理URL
                absolute_url = ""
                if line.startswith("http"):
                    # 已经是绝对URL
                    absolute_url = line
                elif line.startswith("/"):
                    # 以斜杠开头的相对URL（相对于域名根目录）
                    absolute_url = base_domain + line
                else:
                    # 常规相对URL，转换为绝对URL
                    absolute_url = urllib.parse.urljoin(base_url, line)
                
                # 将URL转换为专用115代理URL
                encoded_url = urllib.parse.quote(absolute_url)
                proxy_url = f"/api/cloud115/proxy?url={encoded_url}"
                processed_content += proxy_url + "\n"
                logging.debug(f"115云盘M3U8处理: {line} -> {proxy_url}")
            
            # 创建响应
            proxy_response = Response(
                processed_content,
                status=response.status_code
            )
            
            # 设置内容类型
            proxy_response.headers["Content-Type"] = "application/vnd.apple.mpegurl"
            # 添加缓存控制头，允许短期缓存以提高性能
            proxy_response.headers["Cache-Control"] = "public, max-age=30"
            
        else:
            # 对于TS片段和其他二进制内容
            # 创建响应 - 以块方式流式传输内容
            proxy_response = Response(
                stream_with_context(response.iter_content(chunk_size=8192)),
                status=response.status_code
            )
            
            # 设置内容类型
            if is_ts_segment and "text" in content_type:
                # 强制设置TS片段的内容类型
                proxy_response.headers["Content-Type"] = "video/mp2t"
                # TS 片段可以设置短期缓存以提高性能
                proxy_response.headers["Cache-Control"] = "public, max-age=10"
            else:
                proxy_response.headers["Content-Type"] = content_type
        
        # 设置CORS头
        proxy_response.headers["Access-Control-Allow-Origin"] = "*"
        proxy_response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        proxy_response.headers["Access-Control-Allow-Headers"] = "Origin, X-Requested-With, Content-Type, Accept, Range"
        
        # 复制其他重要的响应头
        for header in ["Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "Etag"]:
            if header in response.headers:
                proxy_response.headers[header] = response.headers[header]
                    
        logging.info(f"115云盘代理流成功: {content_type}")
        return proxy_response

    except Exception as e:
        logging.error(f"115云盘代理流失败: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/cloud115/download_file', methods=['GET', 'OPTIONS'])
def cloud115_download_file_proxy():
    """代理下载115云盘文件（处理认证cookie）"""

    def add_cors_headers(response):
        """添加CORS头到响应"""
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Origin, X-Requested-With, Content-Type, Accept'
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition, Content-Type'
        return response

    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        response = Response()
        return add_cors_headers(response)

    pickcode = request.args.get('pickcode', '').strip()
    file_name = request.args.get('file_name', 'download').strip()

    if not pickcode:
        response = Response("Missing pickcode parameter", status=400)
        return add_cors_headers(response)

    if CLOUD115_CLIENT is None:
        response = Response("115客户端未初始化", status=503)
        return add_cors_headers(response)

    from modules.cloud115_client import Cloud115AuthError, Cloud115RateLimitError

    try:
        # 获取下载信息（包含auth cookie）
        download_info = CLOUD115_CLIENT.get_download_info(pickcode)

        download_url = download_info.get('url')
        if not download_url:
            response = Response("无法获取下载链接", status=500)
            return add_cors_headers(response)

        # 获取认证cookie
        auth_cookie = download_info.get('auth_cookie')

        # 使用CLOUD115_CLIENT的已认证session下载文件
        try:
            resp = CLOUD115_CLIENT.download_file(download_url, auth_cookie)

            if resp.status_code != 200:
                app.logger.error(f"115文件下载失败: HTTP {resp.status_code}")
                response = Response(f"下载失败: HTTP {resp.status_code}", status=resp.status_code)
                return add_cors_headers(response)

            # 获取文件大小和内容类型
            file_size = resp.headers.get('Content-Length', '')
            content_type = resp.headers.get('Content-Type', 'application/octet-stream')

            # 处理文件名编码
            try:
                from urllib.parse import quote
                encoded_filename = quote(file_name.encode('utf-8'))
                content_disposition = f'attachment; filename*=UTF-8\'\'{encoded_filename}'
            except:
                content_disposition = f'attachment; filename="{file_name}"'

            # 创建流式响应
            def generate():
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            response = Response(
                stream_with_context(generate()),
                headers={
                    'Content-Type': content_type,
                    'Content-Disposition': content_disposition,
                    'Cache-Control': 'no-cache',
                }
            )

            if file_size:
                response.headers['Content-Length'] = file_size

            app.logger.info(f"开始代理下载115文件: pickcode={pickcode}, name={file_name}")
            return add_cors_headers(response)

        except requests.RequestException as e:
            app.logger.error(f"115文件下载失败: {str(e)}")
            response = Response(f"下载失败: {str(e)}", status=500)
            return add_cors_headers(response)

    except Cloud115AuthError as e:
        app.logger.error(f"115下载认证失败: {e}")
        response = Response(f"认证失败: {e}", status=401)
        return add_cors_headers(response)
    except Cloud115RateLimitError as e:
        app.logger.error(f"115下载限流: {e}")
        response = Response(f"请求过于频繁: {e}", status=429)
        return add_cors_headers(response)
    except Exception as e:
        app.logger.error(f"115文件下载失败: {str(e)}", exc_info=True)
        response = Response(f"下载失败: {str(e)}", status=500)
        return add_cors_headers(response)

@app.route('/api/cloud115/add_offline_download', methods=['POST'])
def cloud115_add_offline_download():
    """添加115云盘离线下载任务"""
    try:
        # 获取请求参数
        data = request.get_json()
        
        if not data or 'urls' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数：urls'
            })
        
        urls = data.get('urls', '').strip()
        wp_path_id = data.get('wp_path_id', '0')  # 默认保存到根目录
        
        if not urls:
            return jsonify({
                'success': False,
                'message': '离线下载链接不能为空'
            })
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录115云盘'
            })
        
        # 构建请求参数
        params = {
            'urls': urls,
            'wp_path_id': wp_path_id
        }
        
        app.logger.debug(f"Adding 115 offline download task: {params}")
        
        # 发送请求添加离线下载任务
        response = requests.post(
            'https://proapi.115.com/open/offline/add_task_urls',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            data=params
        )
        
        app.logger.debug(f"115 add offline download response status: {response.status_code}")
        
        # 解析响应
        try:
            response_data = response.json()
            app.logger.debug(f"115 add offline download response: {response_data}")
            
            # 检查响应状态
            if response_data.get('state') == 1:
                return jsonify({
                    'success': True,
                    'message': '添加离线下载任务成功',
                    'data': response_data.get('data', {})
                })
            else:
                # 返回错误信息
                error_message = response_data.get('message', '添加离线下载任务失败')
                return jsonify({
                    'success': False,
                    'message': error_message
                })
        except Exception as e:
            app.logger.error(f"Error parsing 115 add offline download response: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'message': f'解析响应失败: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"Error adding 115 offline download task: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'添加离线下载任务失败: {str(e)}'
        })

@app.route('/api/cloud115/offline_tasks', methods=['GET'])
def cloud115_offline_tasks():
    """获取115云盘离线下载任务列表"""
    try:
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录115云盘'
            })

        app.logger.debug("Fetching 115 offline download tasks")

        # 发送请求获取离线任务列表
        response = requests.get(
            'https://proapi.115.com/open/offline/list',
            headers={
                'Authorization': f'Bearer {access_token}'
            },
            timeout=15
        )

        app.logger.debug(f"115 offline tasks response status: {response.status_code}")

        # 解析响应
        try:
            response_data = response.json()
            app.logger.debug(f"115 offline tasks response: {response_data}")

            # 检查响应状态
            if response_data.get('state') == 1:
                data = response_data.get('data', {})
                tasks = []

                # 处理任务列表数据
                # 115的返回格式可能是 {data: {list: [...]}}
                if isinstance(data, dict):
                    task_list = data.get('list', [])
                elif isinstance(data, list):
                    task_list = data
                else:
                    task_list = []

                # 转换任务格式以匹配前端期望
                for task in task_list:
                    task_info = task.get('info', {}) if isinstance(task, dict) else task

                    # 状态映射
                    # 1 = 下载中, 2 = 完成, 3 = 完成(已验证), 4 = 失败
                    status_code = task.get('status', task_info.get('status', 0))
                    status = 'unknown'
                    if status_code == 1:
                        status = 'downloading'
                    elif status_code in (2, 3):
                        status = 'finished'
                    elif status_code == 4:
                        status = 'failed'

                    tasks.append({
                        'name': task.get('name', task_info.get('name', '')),
                        'url': task.get('url', task_info.get('url', '')),
                        'file_size': task.get('file_size', task_info.get('file_size', '')),
                        'percent': task.get('percent', task_info.get('percent', 0)),
                        'status': status,
                        'status_code': status_code,
                        'add_time': task.get('add_time', task_info.get('add_time', ''))
                    })

                return jsonify({
                    'success': True,
                    'data': {
                        'tasks': tasks,
                        'count': len(tasks)
                    }
                })
            else:
                # 返回错误信息
                error_message = response_data.get('message', '获取离线任务列表失败')
                return jsonify({
                    'success': False,
                    'message': error_message
                })
        except Exception as e:
            app.logger.error(f"Error parsing 115 offline tasks response: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'message': f'解析响应失败: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"Error fetching 115 offline tasks: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'获取离线任务列表失败: {str(e)}'
        })

@app.route('/api/clear_all_data', methods=['POST'])
def clear_all_data():
    """API endpoint to clear all database tables"""
    try:
        logging.info("Clearing all database tables")
        
        # Ensure we have a valid database connection for this thread
        db.ensure_connection()
        conn = db.local.conn  # Now should work after ensure_connection is called
        cursor = conn.cursor()
        
        # Get list of all tables in the database
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        # Delete data from each table
        for table in tables:
            table_name = table[0]
            cursor.execute(f"DELETE FROM {table_name}")
        
        # Commit the changes
        conn.commit()
        
        logging.info("All database tables cleared successfully")
        return jsonify({"status": "success", "message": "All database data has been cleared"})
    except Exception as e:
        error_message = f"Failed to clear database data: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/clear_cached_images', methods=['POST'])
def clear_cached_images():
    """API endpoint to clear all cached images"""
    try:
        import shutil
        
        # Define the directories to clear
        cache_dirs = ["buspic/covers", "buspic/actor"]
        
        # Add any movie-specific directories
        for item in os.listdir("buspic"):
            item_path = os.path.join("buspic", item)
            if os.path.isdir(item_path) and item not in ["covers", "actor"]:
                cache_dirs.append(item_path)
        
        # Clear each directory
        cleared_files = 0
        for directory in cache_dirs:
            if os.path.exists(directory):
                logging.info(f"Clearing cached images in {directory}")
                for filename in os.listdir(directory):
                    file_path = os.path.join(directory, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.unlink(file_path)
                            cleared_files += 1
                    except Exception as e:
                        logging.error(f"Error removing {file_path}: {e}")
        
        logging.info(f"Cleared {cleared_files} cached image files")
        return jsonify({"status": "success", "message": f"Cleared {cleared_files} cached image files"})
    except Exception as e:
        error_message = f"Failed to clear cached images: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/clear_logs', methods=['POST'])
def clear_logs():
    """API endpoint to clear all log files"""
    try:
        import os
        
        # Check if logs directory exists
        if not os.path.exists('logs'):
            return jsonify({"status": "success", "message": "No logs directory found"})
        
        # Clear log files
        cleared_files = 0
        for filename in os.listdir('logs'):
            file_path = os.path.join('logs', filename)
            try:
                if os.path.isfile(file_path):
                    # Special handling for webserver.log (the current log file)
                    if filename == 'webserver.log':
                        # Open and truncate the file instead of deleting it
                        with open(file_path, 'w') as f:
                            f.write("Log file cleared at " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                    else:
                        os.unlink(file_path)
                    cleared_files += 1
            except Exception as e:
                logging.error(f"Error clearing log file {file_path}: {e}")
        
        logging.info(f"Cleared {cleared_files} log files")
        return jsonify({"status": "success", "message": f"Cleared {cleared_files} log files"})
    except Exception as e:
        error_message = f"Failed to clear logs: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/strm/sync_category_movie_info', methods=['POST'])
def sync_strm_category_movie_info():
    """同步特定分类下的STRM文件的电影信息"""
    try:
        # 获取请求参数
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少请求数据"}), 400
            
        category = data.get('category', '')
        if not category:
            return jsonify({"status": "error", "message": "缺少分类参数"}), 400
            
        # 创建StrmLibrary实例
        from strm_library import StrmLibrary
        strm_lib = StrmLibrary(db)
        
        # 只同步特定分类的STRM文件
        result = strm_lib.sync_strm_movie_info(category)
        
        return jsonify({
            "status": "success", 
            "updated": result["success"],
            "failed": result["failed"],
            "message": f"成功获取了 {result['success']} 个「{category}」分类下的影片详情，失败 {result['failed']} 个"
        })
    except Exception as e:
        error_message = f"同步STRM文件电影信息失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/strm/sync_all_movie_info', methods=['POST'])
def sync_strm_all_movie_info():
    """同步所有STRM文件的电影信息"""
    try:
        # 创建StrmLibrary实例
        from strm_library import StrmLibrary
        strm_lib = StrmLibrary(db)
        
        # 同步所有STRM文件
        result = strm_lib.sync_strm_movie_info()
        
        return jsonify({
            "status": "success", 
            "updated": result["success"],
            "failed": result["failed"],
            "message": f"成功获取了 {result['success']} 个STRM文件的影片详情，失败 {result['failed']} 个"
        })
    except Exception as e:
        error_message = f"同步STRM文件电影信息失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/cloud115/sync_category_movie_info', methods=['POST'])
def sync_cloud115_category_movie_info():
    """同步特定分类下的115云盘文件的电影信息"""
    try:
        # 获取请求参数
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少请求数据"}), 400
            
        category = data.get('category', '')
        if not category:
            return jsonify({"status": "error", "message": "缺少分类参数"}), 400
            
        # 获取是否只处理没有详情的影片
        only_without_details = data.get('only_without_details', False)
            
        # 创建Cloud115Library实例
        from cloud115_library import Cloud115Library
        cloud115_lib = Cloud115Library(db)
        
        # 只同步特定分类的115云盘文件
        result = cloud115_lib.sync_cloud115_movie_info(category, only_without_details)
        
        message = f"成功获取了 {result['success']} 个「{category}」分类下的115云盘文件影片详情，失败 {result['failed']} 个"
        if only_without_details:
            message = f"成功获取了 {result['success']} 个「{category}」分类下没有详情的115云盘文件影片详情，失败 {result['failed']} 个"
        
        return jsonify({
            "status": "success", 
            "updated": result["success"],
            "failed": result["failed"],
            "message": message
        })
    except Exception as e:
        error_message = f"同步115云盘文件电影信息失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/cloud115/sync_all_movie_info', methods=['POST'])
def sync_cloud115_all_movie_info():
    """同步所有115云盘文件的电影信息"""
    try:
        # 获取是否只处理没有详情的影片
        data = request.get_json() or {}
        only_without_details = data.get('only_without_details', False)
        
        # 创建Cloud115Library实例
        from cloud115_library import Cloud115Library
        cloud115_lib = Cloud115Library(db)
        
        # 同步所有115云盘文件
        result = cloud115_lib.sync_cloud115_movie_info(None, only_without_details)
        
        message = f"成功获取了 {result['success']} 个115云盘文件的影片详情，失败 {result['failed']} 个"
        if only_without_details:
            message = f"成功获取了 {result['success']} 个没有详情的115云盘文件影片详情，失败 {result['failed']} 个"
        
        return jsonify({
            "status": "success", 
            "updated": result["success"],
            "failed": result["failed"],
            "message": message
        })
    except Exception as e:
        error_message = f"同步115云盘文件电影信息失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/cloud115/add_to_library', methods=['POST'])
def cloud115_add_to_library():
    """添加离线下载到115云盘并加入片库"""
    try:
        # 获取请求参数
        data = request.get_json()
        
        if not data or 'urls' not in data:
            return jsonify({
                'success': False,
                'message': '缺少必要参数：urls'
            })
        
        urls = data.get('urls', '').strip()
        movie_info = data.get('movie_info', {})  # 可能包含影片ID等信息
        
        if not urls:
            return jsonify({
                'success': False,
                'message': '离线下载链接不能为空'
            })
        
        # 加载配置
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'config.json')
        config = {}
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            app.logger.error(f"加载配置文件失败: {str(e)}")
            
        # 获取默认文件夹ID，如未配置则默认为根目录(0)
        default_folder_id = "0"
        if 'cloud115' in config and 'default_folder_id' in config['cloud115']:
            default_folder_id = config['cloud115']['default_folder_id']
        
        # 获取库设置
        library_settings = {
            'category': 'other',
            'min_file_size_mb': 50,
            'default_delay_seconds': 8
        }
        if 'cloud115' in config and 'library_settings' in config['cloud115']:
            library_settings.update(config['cloud115']['library_settings'])
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录115云盘'
            })
        
        # 第一步：添加离线下载任务
        app.logger.info(f"开始添加离线下载任务到文件夹 {default_folder_id}")
        # 构建请求参数
        params = {
            'urls': urls,
            'wp_path_id': default_folder_id
        }
        
        # 发送请求添加离线下载任务
        response = requests.post(
            'https://proapi.115.com/open/offline/add_task_urls',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            data=params
        )
        
        app.logger.debug(f"115 add offline download response status: {response.status_code}")
        
        # 解析响应
        try:
            response_data = response.json()
            app.logger.debug(f"115 add offline download response: {response_data}")
            
            # 检查响应状态
            if response_data.get('state') != 1:
                error_message = response_data.get('message', '添加离线下载任务失败')
                return jsonify({
                    'success': False,
                    'message': error_message
                })
                
            # 获取任务信息
            task_info = response_data.get('data', {})
            app.logger.info(f"成功添加离线下载任务: {task_info}")
            
            # 第二步：等待一段时间后将文件添加到库
            # 由于离线下载需要一定时间，我们先返回成功，后台异步处理添加到库的操作
            # 实际应用中可能需要通过轮询或者回调来确认下载完成
            
            # 启动一个后台线程处理后续操作
            task_thread = threading.Thread(
                target=process_offline_download_to_library,
                args=(urls, movie_info, library_settings, default_folder_id)
            )
            task_thread.daemon = True
            task_thread.start()
            
            return jsonify({
                'success': True,
                'message': '已添加离线下载任务，正在处理中',
                'data': {
                    'task_info': task_info,
                    'folder_id': default_folder_id
                }
            })
            
        except Exception as e:
            app.logger.error(f"Error parsing 115 add offline download response: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'message': f'解析响应失败: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"Error adding to 115 library: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'加入片库失败: {str(e)}'
        })

def process_offline_download_to_library(urls, movie_info, library_settings, folder_id):
    """后台处理离线下载添加到库的操作
    
    Args:
        urls: 下载链接
        movie_info: 影片信息字典
        library_settings: 库设置
        folder_id: 文件夹ID
    """
    try:
        # 延迟一段时间等待下载任务创建和开始
        delay_seconds = library_settings.get('default_delay_seconds', 8)
        app.logger.info(f"等待 {delay_seconds} 秒后检查下载状态")
        time.sleep(delay_seconds)
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            app.logger.error("获取token失败，无法继续处理")
            return
        
        # 获取文件夹内容，检查是否有新文件
        # 注意：实际应用中，由于离线下载可能需要很长时间，这里的实现是简化的
        # 更完善的方案应该是定期检查下载状态，或者使用115的回调机制
        
        # 再次延迟一段时间，等待离线下载完成
        # 实际情况可能需要更长时间，这里只是示例
        app.logger.info(f"再次等待 {delay_seconds*2} 秒，确保文件下载")
        time.sleep(delay_seconds * 2)
        
        # 获取文件夹内容
        params = {
            'cid': folder_id,
            'limit': '1150',
            'offset': '0',
            'show_dir': 1,  # 显示目录很重要
            'aid': 1,
            'o': 'upt',  # 按照更新时间排序
            'asc': 0,  # 降序，最新的在前
        }
        
        try:
            response = requests.get(
                'https://proapi.115.com/open/ufile/files',
                params=params,
                headers={
                    'Authorization': f'Bearer {access_token}'
                }
            )
            
            if response.status_code != 200:
                app.logger.error(f"获取文件列表失败: {response.status_code}")
                return
                
            response_data = response.json()
            app.logger.debug(f"115 files response: {response_data}")
            
            # 处理响应数据，兼容两种可能的格式
            files = []
            if isinstance(response_data, list):
                # 如果响应直接是列表
                files = response_data
            elif isinstance(response_data, dict):
                # 如果响应是字典（原先预期的格式）
                if 'data' in response_data:
                    data = response_data.get('data')
                    if isinstance(data, list):
                        files = data
                    elif isinstance(data, dict) and 'data' in data:
                        files = data.get('data', [])
            
            app.logger.info(f"获取到文件列表，共 {len(files)} 项")
            
            if not files:
                app.logger.warning(f"未在文件夹 {folder_id} 中找到任何文件，可能下载尚未完成")
                return
            
            # 首先，检查是否有最近创建的文件夹（可能是离线下载创建的）
            recent_folders = []
            now = time.time()
            for file in files:
                # 检查是否是文件夹
                is_folder = False
                if 'dir' in file and file.get('dir') == 1:
                    is_folder = True
                elif 'fc' in file and file.get('fc') == '0':
                    is_folder = True
                
                if is_folder:
                    # 获取文件夹创建/更新时间
                    update_time = int(file.get('upt', file.get('uet', 0)))
                    time_diff = now - update_time
                    
                    # 如果是最近1分钟内创建/更新的文件夹，很可能是新的离线下载
                    if time_diff < 60:  # 1分钟 = 60秒
                        folder_name = file.get('file_name', file.get('n', file.get('fn', '')))
                        folder_id = file.get('file_id', file.get('fid', ''))
                        app.logger.info(f"发现最近创建的文件夹: {folder_name}, ID: {folder_id}, 创建时间: {update_time}")
                        recent_folders.append(file)
            
            # 如果找到了最近创建的文件夹，则先检查这些文件夹
            processed_files = False
            if recent_folders:
                app.logger.info(f"找到 {len(recent_folders)} 个最近创建的文件夹，将优先处理")
                
                # 对于每个最近的文件夹，获取其内容并处理
                for folder in recent_folders:
                    folder_id = folder.get('file_id', folder.get('fid', ''))
                    folder_name = folder.get('file_name', folder.get('n', folder.get('fn', '')))
                    
                    if not folder_id:
                        app.logger.warning(f"无法获取文件夹ID，跳过此文件夹")
                        continue
                    
                    app.logger.info(f"处理文件夹: {folder_name}, ID: {folder_id}")
                    
                    # 获取此文件夹中的文件
                    folder_params = {
                        'cid': folder_id,
                        'limit': '1150',
                        'offset': '0',
                        'show_dir': 1,
                        'aid': 1,
                    }
                    
                    folder_response = requests.get(
                        'https://proapi.115.com/open/ufile/files',
                        params=folder_params,
                        headers={
                            'Authorization': f'Bearer {access_token}'
                        }
                    )
                    
                    if folder_response.status_code != 200:
                        app.logger.error(f"获取文件夹 {folder_name} 内容失败: {folder_response.status_code}")
                        continue
                    
                    folder_data = folder_response.json()
                    folder_files = []
                    
                    if isinstance(folder_data, list):
                        folder_files = folder_data
                    elif isinstance(folder_data, dict):
                        if 'data' in folder_data:
                            data = folder_data.get('data')
                            if isinstance(data, list):
                                folder_files = data
                            elif isinstance(data, dict) and 'data' in data:
                                folder_files = data.get('data', [])
                    
                    app.logger.info(f"文件夹 {folder_name} 中共有 {len(folder_files)} 个文件")
                    
                    # 如果该文件夹内也存在子文件夹，递归处理一层
                    subfolders = []
                    actual_files = []
                    
                    for item in folder_files:
                        is_dir = False
                        if 'dir' in item and item.get('dir') == 1:
                            is_dir = True
                        elif 'fc' in item and item.get('fc') == '0':
                            is_dir = True
                        
                        if is_dir:
                            subfolders.append(item)
                        else:
                            actual_files.append(item)
                    
                    if subfolders and len(actual_files) == 0:
                        app.logger.info(f"文件夹 {folder_name} 中存在 {len(subfolders)} 个子文件夹，但没有直接文件，将处理子文件夹")
                        
                        for subfolder in subfolders:
                            subfolder_id = subfolder.get('file_id', subfolder.get('fid', ''))
                            subfolder_name = subfolder.get('file_name', subfolder.get('n', subfolder.get('fn', '')))
                            
                            app.logger.info(f"处理子文件夹: {subfolder_name}, ID: {subfolder_id}")
                            
                            subfolder_params = {
                                'cid': subfolder_id,
                                'limit': '1150',
                                'offset': '0',
                                'show_dir': 0,
                                'aid': 1,
                            }
                            
                            subfolder_response = requests.get(
                                'https://proapi.115.com/open/ufile/files',
                                params=subfolder_params,
                                headers={
                                    'Authorization': f'Bearer {access_token}'
                                }
                            )
                            
                            if subfolder_response.status_code != 200:
                                app.logger.error(f"获取子文件夹 {subfolder_name} 内容失败: {subfolder_response.status_code}")
                                continue
                            
                            subfolder_data = subfolder_response.json()
                            subfolder_files = []
                            
                            if isinstance(subfolder_data, list):
                                subfolder_files = subfolder_data
                            elif isinstance(subfolder_data, dict):
                                if 'data' in subfolder_data:
                                    data = subfolder_data.get('data')
                                    if isinstance(data, list):
                                        subfolder_files = data
                                    elif isinstance(data, dict) and 'data' in data:
                                        subfolder_files = data.get('data', [])
                            
                            app.logger.info(f"子文件夹 {subfolder_name} 中共有 {len(subfolder_files)} 个文件")
                            actual_files.extend(subfolder_files)
                    
                    if actual_files:
                        # 过滤出可能的视频文件
                        min_file_size = library_settings.get('min_file_size_mb', 50) * 1024 * 1024  # 转换为字节
                        category = library_settings.get('category', 'other')
                        
                        valid_files = process_files_for_library(actual_files, min_file_size)
                        
                        if valid_files:
                            processed = process_valid_files_to_library(valid_files, category, access_token, movie_info)
                            if processed:
                                processed_files = True
            
            # 如果没有处理到任何文件，则回退到原来的直接查找文件的方法
            if not processed_files:
                app.logger.info("未从新创建的文件夹中处理到文件，尝试直接从原始文件夹中查找")
                
                # 过滤出可能的视频文件
                min_file_size = library_settings.get('min_file_size_mb', 50) * 1024 * 1024  # 转换为字节
                category = library_settings.get('category', 'other')
                
                valid_files = process_files_for_library(files, min_file_size)
                
                if valid_files:
                    process_valid_files_to_library(valid_files, category, access_token, movie_info)
                else:
                    app.logger.warning("未找到符合条件的视频文件")
            
        except Exception as e:
            app.logger.error(f"获取和处理文件列表时出错: {str(e)}", exc_info=True)
            
    except Exception as e:
        app.logger.error(f"后台处理离线下载添加到库时出错: {str(e)}", exc_info=True)

def process_files_for_library(files, min_file_size):
    """处理文件列表，找出符合条件的视频文件
    
    Args:
        files: 文件列表
        min_file_size: 最小文件大小（字节）
        
    Returns:
        list: 符合条件的视频文件列表
    """
    valid_files = []
    for file in files:
        try:
            # 判断是否是文件而非文件夹
            is_folder = False
            if 'dir' in file and file.get('dir') == 1:
                is_folder = True
            elif 'fc' in file and file.get('fc') == '0':
                is_folder = True
            
            if not is_folder:
                # 检查文件大小和类型
                file_size = int(file.get('size', file.get('fs', 0)))
                file_name = file.get('file_name', file.get('n', file.get('fn', '')))
                file_ext = os.path.splitext(file_name)[1].lower()
                
                # 如果是视频文件且大小足够
                if file_size >= min_file_size and file_ext in ['.mp4', '.mkv', '.avi', '.wmv', '.mov', '.flv', '.ts', '.rmvb', '.rm']:
                    valid_files.append(file)
                    app.logger.debug(f"找到有效视频文件: {file_name}, 大小: {file_size} 字节")
        except Exception as e:
            app.logger.error(f"处理文件时出错: {str(e)}")
            continue
    
    app.logger.info(f"找到 {len(valid_files)} 个有效视频文件")
    return valid_files

def process_valid_files_to_library(valid_files, category, access_token, movie_info):
    """处理有效视频文件，添加到115库
    
    Args:
        valid_files: 有效视频文件列表
        category: 分类
        access_token: 115 API访问令牌
        movie_info: 影片信息
        
    Returns:
        bool: 是否成功处理文件
    """
    if not valid_files:
        return False
    
    processed_count = 0
    for file in valid_files:
        try:
            # 提取文件信息，兼容不同的字段名
            file_name = file.get('file_name', file.get('n', file.get('fn', '')))
            pick_code = file.get('pick_code', file.get('pc', ''))
            file_id = file.get('file_id', file.get('fid', ''))
            file_size = int(file.get('size', file.get('fs', 0)))
            
            if not pick_code or not file_id:
                app.logger.error(f"文件 {file_name} 缺少必要的pick_code或file_id")
                continue
            
            app.logger.info(f"开始处理文件: {file_name}, ID: {file_id}, PickCode: {pick_code}")
            
            # 构造正确的115文件URL格式
            file_url = f"https://115.com/?ct=file&ac=view&pickcode={pick_code}"
            app.logger.info(f"使用标准115文件URL: {file_url}")
            
            # 准备文件数据
            file_data = {
                'file_id': file_id,
                'title': file_name,
                'path': file_name,  # 使用文件名作为path
                'size': file_size,
                'category': category,
                'pick_code': pick_code,  # 添加pick_code字段
                'url': file_url,
                'thumbnail': file.get('thumb', ''),
                'description': f"pick_code:{pick_code},size:{file_size}",
                'video_id': movie_info.get('movie_id') if movie_info else None
                
            }
            
            # 使用add_cloud115_file添加文件
            result = add_cloud115_file(file_data)
            
            if not result:
                app.logger.error(f"添加文件 {file_name} 到115云盘库失败")
                continue
            
            app.logger.info(f"成功添加文件 {file_name} 到115云盘库")
            processed_count += 1
            
        except Exception as e:
            app.logger.error(f"处理文件 {file_name if 'file_name' in locals() else 'unknown'} 时出错: {str(e)}")
        
        # 添加延迟，避免API请求过于频繁
        time.sleep(1)
    
    app.logger.info(f"所有文件处理完成，共处理了 {processed_count} 个文件")
    return processed_count > 0

# extract_movie_id_from_filename 函数已移除，现在直接使用movie_info中的movie_id

@app.route('/api/cloud115/find_files_by_movie_id/<movie_id>', methods=['GET'])
def list_cloud115_files_by_movie_id(movie_id):
    """查找115云盘中与指定视频ID匹配的所有文件"""
    try:
        app.logger.info(f"正在查找视频ID为 {movie_id} 的115云盘文件")
        
        # 获取所有云盘文件
        cloud115_files = db.get_cloud115_files()
        
        # 查找匹配指定视频ID的所有文件
        matching_files = [file for file in cloud115_files if file.get('video_id') == movie_id]
        
        if matching_files:
            # 找到匹配的文件，返回文件列表
            app.logger.info(f"为视频ID {movie_id} 找到 {len(matching_files)} 个115云盘文件")
            
            # 整理文件信息，确保包含pickcode
            files_with_details = []
            for file in matching_files:
                # 获取文件大小，优先使用size字段
                file_size = file.get('size', 0)
                
                # 如果size字段为0或不存在，尝试从description中提取
                if not file_size and file.get('description'):
                    description = file.get('description', '')
                    size_match = re.search(r'size:(\d+)', description)
                    if size_match:
                        file_size = int(size_match.group(1))
                
                # 从filepath中提取文件名
                filepath = file.get('filepath', '')
                filename = filepath
                
                # 处理不同的路径情况
                if '/' in filepath:
                    # 对于包含路径的情况，取最后一部分
                    filename = filepath.split('/')[-1]
                elif '\\' in filepath:
                    # 处理Windows风格的路径
                    filename = filepath.split('\\')[-1]
                
                files_with_details.append({
                    'file_id': file.get('id'),
                    'name': filename,  # 使用处理后的文件名
                    'size': file_size,  # 从description提取的大小
                    'pickcode': file.get('pickcode', ''),
                    'category': file.get('category', ''),
                    'path': file.get('filepath', '')
                })
                
            return jsonify({
                'success': True,
                'files': files_with_details
            })
        else:
            # 未找到匹配的文件
            app.logger.warning(f"未找到视频ID为 {movie_id} 的115云盘文件")
            return jsonify({
                'success': False,
                'message': f"未找到视频ID为 {movie_id} 的115云盘文件"
            })
    except Exception as e:
        app.logger.error(f"查找115云盘文件错误: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f"查找115云盘文件时出错: {str(e)}"
        }), 500

def convert_human_size_to_bytes(size_str):
    """将人类可读的文件大小字符串（如'1.44GB'）转换为字节数整数"""
    try:
        if not size_str or not isinstance(size_str, str):
            return 0
            
        # 移除所有空格
        size_str = size_str.strip()
        
        # 匹配数字和单位
        import re
        match = re.match(r'^([\d\.]+)\s*([KMGTP]?B?)$', size_str, re.IGNORECASE)
        if not match:
            return 0
            
        num, unit = match.groups()
        num = float(num)
        unit = unit.upper()
        
        # 转换单位到字节数
        multipliers = {
            'B': 1,
            '': 1,
            'KB': 1024,
            'K': 1024,
            'MB': 1024**2,
            'M': 1024**2,
            'GB': 1024**3,
            'G': 1024**3,
            'TB': 1024**4,
            'T': 1024**4,
            'PB': 1024**5,
            'P': 1024**5
        }
        
        if unit in multipliers:
            return int(num * multipliers[unit])
        return 0
    except Exception as e:
        app.logger.error(f"转换文件大小 '{size_str}' 时出错: {str(e)}")
        return 0

@app.route('/api/cloud115/update_all_file_sizes', methods=['POST'])
def update_all_cloud115_file_sizes():
    """更新所有115云盘文件的大小信息"""
    try:
        # 获取所有云盘文件
        cloud115_files = db.get_cloud115_files()
        total_files = len(cloud115_files)
        updated_count = 0
        
        # 获取有效的token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '无法获取有效的115访问令牌'
            }), 401
        
        app.logger.info(f"开始更新 {total_files} 个115云盘文件的大小信息")
        
        for file in cloud115_files:
            try:
                pickcode = file.get('pickcode')
                file_id = file.get('file_id')
                
                if not pickcode and not file_id:
                    app.logger.warning(f"跳过文件 {file.get('filepath')} - 没有pickcode和file_id")
                    continue
                
                file_size_str = ""
                
                # 首先尝试使用pickcode获取文件信息（这是最准确的方法）
                if pickcode:
                    try:
                        api_url = "https://proapi.115.com/open/file/info"
                        params = {
                            'pick_code': pickcode
                        }
                        headers = {
                            'Authorization': f'Bearer {access_token}'
                        }
                        
                        response = requests.get(api_url, params=params, headers=headers, timeout=10)
                        
                        if response.status_code == 200:
                            try:
                                file_info = response.json()
                                app.logger.debug(f"使用pickcode获取到的文件信息: {file_info}")
                                
                                if file_info.get('state') and isinstance(file_info.get('data'), dict):
                                    file_size_str = file_info['data'].get('size', '')
                                    if file_size_str:
                                        app.logger.info(f"从pickcode API获取到文件 {file.get('filepath')} 的大小: {file_size_str}")
                            except ValueError as e:
                                app.logger.error(f"解析文件 {file.get('filepath')} 的API响应时出错: {str(e)}, 响应内容: {response.text[:100]}")
                    except Exception as e:
                        app.logger.error(f"使用pickcode获取文件 {file.get('filepath')} 的大小时出错: {str(e)}")
                
                # 如果使用pickcode不成功，尝试使用file_id
                if not file_size_str and file_id:
                    try:
                        api_url = "https://proapi.115.com/open/folder/get_info"
                        params = {
                            'file_id': file_id
                        }
                        headers = {
                            'Authorization': f'Bearer {access_token}'
                        }
                        
                        response = requests.get(api_url, params=params, headers=headers, timeout=10)
                        
                        if response.status_code == 200:
                            try:
                                data = response.json()
                                app.logger.debug(f"使用file_id获取到的文件信息: {data}")
                                
                                if data.get('state') and isinstance(data.get('data'), dict):
                                    file_size_str = data['data'].get('size', '')
                                    if file_size_str:
                                        app.logger.info(f"从file_id API获取到文件 {file.get('filepath')} 的大小: {file_size_str}")
                            except ValueError as e:
                                app.logger.error(f"解析文件 {file.get('filepath')} 的API响应时出错: {str(e)}, 响应内容: {response.text[:100]}")
                    except Exception as e:
                        app.logger.error(f"使用file_id获取文件 {file.get('filepath')} 的大小时出错: {str(e)}")
                
                # 记录日志，如果获取文件大小失败
                if not file_size_str:
                    app.logger.warning(f"无法获取文件 {file.get('filepath')} 的大小")
                
                # 更新数据库
                db.local.cursor.execute('''
                UPDATE cloud115_library SET size = ? WHERE id = ?
                ''', (file_size_str, file.get('id')))
                
                if file_size_str:
                    updated_count += 1
                    app.logger.debug(f"已更新文件 {file.get('filepath')} 的大小为 {file_size_str}")
                
                # 添加延迟，避免API请求过于频繁
                time.sleep(0.5)
                
            except requests.exceptions.RequestException as e:
                app.logger.error(f"请求文件 {file.get('filepath')} 信息时网络错误: {str(e)}")
                time.sleep(1)  # 网络错误时增加等待时间
                continue
            except Exception as e:
                app.logger.error(f"更新文件 {file.get('filepath')} 大小时出错: {str(e)}")
                continue
        
        # 提交更改
        db.local.conn.commit()
        
        app.logger.info(f"文件大小更新完成，共更新了 {updated_count}/{total_files} 个文件")
        
        return jsonify({
            'success': True,
            'message': f'成功更新了 {updated_count}/{total_files} 个文件的大小信息'
        })
        
    except Exception as e:
        app.logger.error(f"更新文件大小信息出错: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f"更新文件大小信息时出错: {str(e)}"
        }), 500

# Jellyfin Library routes
@app.route('/jellyfin')
def jellyfin_library_route():
    """Show Jellyfin library page"""
    return redirect(url_for('jellyfin_library'))

@app.route('/jellyfin/library')
def jellyfin_library():
    """Display Jellyfin libraries"""
    # 获取已导入的库列表
    libraries = jellyfin_lib.get_imported_libraries()
    
    return render_template('jellyfin_library.html', 
                          libraries=libraries,
                          page_title="Jellyfin 影片库")

@app.route('/jellyfin/movies')
def jellyfin_movies():
    """Display movies from a Jellyfin library"""
    library_id = request.args.get('library_id')
    page = int(request.args.get('page', 1))
    search = request.args.get('search', '')
    
    # 每页显示的数量
    per_page = 40
    start = (page - 1) * per_page
    
    # 获取电影列表
    result = jellyfin_lib.get_library_movies(
        library_id=library_id,
        start=start,
        limit=per_page,
        search_term=search
    )
    
    movies = result.get('items', [])
    total_count = result.get('total_count', 0)
    
    # 计算总页数
    total_pages = (total_count + per_page - 1) // per_page
    
    # 获取库名称
    library_name = ""
    if library_id:
        libraries = jellyfin_lib.get_imported_libraries()
        for lib in libraries:
            if lib.get('id') == library_id:
                library_name = lib.get('name', '')
                break
    
    return render_template('jellyfin_movies.html',
                          movies=movies,
                          library_id=library_id,
                          library_name=library_name,
                          page=page,
                          total_pages=total_pages,
                          search=search,
                          page_title=f"Jellyfin - {library_name}" if library_name else "Jellyfin 影片")

@app.route('/jellyfin/player/<item_id>')
def jellyfin_player(item_id):
    """Play a movie from Jellyfin"""
    from urllib.parse import urlparse
    t_cfg = CURRENT_CONFIG.get("transcription") or {}
    base = (t_cfg.get("api_base_url") or "").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    parsed = urlparse(base) if base else None
    transcription_ws_host = parsed.hostname if parsed else ""
    transcription_ws_port = str(parsed.port) if parsed and parsed.port else "8001"

    # 从数据库获取影片信息
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM jelmovie WHERE item_id = ?", (item_id,))
    movie = cursor.fetchone()
    conn.close()
    
    if not movie:
        flash("影片不存在或已被删除")
        return redirect(url_for('jellyfin_library'))
    
    # 更新播放计数
    jellyfin_lib.update_play_count(movie['item_id'])
    
    # 将行对象转换为字典
    movie_dict = dict(movie)
    
    # 解析演员JSON
    if movie_dict.get('actors'):
        try:
            movie_dict['actors'] = json.loads(movie_dict['actors'])
        except:
            movie_dict['actors'] = []
    
    # 获取关联的电影详细信息
    movie_detail = None
    jellyfin_meta = {}
    video_id = movie_dict.get('video_id', '')
    app.logger.info(f"Jellyfin文件item_id={item_id}, video_id={video_id}")

    if video_id:
        # 直接查询数据库获取完整记录
        try:
            db.ensure_connection()
            db.local.cursor.execute('''
                SELECT id, cover, date, data 
                FROM movies 
                WHERE id = ?
            ''', (video_id,))
            result = db.local.cursor.fetchone()
            
            if result:
                movie_detail = dict(result)
                app.logger.info(f"找到电影记录: id={movie_detail.get('id')}, cover={movie_detail.get('cover')[:50] if movie_detail.get('cover') else 'None'}, date={movie_detail.get('date')}")
                
                # 解析 data 字段中的 JSON 数据
                if movie_detail.get('data'):
                    try:
                        movie_data = json.loads(movie_detail['data']) if isinstance(movie_detail['data'], str) else movie_detail['data']
                        movie_detail['parsed_data'] = movie_data
                        app.logger.info(f"解析电影数据成功: stars={len(movie_data.get('stars', []))}, genres={len(movie_data.get('genres', []))}")
                    except Exception as e:
                        app.logger.warning(f"解析电影数据失败: {e}")
            else:
                app.logger.warning(f"未找到video_id={video_id}的电影记录")
        except Exception as e:
            app.logger.error(f"获取电影信息失败: {e}", exc_info=True)

    try:
        if not jellyfin_lib.client:
            jellyfin_cfg = CURRENT_CONFIG.get("jellyfin", {}) or {}
            server_url = jellyfin_cfg.get("server_url", "")
            api_key = jellyfin_cfg.get("api_key", "")
            username = jellyfin_cfg.get("username", "")
            password = jellyfin_cfg.get("password", "")
            if server_url and (api_key or (username and password)):
                jellyfin_lib.connect_to_server(server_url, username=username, password=password, api_key=api_key)
        jellyfin_meta = jellyfin_lib.get_item_metadata(item_id) if jellyfin_lib.client else {}
    except Exception as e:
        app.logger.warning(f"读取Jellyfin元数据失败 item_id={item_id}: {e}")

    return render_template('jellyfin_player.html',
                          movie=movie_dict,
                          movie_detail=movie_detail,
                          jellyfin_meta=jellyfin_meta,
                          page_title=movie_dict.get('title', '影片播放'),
                          fwh_ws_host=transcription_ws_host,
                          fwh_ws_port=transcription_ws_port,
                          fwh_model=(CURRENT_CONFIG.get("transcription") or {}).get("model"),
                          fwh_language=(CURRENT_CONFIG.get("transcription") or {}).get("language"),
                          fwh_chunk_secs=(CURRENT_CONFIG.get("transcription") or {}).get("chunk_secs", 4.0),
                          fwh_overlap_secs=(CURRENT_CONFIG.get("transcription") or {}).get("overlap_secs", 0.7),
                          fwh_prefix_chars=(CURRENT_CONFIG.get("transcription") or {}).get("prefix_chars", 0),
                          fwh_segmenter=(CURRENT_CONFIG.get("transcription") or {}).get("segmenter", "vad"),
                          fwh_vad_max_window=(CURRENT_CONFIG.get("transcription") or {}).get("vad_max_window_secs", 15.0),
                          fwh_vad_overlap=(CURRENT_CONFIG.get("transcription") or {}).get("vad_overlap_secs", 0.35),
                          fwh_vad_min_silence=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_silence_secs", 0.4),
                          fwh_vad_min_speech=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_speech_secs", 0.6),
                          fwh_vad_frame=(CURRENT_CONFIG.get("transcription") or {}).get("vad_frame_secs", 0.03),
                          fwh_vad_energy=(CURRENT_CONFIG.get("transcription") or {}).get("vad_energy_threshold", 0.001))

@app.route('/api/jellyfin/connect', methods=['POST'])
def jellyfin_connect():
    """Connect to a Jellyfin server"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少必要参数"}), 400
        
        server_url = data.get('server_url', '')
        username = data.get('username', '')
        password = data.get('password', '')
        api_key = data.get('api_key', '')
        
        if not server_url:
            return jsonify({"status": "error", "message": "服务器URL不能为空"}), 400
        
        # 设置身份验证方式
        if api_key:
            # 使用API密钥连接
            result = jellyfin_lib.connect_to_server(server_url, api_key=api_key)
        elif username and password:
            # 使用用户名和密码连接
            result = jellyfin_lib.connect_to_server(server_url, username=username, password=password)
        else:
            return jsonify({"status": "error", "message": "必须提供API密钥或用户名和密码"}), 400
        
        if result:
            return jsonify({"status": "success", "message": "连接成功"})
        else:
            return jsonify({"status": "error", "message": "连接失败，请检查服务器URL和凭据"}), 400
            
    except Exception as e:
        error_message = f"连接Jellyfin服务器失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/jellyfin/libraries', methods=['GET'])
def jellyfin_libraries():
    """Get Jellyfin libraries"""
    try:
        # 检查是否已连接
        if not jellyfin_lib.client:
            # 尝试使用保存的凭据重新连接
            # 你可能需要从某处加载保存的凭据
            return jsonify({"status": "error", "message": "未连接到Jellyfin服务器"}), 400
        
        libraries = jellyfin_lib.get_libraries()
        return jsonify({
            "status": "success", 
            "libraries": libraries
        })
            
    except Exception as e:
        error_message = f"获取Jellyfin库列表失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/jellyfin/import_library', methods=['POST'])
def jellyfin_import_library():
    """Import a Jellyfin library"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少必要参数"}), 400
        
        library_id = data.get('library_id', '')
        library_name = data.get('library_name', '')
        
        if not library_id or not library_name:
            return jsonify({"status": "error", "message": "库ID和名称不能为空"}), 400

        # 检查是否已连接
        if not jellyfin_lib.client:
            jellyfin_cfg = CURRENT_CONFIG.get("jellyfin", {}) or {}
            server_url = jellyfin_cfg.get("server_url", "")
            api_key = jellyfin_cfg.get("api_key", "")
            username = jellyfin_cfg.get("username", "")
            password = jellyfin_cfg.get("password", "")
            if server_url and (api_key or (username and password)):
                jellyfin_lib.connect_to_server(server_url, username=username, password=password, api_key=api_key)
            if not jellyfin_lib.client:
                return jsonify({"status": "error", "message": "未连接到Jellyfin服务器"}), 400

        # 导入库
        result = jellyfin_lib.import_library(library_id, library_name)
        
        return jsonify({
            "status": "success",
            "message": f"导入完成，共导入 {result['imported']} 个项目，失败 {result['failed']} 个",
            "result": result
        })
            
    except Exception as e:
        error_message = f"导入Jellyfin库失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/jellyfin/sync_library', methods=['POST'])
def jellyfin_sync_library():
    """Incrementally sync a Jellyfin library (by DateCreated)"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少必要参数"}), 400

        library_id = data.get('library_id', '')
        library_name = data.get('library_name', '')

        if not library_id or not library_name:
            return jsonify({"status": "error", "message": "库ID和名称不能为空"}), 400

        if not jellyfin_lib.client:
            jellyfin_cfg = CURRENT_CONFIG.get("jellyfin", {}) or {}
            server_url = jellyfin_cfg.get("server_url", "")
            api_key = jellyfin_cfg.get("api_key", "")
            username = jellyfin_cfg.get("username", "")
            password = jellyfin_cfg.get("password", "")
            if server_url and (api_key or (username and password)):
                jellyfin_lib.connect_to_server(server_url, username=username, password=password, api_key=api_key)

        if not jellyfin_lib.client:
            return jsonify({"status": "error", "message": "未连接到Jellyfin服务器"}), 400

        result = jellyfin_lib.sync_library_incremental_by_date_created(library_id, library_name)
        if result.get("needs_full_import"):
            return jsonify({"status": "error", "message": result.get("message") or "需要先全量导入以初始化增量同步", "result": result}), 400

        return jsonify({
            "status": "success",
            "message": f"增量同步完成，共导入 {result.get('imported', 0)} 个项目，失败 {result.get('failed', 0)} 个",
            "result": result
        })
    except Exception as e:
        error_message = f"增量同步Jellyfin库失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/jellyfin/delete_library', methods=['POST'])
def jellyfin_delete_library():
    """Delete an imported Jellyfin library"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少必要参数"}), 400
        
        library_id = data.get('library_id', '')
        
        if not library_id:
            return jsonify({"status": "error", "message": "库ID不能为空"}), 400
        
        # 删除库
        deleted_count = jellyfin_lib.delete_library(library_id)
        
        return jsonify({
            "status": "success", 
            "message": f"删除完成，共删除 {deleted_count} 个项目",
            "deleted_count": deleted_count
        })
            
    except Exception as e:
        error_message = f"删除Jellyfin库失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500



@app.route('/api/jellyfin/find_files_by_movie_id/<movie_id>', methods=['GET'])
def api_jellyfin_find_files_by_movie_id(movie_id):
    """查找指定电影ID的Jellyfin文件
    
    Args:
        movie_id: 电影ID (video_id)
        
    Returns:
        JSON: 包含文件列表的JSON响应
    """
    try:
        # 初始化Jellyfin库
        jellyfin_lib = JellyfinLibrary(db_file=DB_FILE)
        
        # 查找文件
        files = jellyfin_lib.find_files_by_movie_id(movie_id)
        
        # 构造响应
        if files and len(files) > 0:
            return jsonify({
                "success": True,
                "files": files
            })
        else:
            return jsonify({
                "success": False,
                "message": f"No Jellyfin files found for movie ID {movie_id}",
                "files": []
            })
    except Exception as e:
        app.logger.error(f"Error finding Jellyfin files by movie ID: {e}")
        return jsonify({
            "success": False,
            "message": str(e),
            "files": []
        })

@app.route('/jellyfin_player/file/<file_id>')
def jellyfin_file_player(file_id):
    """从Jellyfin库播放特定文件
    
    Args:
        file_id: jelmovie表中的文件ID
    """
    try:
        from urllib.parse import urlparse
        t_cfg = CURRENT_CONFIG.get("transcription") or {}
        base = (t_cfg.get("api_base_url") or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        parsed = urlparse(base) if base else None
        transcription_ws_host = parsed.hostname if parsed else ""
        transcription_ws_port = str(parsed.port) if parsed and parsed.port else "8001"

        # 从数据库获取文件信息
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM jelmovie WHERE id = ?", (file_id,))
        file = cursor.fetchone()
        conn.close()
        
        if not file:
            flash("文件不存在或已被删除")
            return redirect(url_for('jellyfin_library'))
        
        # 更新播放计数
        jellyfin_lib.update_play_count(file['item_id'])
        
        # 将行对象转换为字典
        file_dict = dict(file)
        
        # 解析演员JSON
        if file_dict.get('actors'):
            try:
                file_dict['actors'] = json.loads(file_dict['actors'])
            except:
                file_dict['actors'] = []
        
        # 获取关联的电影详细信息
        movie_detail = None
        video_id = file_dict.get('video_id', '')
        app.logger.info(f"Jellyfin文件ID={file_id}, video_id={video_id}")
        
        if video_id:
            # 直接查询数据库获取完整记录
            try:
                db.ensure_connection()
                db.local.cursor.execute('''
                    SELECT id, cover, date, data 
                    FROM movies 
                    WHERE id = ?
                ''', (video_id,))
                result = db.local.cursor.fetchone()
                
                if result:
                    movie_detail = dict(result)
                    app.logger.info(f"找到电影记录: id={movie_detail.get('id')}, cover={movie_detail.get('cover')[:50] if movie_detail.get('cover') else 'None'}, date={movie_detail.get('date')}")
                    
                    # 解析 data 字段中的 JSON 数据
                    if movie_detail.get('data'):
                        try:
                            movie_data = json.loads(movie_detail['data']) if isinstance(movie_detail['data'], str) else movie_detail['data']
                            movie_detail['parsed_data'] = movie_data
                            app.logger.info(f"解析电影数据成功: stars={len(movie_data.get('stars', []))}, genres={len(movie_data.get('genres', []))}")
                        except Exception as e:
                            app.logger.warning(f"解析电影数据失败: {e}")
                else:
                    app.logger.warning(f"未找到video_id={video_id}的电影记录")
            except Exception as e:
                app.logger.error(f"获取电影信息失败: {e}", exc_info=True)
        
        return render_template('jellyfin_player.html',
                             movie=file_dict,
                             movie_detail=movie_detail,
                             page_title=file_dict.get('title', 'Jellyfin播放器'),
                             fwh_ws_host=transcription_ws_host,
                             fwh_ws_port=transcription_ws_port,
                             fwh_model=(CURRENT_CONFIG.get("transcription") or {}).get("model"),
                             fwh_language=(CURRENT_CONFIG.get("transcription") or {}).get("language"),
                             fwh_chunk_secs=(CURRENT_CONFIG.get("transcription") or {}).get("chunk_secs", 4.0),
                             fwh_overlap_secs=(CURRENT_CONFIG.get("transcription") or {}).get("overlap_secs", 0.7),
                             fwh_prefix_chars=(CURRENT_CONFIG.get("transcription") or {}).get("prefix_chars", 0),
                             fwh_segmenter=(CURRENT_CONFIG.get("transcription") or {}).get("segmenter", "vad"),
                             fwh_vad_max_window=(CURRENT_CONFIG.get("transcription") or {}).get("vad_max_window_secs", 15.0),
                             fwh_vad_overlap=(CURRENT_CONFIG.get("transcription") or {}).get("vad_overlap_secs", 0.35),
                             fwh_vad_min_silence=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_silence_secs", 0.4),
                             fwh_vad_min_speech=(CURRENT_CONFIG.get("transcription") or {}).get("vad_min_speech_secs", 0.6),
                             fwh_vad_frame=(CURRENT_CONFIG.get("transcription") or {}).get("vad_frame_secs", 0.03),
                             fwh_vad_energy=(CURRENT_CONFIG.get("transcription") or {}).get("vad_energy_threshold", 0.001))
    
    except Exception as e:
        app.logger.error(f"Error playing Jellyfin file: {e}")
        flash(f"播放文件时发生错误: {str(e)}")
        return redirect(url_for('jellyfin_library'))

@app.route('/jellyfin_player/<movie_id>')
def jellyfin_movie_player(movie_id):
    """为特定电影ID查找Jellyfin文件并播放
    
    Args:
        movie_id: 电影ID (video_id)
    """
    try:
        # 初始化Jellyfin库
        jellyfin_lib = JellyfinLibrary(db_file=DB_FILE)
        
        # 查找电影的Jellyfin文件
        files = jellyfin_lib.find_files_by_movie_id(movie_id)
        
        if not files or len(files) == 0:
            flash(f"未找到电影ID为 {movie_id} 的Jellyfin文件")
            return redirect(url_for('movie_detail', movie_id=movie_id))
        
        # 如果找到多个文件，显示文件列表让用户选择
        if len(files) > 1:
            # 获取影片信息
            movie_info = get_movie_data(movie_id)
            return render_template('jellyfin_file_selector.html',
                                 files=files,
                                 movie_id=movie_id,
                                 movie_info=movie_info,
                                 page_title=f"选择Jellyfin文件 - {movie_id}")
        
        # 如果只有一个文件，直接播放
        file_id = files[0]['id']
        return redirect(url_for('jellyfin_file_player', file_id=file_id))
        
    except Exception as e:
        app.logger.error(f"Error finding Jellyfin files for movie: {e}")
        flash(f"查找电影文件时发生错误: {str(e)}")
        return redirect(url_for('movie_detail', movie_id=movie_id))

@app.route('/api/config/jellyfin', methods=['GET'])
def get_jellyfin_config():
    """Get Jellyfin configuration from config.json
    
    Returns:
        JSON: Jellyfin configuration with sensitive information removed
    """
    try:
        # Load config from file
        with open('config/config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        # Return only Jellyfin configuration
        if 'jellyfin' in config:
            # Create a copy to avoid modifying the original
            jellyfin_config = copy.deepcopy(config['jellyfin'])
            
            # Remove sensitive information from the response
            if 'password' in jellyfin_config:
                jellyfin_config['password'] = ''
            
            return jsonify(jellyfin_config)
        else:
            return jsonify({"status": "error", "message": "Jellyfin configuration not found"}), 404
    except Exception as e:
        error_message = f"获取Jellyfin配置失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/jellyfin/authenticate', methods=['POST'])
def jellyfin_authenticate():
    """Authenticate with Jellyfin server and return access token
    
    Returns:
        JSON: Authentication result with access token
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少必要参数"}), 400
        
        server_url = data.get('server_url', '')
        username = data.get('username', '')
        password = data.get('password', '')
        api_key = data.get('api_key', '')
        
        # Check if we have server URL and auth information
        if not server_url:
            return jsonify({"status": "error", "message": "服务器URL不能为空"}), 400
        
        # Initialize Jellyfin client
        client = JellyfinClient()
        client.config.app('BusPre', '1.0.0', 'Web Player', 'buspre-web-player-01')
        client.config.data["auth.ssl"] = server_url.startswith('https')
        
        # Try authentication in this order: 
        # 1. User-provided API key
        # 2. User-provided username/password
        # 3. Config file API key
        # 4. Config file username/password
        
        # 1. Try API key from request
        if api_key:
            try:
                client.authenticate({
                    "Servers": [{
                        "AccessToken": api_key,
                        "address": server_url
                    }]
                }, discover=False)
                
                # Get user info
                user_info = client.jellyfin.get_user()
                
                return jsonify({
                    "status": "success",
                    "access_token": api_key,
                    "user_id": user_info.get('Id', ''),
                    "server_id": '',  # Not needed for API key auth
                    "auth_method": "api_key"
                })
            except Exception as e:
                logging.error(f"API Key认证失败: {str(e)}")
                # Continue to next method
        
        # 2. Try username/password from request
        if username and password:
            try:
                # Connect to server
                client.auth.connect_to_address(server_url)
                
                # Login with username and password
                result = client.auth.login(server_url, username, password)
                if result:
                    # Get credentials
                    credentials = client.auth.credentials.get_credentials()
                    if not credentials:
                        return jsonify({"status": "error", "message": "无法获取认证凭据"}), 500
                    
                    # Get server info
                    server = None
                    if "Servers" in credentials and len(credentials["Servers"]) > 0:
                        server = credentials["Servers"][0]
                    
                    if not server or "AccessToken" not in server:
                        return jsonify({"status": "error", "message": "获取的凭据无效"}), 500
                    
                    # Return token and user information
                    return jsonify({
                        "status": "success",
                        "access_token": server["AccessToken"],
                        "user_id": server.get("UserId", ""),
                        "server_id": server.get("Id", ""),
                        "auth_method": "username_password"
                    })
            except Exception as e:
                logging.error(f"用户名/密码认证失败: {str(e)}")
                # Continue to next method
        
        # 3 & 4. Try config file settings
        try:
            with open('config/config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            if 'jellyfin' in config:
                jellyfin_config = config['jellyfin']
                
                # 3. Try API key from config
                if jellyfin_config.get('api_key'):
                    try:
                        # Create new client instance
                        client = JellyfinClient()
                        client.config.app('BusPre', '1.0.0', 'Web Player', 'buspre-web-player-01')
                        client.config.data["auth.ssl"] = server_url.startswith('https')
                        
                        client.authenticate({
                            "Servers": [{
                                "AccessToken": jellyfin_config['api_key'],
                                "address": server_url
                            }]
                        }, discover=False)
                        
                        # Get user info
                        user_info = client.jellyfin.get_user()
                        
                        return jsonify({
                            "status": "success",
                            "access_token": jellyfin_config['api_key'],
                            "user_id": user_info.get('Id', ''),
                            "server_id": '',
                            "auth_method": "config_api_key"
                        })
                    except Exception as e:
                        logging.error(f"配置文件API Key认证失败: {str(e)}")
                        # Continue to next method
                
                # 4. Try username/password from config
                if jellyfin_config.get('username') and jellyfin_config.get('password'):
                    try:
                        # Create new client instance
                        client = JellyfinClient()
                        client.config.app('BusPre', '1.0.0', 'Web Player', 'buspre-web-player-01')
                        client.config.data["auth.ssl"] = server_url.startswith('https')
                        
                        # Connect to server
                        client.auth.connect_to_address(server_url)
                        
                        # Login with username and password
                        result = client.auth.login(
                            server_url, 
                            jellyfin_config['username'], 
                            jellyfin_config['password']
                        )
                        
                        if result:
                            # Get credentials
                            credentials = client.auth.credentials.get_credentials()
                            if not credentials:
                                return jsonify({"status": "error", "message": "配置文件中的用户名/密码凭据无效"}), 500
                            
                            # Get server info
                            server = None
                            if "Servers" in credentials and len(credentials["Servers"]) > 0:
                                server = credentials["Servers"][0]
                            
                            if not server or "AccessToken" not in server:
                                return jsonify({"status": "error", "message": "配置文件中的用户名/密码获取的凭据无效"}), 500
                            
                            # Return token and user information
                            return jsonify({
                                "status": "success",
                                "access_token": server["AccessToken"],
                                "user_id": server.get("UserId", ""),
                                "server_id": server.get("Id", ""),
                                "auth_method": "config_username_password"
                            })
                    except Exception as e:
                        logging.error(f"配置文件用户名/密码认证失败: {str(e)}")
                        # Fall through to error message
        except Exception as config_error:
            logging.error(f"读取配置文件失败: {str(config_error)}")
        
        # If all authentication methods failed
        return jsonify({
            "status": "error", 
            "message": "认证失败，请提供有效的API Key或用户名/密码"
        }), 401
            
    except Exception as e:
        error_message = f"Jellyfin认证失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

@app.route('/api/jellyfin/playback_info', methods=['POST'])
def jellyfin_playback_info():
    """Get Jellyfin playback information for a media item
    
    Returns:
        JSON: Playback information including transcoding URLs
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "缺少必要参数"}), 400
        
        item_id = data.get('item_id')
        token = data.get('token')
        server_url = data.get('server_url')
        device_profile = data.get('device_profile')
        start_time_ticks = data.get('start_time_ticks', 0)
        user_id = data.get('user_id', '')
        
        if not all([item_id, token, server_url]):
            return jsonify({"status": "error", "message": "必须提供item_id、token和server_url"}), 400
        
        # Initialize client with authentication token
        client = JellyfinClient()
        client.config.app('BusPre', '1.0.0', 'Web Player', 'buspre-web-player-01')
        client.config.data["auth.ssl"] = server_url.startswith('https')
        
        # Authenticate using provided token
        server_address = server_url
        if server_address.endswith('/'):
            server_address = server_address[:-1]
            
        # Determine if we are using API key (shorter) or access token
        is_api_key = len(token) < 64  # API keys are typically shorter than user access tokens
        
        if is_api_key:
            # API key authentication - simpler
            logging.info("使用API Key进行认证")
            client.authenticate({
                "Servers": [{
                    "AccessToken": token,
                    "address": server_address
                }]
            }, discover=False)
        else:
            # Access token authentication - requires more fields
            logging.info("使用访问令牌进行认证")
            client.authenticate({
                "Servers": [{
                    "AccessToken": token,
                    "UserId": user_id,
                    "address": server_address
                }]
            }, discover=False)
        
        # Get playback info with provided device profile for transcoding
        playback_info = client.jellyfin.get_play_info(
            item_id=item_id,
            profile=device_profile,
            start_time_ticks=start_time_ticks,
            is_playback=True
        )
        
        return jsonify(playback_info)
        
    except Exception as e:
        error_message = f"获取Jellyfin播放信息失败: {str(e)}"
        logging.error(error_message)
        return jsonify({"status": "error", "message": error_message}), 500


@app.route('/tools/sha256/<text>', methods=['GET'])
def sha256_base_encoding(text):
    """sha256加密及base64Encode

    Args:
        text: 待加密的字符串 (text)

    Returns:
        JSON: 包含加密完成的JSON响应
    """
    try:
        encrypted = hashlib.sha256(text.encode('utf-8')).digest()
        result = base64.urlsafe_b64encode(encrypted).rstrip(b'=').decode('ascii')
        # 构造响应
        if result and len(result) > 0:
            return jsonify({
                "success": True,
                "result": result
            })
        else:
            return jsonify({
                "success": False,
                "message": f"undone encrypt {text}",
                "result": ""
            })
    except Exception as e:
        app.logger.error(f"error encrypt: {e}")
        return jsonify({
            "success": False,
            "message": str(e),
            "result": ""
        })


# ==================== 播放位置记忆API ====================

@app.route('/api/playback/position', methods=['GET'])
def get_playback_position():
    """获取视频的播放位置"""
    try:
        file_id = request.args.get('file_id', '')
        if not file_id:
            return jsonify({'success': False, 'message': '缺少file_id参数'}), 400

        position_data = db.get_playback_position(file_id)
        if position_data:
            return jsonify({
                'success': True,
                'position': position_data['position'],
                'duration': position_data['duration'],
                'last_played_at': position_data['last_played_at'],
                'title': position_data.get('title'),
                'file_size': position_data.get('file_size')
            })
        else:
            return jsonify({'success': False, 'message': '未找到播放记录'}), 404
    except Exception as e:
        app.logger.error(f"获取播放位置失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/playback/position', methods=['POST'])
def save_playback_position():
    """保存视频的播放位置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '缺少请求体'}), 400

        file_id = data.get('file_id', '')
        file_type = data.get('file_type', 'cloud115')
        position = data.get('position', 0)
        duration = data.get('duration', 0)
        title = data.get('title', '')
        file_size = data.get('file_size', '')

        if not file_id:
            return jsonify({'success': False, 'message': '缺少file_id参数'}), 400

        # 只有当播放位置大于5秒时才保存（避免意外点击产生记录）
        if position < 5:
            return jsonify({'success': True, 'message': '播放时间太短，不保存'})

        if db.save_playback_position(file_id, file_type, position, duration, title, file_size):
            return jsonify({'success': True, 'message': '播放位置已保存'})
        else:
            return jsonify({'success': False, 'message': '保存失败'}), 500
    except Exception as e:
        app.logger.error(f"保存播放位置失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/playback/position/<file_id>', methods=['DELETE'])
def delete_playback_position(file_id):
    """删除视频的播放位置"""
    try:
        if db.delete_playback_position(file_id):
            return jsonify({'success': True, 'message': '播放位置已删除'})
        else:
            return jsonify({'success': False, 'message': '删除失败'}), 500
    except Exception as e:
        app.logger.error(f"删除播放位置失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/playback/positions', methods=['GET'])
def get_all_playback_positions():
    """获取所有播放位置记录（可选按类型筛选）"""
    try:
        file_type = request.args.get('file_type', '')
        limit = request.args.get('limit', '')

        positions = db.get_all_playback_positions(
            file_type=file_type if file_type else None,
            limit=int(limit) if limit.isdigit() else None
        )
        return jsonify({'success': True, 'positions': positions})
    except Exception as e:
        app.logger.error(f"获取播放位置列表失败: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


def _periodic_cleanup_worker():
    """后台线程：定期执行转码任务清理"""
    while True:
        try:
            # 每5分钟执行一次清理
            time.sleep(300)
            TRANSCODE_LOGGER.debug("执行定期清理转码任务")
            _cleanup_transcode_tasks(force=False)
        except Exception as exc:
            TRANSCODE_LOGGER.error(f"定期清理线程出错: {exc}", exc_info=True)
            time.sleep(60)  # 出错后等待1分钟再继续


# Start the server
if __name__ == '__main__':
    # Initialize libraries only once when the app starts
    strm_lib = StrmLibrary(db)
    cloud115_lib = Cloud115Library(db)
    jellyfin_lib = JellyfinLibrary(db_file=DB_FILE)
    
    # 启动定期清理转码任务的后台线程
    cleanup_thread = threading.Thread(target=_periodic_cleanup_worker, daemon=True, name="TranscodeCleanup")
    cleanup_thread.start()
    TRANSCODE_LOGGER.info("转码任务定期清理线程已启动")
    
    # # Create placeholder image for missing covers if it doesn't exist
    # no_cover_path = os.path.join("static", "images", "no-cover.jpg")
    # if not os.path.exists(no_cover_path):
    #     try:
    #         os.makedirs(os.path.dirname(no_cover_path), exist_ok=True)
    #         with open(no_cover_path, 'wb') as f:
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False) 
