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

from flask import Flask, request, jsonify, render_template, redirect, url_for, send_from_directory, Response, stream_with_context, flash
from flask_cors import CORS
from werkzeug.utils import secure_filename
from javbus_db import JavbusDatabase
from modules.translation.translator import get_translator
import logging
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

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 配置较少日志输出的模块
for module in ['urllib3', 'requests', 'werkzeug', 'chardet.charsetprober']:
    logging.getLogger(module).setLevel(logging.WARNING)

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

# 应用过滤器
logger = logging.getLogger()
logger.addFilter(VideoRequestFilter())

# 设置文件日志处理器的最大大小和文件数
if not os.path.exists('logs'):
    os.makedirs('logs')

# 添加按日期滚动的文件处理器
from logging.handlers import TimedRotatingFileHandler
file_handler = TimedRotatingFileHandler(
    'logs/webserver.log', 
    when='midnight',
    interval=1,
    backupCount=3  # 保留3天日志
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
file_handler.setLevel(logging.INFO)
logger.addHandler(file_handler)

# Initialize Flask application
app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)  # Enable CORS

# Add secret key for session
app.secret_key = os.urandom(24)

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

# Initialize database
db = JavbusDatabase(db_file=DB_FILE)
logging.info(f"Using database file: {DB_FILE}")

# Initialize translator
translator = get_translator()

# Load configuration
def load_config():
    """Load configuration file"""
    config = {
        "api_url": "http://192.168.1.246:8922/api",
        "watch_url_prefix": "https://missav.ai",
        "base_url": "https://www.javbus.com",
        "translation": {
            "api_url": "https://api.siliconflow.cn/v1/chat/completions",
            "source_lang": "日语",
            "target_lang": "中文",
            "api_token": "",
            "model": "THUDM/glm-4-9b-chat"
        },
        "cloud115": {
            "default_folder_id": "",
            "library_settings": {
                "category": "other",
                "min_file_size_mb": 200,
                "default_delay_seconds": 5
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
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                config.update(loaded_config)
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
    
    return config

# Get current configuration
CURRENT_CONFIG = load_config()

# 优先使用环境变量中的 API_URL
CURRENT_API_URL = os.environ.get("API_URL", "")
if not CURRENT_API_URL:
    # 如果环境变量未设置，则使用配置文件中的值
    CURRENT_API_URL = CURRENT_CONFIG.get("api_url", "")
    logging.info(f"Using API URL from config file: {CURRENT_API_URL}")
else:
    logging.info(f"Using API URL from environment: {CURRENT_API_URL}")
    # 更新配置文件中的 API URL
    CURRENT_CONFIG["api_url"] = CURRENT_API_URL
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(CURRENT_CONFIG, f, ensure_ascii=False, indent=2)
            logging.info(f"Updated configuration file with API URL from environment")
    except Exception as e:
        logging.error(f"Failed to update configuration file: {str(e)}")

CURRENT_WATCH_URL_PREFIX = CURRENT_CONFIG.get("watch_url_prefix", "https://missav.ai")
CURRENT_BASE_URL = CURRENT_CONFIG.get("base_url", "https://www.javbus.com")

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
        # 构建搜索URL和参数
        search_params = {"page": page}
        
        # Add optional parameters
        if magnet:
            search_params["magnet"] = magnet
        if movie_type:
            search_params["type"] = movie_type
            
        # Determine which API endpoint to use based on parameters
        if keyword:
            # When we have a keyword, use search endpoint
            search_url = f"{CURRENT_API_URL}/movies/search"
            search_params["keyword"] = keyword
        else:
            # When no keyword, use base endpoint
            search_url = f"{CURRENT_API_URL}/movies"
            
            # Add filter parameters if provided (only valid for base endpoint)
            if filter_type and filter_value:
                search_params["filterType"] = filter_type
                search_params["filterValue"] = filter_value
        
        # Call the API
        response = requests.get(search_url, params=search_params)
        
        if response.status_code == 200:
            data = response.json()
            movies_list = data.get("movies", [])
            pagination = data.get("pagination", {})
            
            # 格式化电影列表数据
            formatted_movies = []
            for movie in movies_list:
                # 保存电影的基本信息到数据库，以便影片详情页使用
                # basic_movie_info = {
                #    "id": movie.get("id", ""),
                #    "title": movie.get("title", ""),
                #    "img": movie.get("img", ""),
                #    "date": movie.get("date", ""),
                #    # 对于无码影片，添加标志
                #    "is_uncensored": bool(movie_type == "uncensored" or re.search(r'_\d+$', movie.get("id", "")))
                # }
                
                # 保存基本信息到数据库
                # db.save_movie(basic_movie_info)
                
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
            
            return render_template('search.html', 
                                  keyword_results=formatted_movies,
                                  keyword_query=keyword,
                                  pagination=page_info,
                                  filter_type=filter_type,
                                  filter_value=filter_value,
                                  movie_type=movie_type)
        else:
            logging.error(f"搜索失败: HTTP {response.status_code}")
            return render_template('search.html', 
                                 keyword_query=keyword,
                                 filter_type=filter_type,
                                 filter_value=filter_value,
                                 error_message=f"搜索失败: HTTP {response.status_code}")
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
            response = requests.get(f"{CURRENT_API_URL}/stars/search", params={"keyword": actor_name})
            if response.status_code == 200:
                data = response.json()
                actors = data.get("stars", [])
                
                # Save actors to database
                for actor in actors:
                    db.save_star(actor)
        except Exception as e:
            logging.error(f"Failed to search actor by API: {str(e)}")
    
    # If we found exactly one actor, show their details
    if len(actors) == 1:
        actor = actors[0]
        actor_id = actor.get("id", "")
        
        # Format actor data
        formatted_actor = {
            "name": actor.get("name", ""),
            "image_url": actor.get("avatar", ""),
            "birthdate": actor.get("birthday", ""),
            "age": actor.get("age", ""),
            "height": actor.get("height", ""),
            "measurements": f"{actor.get('bust', '')} - {actor.get('waistline', '')} - {actor.get('hipline', '')}" if actor.get('bust') else "",
            "birthplace": actor.get("birthplace", ""),
            "hobby": actor.get("hobby", "")
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
        if not movie_data.get("magnets") and (movie_data.get("is_uncensored", False) or is_likely_uncensored):
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
        if not formatted_movie.get("magnet_links"):
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
        
        return render_template('movie.html', 
                              movie=formatted_movie, 
                              has_summary=has_summary, 
                              movie_id=movie_id,
                              watch_url_prefix=CURRENT_WATCH_URL_PREFIX)
    else:
        return redirect(url_for('index'))

@app.route('/video_player/<movie_id>')
def video_player(movie_id):
    """Show ad-free video player page"""
    try:
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
        
        return render_template('video_player.html', 
                              movie=formatted_movie,
                              video_url=video_url,
                              hls_url=hls_url,
                              magnet_link=magnet_link,
                              movie_id=movie_id)
    except Exception as e:
        error_message = str(e)
        logging.error(f"Error in video_player route: {error_message}")
        import traceback
        logging.error(traceback.format_exc())
        return render_template('error.html', 
                              error_title="Video Player Error", 
                              error_message=error_message), 500

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
            except Exception as e:
                logging.error(f"Failed to download actor image: {str(e)}")
        
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
            except Exception as e:
                logging.error(f"Failed to download cover image: {str(e)}")
        
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
                                logging.info(f"No full-size image available for sample {sample_index+1} of {movie_id}, using thumbnail instead")
                            
                            if sample_url:
                                download_image(sample_url, file_path)
                                logging.info(f"Downloaded sample image {sample_index+1} for {movie_id}")
                            else:
                                logging.error(f"No image URL found for sample {sample_index+1} of {movie_id}")
                    except (ValueError, IndexError) as e:
                        logging.error(f"Invalid sample index in filename: {image_name}, Error: {str(e)}")
        except Exception as e:
            logging.error(f"Failed to download image: {str(e)}")
    
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
    
    # Try to get from database first with a shorter expiration time to ensure data is fresh
    movie_data = db.get_movie(movie_id, max_age=1)  # 1 day expiration to ensure frequent updates
    
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
                
            response = requests.get(f"{CURRENT_API_URL}/movies/{movie_id}", params=params)
            if response.status_code == 200:
                movie_data = response.json()
                # Save to database
                if not db.save_movie(movie_data):
                    logging.error(f"Failed to save movie data for {movie_id} to database")
                else:
                    logging.info(f"Successfully retrieved and saved complete data for {movie_id}")
                    javbus_api_success = True
            else:
                logging.error(f"API returned status code {response.status_code} for movie {movie_id}")
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
    # Try to get from database first with a shorter expiration time
    actor_data = db.get_star(actor_id, max_age=1)  # 1 day expiration to ensure fresh data
    
    # If not in database, try to get from API
    if not actor_data:
        try:
            response = requests.get(f"{CURRENT_API_URL}/stars/{actor_id}")
            if response.status_code == 200:
                actor_data = response.json()
                # Save to database
                if not db.save_star(actor_data):
                    logging.error(f"Failed to save actor data for {actor_id} to database")
                else:
                    logging.info(f"Successfully retrieved and saved actor data for {actor_id}")
            else:
                logging.error(f"API returned status code {response.status_code} for actor {actor_id}")
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
                response = requests.get(
                    f"{CURRENT_API_URL}/movies",
                    params={
                        "filterType": "star",
                        "filterValue": actor_id,
                        "page": str(page),
                        "magnet": "all"
                    }
                )
                
                if response.status_code != 200:
                    break
                
                data = response.json()
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
                    logging.warning(f"Downloaded content is not an image (Content-Type: {content_type}) from {url}")
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
                    logging.error(f"Downloaded image file is empty or missing: {save_path}")
                    # Continue to retry if this isn't the last attempt
                    if retry < max_retries:
                        continue
            else:
                logging.error(f"Failed to download image from {url}, status code: {response.status_code}")
                # Continue to retry if this isn't the last attempt
                if retry < max_retries:
                    time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                    continue
            
            # If we've reached this point on the last retry, we've failed
            if retry == max_retries:
                return False
                
        except requests.exceptions.Timeout:
            logging.error(f"Timeout while downloading image from {url}")
            if retry < max_retries:
                time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                continue
            return False
        except requests.exceptions.ConnectionError:
            logging.error(f"Connection error while downloading image from {url}")
            if retry < max_retries:
                time.sleep(retry_delay * (2 ** retry))  # Exponential backoff
                continue
            return False
        except Exception as e:
            logging.error(f"Failed to download image from {url}: {str(e)}")
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
        
        return render_template('config.html', config_json=config_json)
    except Exception as e:
        error_message = f"Failed to load configuration file: {str(e)}"
        logging.error(error_message)
        return render_template('config.html', error_message=error_message, config_json="{}")

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
        global CURRENT_CONFIG, CURRENT_API_URL, CURRENT_WATCH_URL_PREFIX, CURRENT_BASE_URL
        CURRENT_CONFIG = config_data
        CURRENT_API_URL = config_data.get("api_url", "")
        CURRENT_WATCH_URL_PREFIX = config_data.get("watch_url_prefix", "https://missav.ai")
        CURRENT_BASE_URL = config_data.get("base_url", "https://www.javbus.com")
        
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
    # 先尝试从数据库获取只包含图片URL的简单记录
    movie_data = db.get_movie(movie_id)
    if movie_data and movie_data.get("img"):
        # 将缩略图URL转换为高清封面图URL
        thumb_url = movie_data.get("img")
        
        # 处理JavBus格式的URL
        if "thumb" in thumb_url:
            # 从缩略图URL中提取ID部分
            # 例如：从 https://www.javbus.com/pics/thumb/b9f2.jpg 提取 b9f2
            thumb_id = thumb_url.split('/')[-1].split('.')[0]
            # 构造高清封面图URL
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
        
        # 构建搜索参数，找出包含此ID的电影
        search_url = f"{CURRENT_API_URL}/movies/search"
        search_params = {"keyword": movie_id, "page": "1"}
        
        response = fetch_with_retry(search_url, search_params)
        if response and response.status_code == 200:
            data = response.json()
            movies_list = data.get("movies", [])
            
            # 查找匹配的电影
            for movie in movies_list:
                if movie.get("id") == movie_id and movie.get("img"):
                    # 将缩略图URL转换为高清封面图URL
                    thumb_url = movie.get("img")
                    
                    # 处理JavBus格式的URL
                    if "thumb" in thumb_url:
                        # 从缩略图URL中提取ID部分
                        thumb_id = thumb_url.split('/')[-1].split('.')[0]
                        # 构造高清封面图URL
                        cover_url = f"{CURRENT_BASE_URL}/pics/cover/{thumb_id}_b.jpg"
                        return cover_url
                        
                    # 处理DMM格式的URL
                    if "pics.dmm.co.jp" in thumb_url and "ps.jpg" in thumb_url:
                        # 将ps.jpg替换为pl.jpg来获取高清封面图
                        cover_url = thumb_url.replace("ps.jpg", "pl.jpg")
                        return cover_url
                        
                    return thumb_url
        
        # 如果搜索没有结果，尝试直接获取电影数据（这是最后的选择）
        # 注意这会获取完整的电影详情，但我们会在最后尝试
        response = fetch_with_retry(f"{CURRENT_API_URL}/movies/{movie_id}")
        if response and response.status_code == 200:
            movie_data = response.json()
            if movie_data and movie_data.get("img"):
                # 将缩略图URL转换为高清封面图URL
                thumb_url = movie_data.get("img")
                
                # 处理JavBus格式的URL
                if "thumb" in thumb_url:
                    # 从缩略图URL中提取ID部分
                    thumb_id = thumb_url.split('/')[-1].split('.')[0]
                    # 构造高清封面图URL
                    cover_url = f"{CURRENT_BASE_URL}/pics/cover/{thumb_id}_b.jpg"
                    # 只保存基础信息到数据库
                    basic_info = {
                        "id": movie_id,
                        "img": cover_url,  # 保存高清封面图URL
                        "title": movie_data.get("title", ""),
                        "date": movie_data.get("date", "")
                    }
                    db.save_movie(basic_info)
                    return cover_url
                    
                # 处理DMM格式的URL
                if "pics.dmm.co.jp" in thumb_url and "ps.jpg" in thumb_url:
                    # 将ps.jpg替换为pl.jpg来获取高清封面图
                    cover_url = thumb_url.replace("ps.jpg", "pl.jpg")
                    # 只保存基础信息到数据库
                    basic_info = {
                        "id": movie_id,
                        "img": cover_url,  # 保存高清封面图URL
                        "title": movie_data.get("title", ""),
                        "date": movie_data.get("date", "")
                    }
                    db.save_movie(basic_info)
                    return cover_url
                    
                # 只保存基础信息到数据库
                basic_info = {
                    "id": movie_id,
                    "img": thumb_url,
                    "title": movie_data.get("title", ""),
                    "date": movie_data.get("date", "")
                }
                db.save_movie(basic_info)
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
        # 获取文件信息
        file_info = db.get_cloud115_file(file_id)
        if not file_info:
            flash('找不到指定的视频文件', 'danger')
            return redirect(url_for('cloud115_library'))
        
        # 更新播放次数
        db.update_cloud115_play_count(file_id)
        
        # 返回播放页面
        return render_template(
            'cloud115_player.html',
            title=file_info.get('title', '未命名视频'),
            file_id=file_id
        )
    except Exception as e:
        app.logger.error(f"Error rendering 115 player page: {str(e)}", exc_info=True)
        flash(f'加载播放器失败: {str(e)}', 'danger')
        return redirect(url_for('cloud115_library'))

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
    """检查115云盘登录状态"""
    try:
        is_valid = is_cloud115_token_valid()
        
        if is_valid:
            return jsonify({
                'state': 1,
                'code': 0,
                'message': '已登录',
                'data': {
                    'logged_in': True
                }
            })
        else:
            return jsonify({
                'state': 1,
                'code': 0,
                'message': '未登录',
                'data': {
                    'logged_in': False
                }
            })
    except Exception as e:
        app.logger.error(f"检查115登录状态错误: {str(e)}")
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'检查登录状态失败: {str(e)}'
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
    
# 数据库辅助方法
def add_cloud115_file(file_data):
    """添加115云盘文件记录到数据库
    
    Args:
        file_data: 文件数据字典，包含必要字段
            - file_id: 115文件ID
            - title: 文件标题
            - path: 文件路径
            - size: 文件大小
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
            'size': file_data['size'],
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

@app.route('/api/cloud115/files', methods=['GET'])
def cloud115_files():
    """获取115云盘文件列表"""
    try:
        # 参数获取
        cid = request.args.get('cid', '0')  # 默认为根目录
        limit = request.args.get('limit', '1150')
        offset = request.args.get('offset', '0')
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'state': 0,
                'code': 401,
                'message': '未授权，请先登录115云盘'
            })
        
        # 构建请求参数
        params = {
            'cid': cid,
            'limit': limit,
            'offset': offset,
            'show_dir': 1,  # 显示目录
            'aid': 1,  # 正常文件
            'o': 'user_utime',  # 按修改时间排序
            'asc': 0,  # 降序
        }
        
        # 发送请求获取文件列表
        response = requests.get(
            'https://proapi.115.com/open/ufile/files',
            params=params,
            headers={
                'Authorization': f'Bearer {access_token}'
            }
        )
        
        app.logger.debug(f"115 files response status: {response.status_code}")
        
        # 解析响应
        try:
            response_data = response.json()
            app.logger.debug(f"115 files response: {response_data}")
            
            # 返回文件列表
            return jsonify({
                'state': 1,
                'code': 0,
                'message': '',
                'data': response_data.get('data', [])
            })
        except Exception as e:
            app.logger.error(f"Error parsing 115 files response: {str(e)}", exc_info=True)
            return jsonify({
                'state': 0,
                'code': 500,
                'message': f'解析文件列表失败: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"Error getting 115 files: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取文件列表失败: {str(e)}'
        })

@app.route('/api/cloud115/folder_info', methods=['GET'])
def cloud115_folder_info():
    """获取115云盘文件夹信息"""
    try:
        # 参数获取
        file_id = request.args.get('file_id')
        
        if not file_id:
            return jsonify({
                'state': 0,
                'code': 400,
                'message': '缺少文件夹ID参数'
            })
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'state': 0,
                'code': 401,
                'message': '未授权，请先登录115云盘'
            })
        
        # 发送请求获取文件夹信息
        response = requests.get(
            'https://proapi.115.com/open/folder/get_info',
            params={'file_id': file_id},
            headers={
                'Authorization': f'Bearer {access_token}'
            }
        )
        
        app.logger.debug(f"115 folder info response status: {response.status_code}")
        
        # 解析响应
        try:
            response_data = response.json()
            app.logger.debug(f"115 folder info response: {response_data}")
            
            # 返回文件夹信息
            return jsonify({
                'state': 1,
                'code': 0,
                'message': '',
                'data': response_data.get('data', {})
            })
        except Exception as e:
            app.logger.error(f"Error parsing 115 folder info response: {str(e)}", exc_info=True)
            return jsonify({
                'state': 0,
                'code': 500,
                'message': f'解析文件夹信息失败: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"Error getting 115 folder info: {str(e)}", exc_info=True)
        return jsonify({
            'state': 0,
            'code': 500,
            'message': f'获取文件夹信息失败: {str(e)}'
        })

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
                            # 检查文件大小是否大于最小值
                            file_size = int(file['fs']) if 'fs' in file else 0
                            
                            if min_size_mb > 0 and file_size < min_size_bytes:
                                skipped_files += 1
                                app.logger.debug(f"跳过小文件：{file['fn']}，大小：{file_size/1024/1024:.2f}MB")
                                continue
                                
                            # 检查文件是否已存在于数据库中
                            if file['fid'] in existing_file_ids:
                                skipped_existing_files += 1
                                app.logger.debug(f"跳过已存在文件：{file['fn']}，ID：{file['fid']}")
                                continue
                                
                            # 获取文件详情，获取正确的pickcode
                            file_details_response = requests.get(
                                'https://proapi.115.com/open/folder/get_info',
                                params={'file_id': file['fid']},
                                headers={
                                    'Authorization': f'Bearer {access_token}'
                                }
                            )
                            
                            file_details = file_details_response.json().get('data', {})
                            pick_code = file_details.get('pick_code', '')
                            
                            video_files.append({
                                'file_id': file['fid'],
                                'title': file['fn'],
                                'path': current_path,
                                'size': file_size,
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

@app.route('/api/cloud115/video_play_url', methods=['GET'])
def cloud115_video_play_url():
    """获取115云盘视频播放地址"""
    try:
        # 获取参数
        file_id = request.args.get('file_id')
        if not file_id:
            return jsonify({
                'success': False,
                'message': '缺少文件ID参数'
            })
        
        # 获取数据库中的文件信息，获取pick_code
        file_info = db.get_cloud115_file(file_id)
        if not file_info:
            return jsonify({
                'success': False,
                'message': '文件不存在'
            })
        
        # 直接使用数据库中的pickcode字段
        pick_code = file_info.get('pickcode')
        
        # 如果pickcode为空，尝试从URL中提取
        if not pick_code:
            # 从URL中提取pick_code
            url = file_info.get('url', '')
            import re
            pick_code_match = re.search(r'pickcode=([^&]+)', url)
            if pick_code_match:
                pick_code = pick_code_match.group(1)
        
        # 如果还是找不到pickcode，尝试使用file_id
        if not pick_code:
            pick_code = file_info.get('file_id')
        
        if not pick_code:
            return jsonify({
                'success': False,
                'message': '无法获取文件的pick_code'
            })
        
        # 获取token
        access_token = get_cloud115_valid_token()
        if not access_token:
            return jsonify({
                'success': False,
                'message': '未授权，请先登录115云盘'
            })
        
        # 请求视频播放地址
        response = requests.get(
            'https://proapi.115.com/open/video/play',
            params={'pick_code': pick_code},
            headers={
                'Authorization': f'Bearer {access_token}'
            }
        )
        
        app.logger.debug(f"115 video play response status: {response.status_code}")
        
        try:
            response_data = response.json()
            app.logger.debug(f"115 video play response: {response_data}")
            
            if response_data.get('state') and response_data.get('data') and 'video_url' in response_data.get('data', {}):
                # 更新播放计数
                db.update_cloud115_play_count(file_id)
                
                # 返回视频播放信息
                return jsonify({
                    'success': True,
                    'data': response_data.get('data'),
                    'title': file_info.get('title', '')
                })
            else:
                error_msg = response_data.get('message') or '获取播放地址失败'
                app.logger.error(f"Error getting 115 video URL: {error_msg}")
                return jsonify({
                    'success': False,
                    'message': error_msg
                })
        except Exception as e:
            app.logger.error(f"Error parsing 115 video play response: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'message': f'解析播放地址失败: {str(e)}'
            })
    except Exception as e:
        app.logger.error(f"Error getting 115 video play URL: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'获取播放地址失败: {str(e)}'
        })

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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://115.com",
            "Referer": "https://115.com/"
        }
        
        # 传递一些重要的请求头
        important_headers = ["Range", "If-Modified-Since", "If-None-Match", "If-Range", "Cache-Control"]
        for header in important_headers:
            if header.lower() in request.headers:
                headers[header] = request.headers[header.lower()]
        
        # 检测是否为TS片段请求
        is_ts_segment = decoded_url.endswith('.ts') or '.ts?' in decoded_url
        
        # 发送请求
        response = requests.get(
            decoded_url,
            headers=headers,
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
    
    # 获取视频ID相关的影片信息
    video_id = movie_dict.get('video_id', '')
    movie_info = None
    
    if video_id:
        movie_info = get_movie_data(video_id)
    
    return render_template('jellyfin_player.html',
                          movie=movie_dict,
                          movie_info=movie_info,
                          page_title=movie_dict.get('title', '影片播放'))

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
        
        # 获取视频ID相关的影片信息
        video_id = file_dict.get('video_id', '')
        movie_info = None
        
        if video_id:
            movie_info = get_movie_data(video_id)
        
        return render_template('jellyfin_player.html',
                             movie=file_dict,
                             movie_info=movie_info,
                             page_title=file_dict.get('title', 'Jellyfin播放器'))
    
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


# Start the server
if __name__ == '__main__':
    # Initialize libraries only once when the app starts
    strm_lib = StrmLibrary(db)
    cloud115_lib = Cloud115Library(db)
    jellyfin_lib = JellyfinLibrary(db_file=DB_FILE)
    
    # # Create placeholder image for missing covers if it doesn't exist
    # no_cover_path = os.path.join("static", "images", "no-cover.jpg")
    # if not os.path.exists(no_cover_path):
    #     try:
    #         os.makedirs(os.path.dirname(no_cover_path), exist_ok=True)
    #         with open(no_cover_path, 'wb') as f:
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False) 