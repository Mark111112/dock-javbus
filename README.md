# BUS115影片库管理系统

## 项目简介

这是一个基于Python Flask开发的JAV影片管理系统，提供了视频搜索、元数据管理、在线播放、115网盘集成、Jellyfin媒体库集成和STRM文件库等功能。

## 主要功能

### 1. 影片管理

- 影片搜索与浏览
- 在bus网站提供的元数据基础上加入了多来源的影片简介（强力！）
- bus未收录影片的元数据刮削（kin8等）（强力！）
- 增加了影片简介爬取功能；由于用户评价也很有趣，所以一起列入
- 影片标题、简介自动翻译(支持多种翻译API)

### 2. 视频播放（统一播放器）

- 支持在线 HLS 流播放
- 在线网站清洁播放（强力！）、网站播放
- STRM 片库在线播放
- 115 与 Jellyfin 片库播放（强力！）：
  - 115片库默认优先使用 Alist 原始直链播放（对 mp4 的定位/快进时延最小，体验更好，需要部署alist挂载115网盘）
  - 当原始直链不可用或需要转码时，可切换到 115 官方的转码 HLS 播放（多清晰度）
  - 未登录115时自动回退 Alist 原画直链（保持可播）
  - 清晰度选择与 UI 统一，可在播放器内一键切换

### 3. 115网盘 / Jellyfin 影片库

- 115云盘文件浏览与视频文件点击播放
- 视频文件识别与关联
- 数据库方式对影片库的管理
- 115 登录管理：扫码登录（OpenAPI）或 Cookie 登录（driver）
- 登录状态自动识别与回退（auto 模式：优先 driver，失败回退 OpenAPI）
- 一键更新 Cookie（热更新，无需重启）
- 增加导入Jellyfin影片库功能，和115影片库完美融合，你的一体化终极影片库！
- 在线播放云盘视频（包括115和Jellyfin，自动转码hls推流）
- 浏览搜索影片一键离线下载并加入片库播放 （强力！）
  - 自动添加到默认目录添加到115网盘影片库、自动提取影片ID并关联元数据
  - 一键后10秒即可播放！

### 4. STRM文件影片库

- STRM文件生成与管理
- 在线播放各来源视频
- 目录自动扫描
- 数据库方式对影片库的管理

## 技术特点

- 基于Flask框架开发
- 使用SQLite数据库存储数据
- 支持多种API接口(JavBus、DMM等)
- 灵活应用多种在线播放可能性
- 支持多种翻译API(OpenAI及兼容接口、Ollama等)
- 实现了完整的缓存机制
- 支持Docker部署

## 系统要求（docker方式部署可跳过）

- Python 3.6+
- 必要的Python包(requirements.txt)
- SQLite3
- 足够的磁盘空间(用于缓存图片和元数据)

## 目录结构

```
.
├── buspic/         # 图片缓存目录
├── config/         # 配置文件目录
├── data/           # 数据文件目录
├── logs/           # 日志目录
├── modules/        # 模块目录
├── static/         # 静态文件
├── templates/      # 模板文件
├── webserver.py    # 主程序
└── requirements.txt # 依赖包列表
```

## 配置说明

系统配置存储在`config/config.json`文件中，主要配置项包括：

```json
{
S  "api_url": "API server address", //Requires valid API from javbus-api
  "watch_url_prefix": "Video playback prefix", //e.g., missav.ai
  "base_url": "Data source URL", //Defaults to javbus.com
  "translation": {
    "api_url": "Translation API URL", //Ollama/OpenAI-compatible endpoint
    "source_lang": "Source language",
    "target_lang": "Target language",
    "api_token": "API key", //Required for non-Ollama APIs
    "model": "Translation model"
  },
  "cloud115": {
    "auth_mode": "auto",            // openapi | driver | auto（推荐）
    "token_file": "data/cloud115_token.json",
    "request_timeout": 15,
    "driver": {                      // 115driver（Cookie 登录）
      "enabled": true,
      "cookie": "UID=...;CID=...;SEID=...;KID=...", // 或留空配合 cookie_file
      "cookie_file": "data/cloud115_cookie.txt",
      "user_agent": "Mozilla/5.0 115Browser/27.0.5.7",
      "timeout": 15,
      "api_urls": [
        "https://webapi.115.com/files",
        "http://web.api.115.com/files"
      ],
      "login_check_interval": 300
    },
    "alist": {                       // Alist 播放直链来源
      "enabled": true,
      "base_url": "http://<alist-host>:5244",
      "root_path": "/115",
      "username": "admin",
      "password": "******",
      "timeout": 30,
      "url_cache_seconds": 300
    },
    "default_folder_id": "0",
    "library_settings": {
      "category": "other",
      "min_file_size_mb": 200,
      "default_delay_seconds": 5
    }
  },
  "jellyfin": {
    "server_url": "Your Jellyfin server URL", //e.g., http://192.168.1.100:8096
    "username": "Your Jellyfin username",
    "password": "Your Jellyfin password",
    "api_key": "Your Jellyfin API key",
    "client_name": "BusPre",
    "client_id": "buspre-web-player",
    "device_name": "Web Browser",
    "device_id": "buspre-web-player-01",
    "transcoding": {
      "enable_auto_transcoding": true,
      "max_streaming_bitrate": 20000000,
      "preferred_video_codec": "h264",
      "preferred_audio_codec": "aac",
      "container": "ts"
    }
  }
}
```

### 115 云盘 / 登录管理

新增统一的 115 登录管理：访问 `/cloud115/login`。

- 查看当前登录状态（OpenAPI Token / driver Cookie）
- 扫码登录（OpenAPI）
- 粘贴 Cookie 登录（driver）
- 认证模式切换：openapi / driver / auto
- Cookie 热更新，无需重启

`cloud115` 配置项说明：

- `auth_mode`: 登录模式（openapi/driver/auto），推荐 `auto`
- `driver.cookie`/`cookie_file`: 浏览器 Cookie（UID/CID/SEID/KID），二选一
- `alist.*`: Alist 服务配置，用于原始直链播放与路径解析
- `default_folder_id`: 115 默认保存目录 ID，默认 `0`（根目录），用于离线下载保存位置
- `library_settings`: 片库导入参数（导入目录时生效）
  - `category`: `movies`/`tv`/`other`
  - `min_file_size_mb`: 小于该值的文件会被忽略
  - `default_delay_seconds`: 发起离线下载后的等待时间

### Jellyfin Configuration

- `server_url`: Your Jellyfin server URL (required for transcoding)
- `username`: Jellyfin account username
- `password`: Jellyfin account password
- `api_key`: API key generated from Jellyfin dashboard
- `transcoding`: Settings for automatic transcoding of unsupported formats
  - `enable_auto_transcoding`: Toggle for auto-transcoding feature
  - `max_streaming_bitrate`: Maximum bitrate for transcoded streams
  - `preferred_video_codec`: Preferred video codec for transcoding (h264 recommended)
  - `preferred_audio_codec`: Preferred audio codec for transcoding
  - `container`: Container format for transcoded streams

## 使用方法

1. 安装依赖：
   
   ```bash
   pip install -r requirements.txt
   ```

2. 配置系统：
   编辑`config/config.json`文件，设置必要的API地址和密钥。其中：
   
   api_url为必选项，否则首页的搜索功能不可用。请通过docker或vercel等方式部署javbus-api。感谢原作者：[ovnrain/javbus-api: 一个自我托管的 JavBus API 服务](https://github.com/ovnrain/javbus-api)

3. 启动服务：
   
   ```bash
   python webserver.py
   ```

## 使用 Docker 部署

```bash
docker run -d -p 9080:8080 \
  -e API_URL=your_api_url \
  -v ./data:/app/data \
  -v ./buspic:/app/buspic \
  -v ./config:/app/confc \
  -v ./logs:/app/logs \
  --name dock-2_javbus furey79:dock-2_javbus
```

```bash
docker-compose up -d
```

## 页面导航

- 登录管理：`/cloud115/login`
- 115 网盘浏览器：`/explorer`（分页浏览、进入子目录、获取文件列表）
- 115 影片库：`/cloud115/library`（分类筛选、搜索、元数据、删除）
- 115 播放页：`/cloud115/player?file_id=<id>` 或从库页进入

## 注意事项

- 请确保网络环境科学，或预先在可访问的科学环境中部署可以不科学访问的API
- 建议使用反向代理来保护服务
- 定期备份数据库文件
- 注意配置文件中的敏感信息安全
- 115 播放在无 Token 时会自动回退 Alist 原画直链（需正确配置 Alist & Cookie）

## License

MIT License

# BUS115 Video Lib Management System

## Project Overview

A JAV video management system developed with Python Flask, offering video search, metadata management, online playback, 115 Cloud integration, Jellyfin Media Lib integration, STRM file library, and more.

## Core Features

### 1. Video Management

- Video search and browsing
- Enhanced multi-source video descriptions (complements BUS website metadata) (Powerful!)
- Metadata scraping for non-BUS indexed videos (e.g., kin8) (Powerful!)
- Automatic title/description translation (supports multiple translation APIs)

### 2. Video Playback

- Online HLS streaming
- Clean ad-free playback from source websites (Powerful!)
- STRM library streaming
- 115 and Jellyfin Cloud video streaming (Powerful!)
- Multi-quality switching

### 3. 115 Cloud & Jellyfin Integration

- Cloud storage browsing and management
- Video file recognition and metadata association
- Database-driven library management
- QR code login (via 115 API, no cookies required)
- ​**​Jellyfin library import feature for seamless integration with 115 Cloud library - your ultimate unified video library!​**​
- ​**​One-click offline download & library integration​**​ (Powerful!)
  - Auto-saves to default directory
  - Auto-adds to 115 Cloud library
  - Auto-extracts video ID for metadata association
  - Ready to play in 10 seconds!
- ​**​Online cloud video playback (supports 115 Cloud & Jellyfin, auto HLS transcoding)​**

### 4. STRM File Library

- STRM file generation and management
- Multi-source video streaming
- Automatic directory scanning
- Database-driven library management

## Technical Highlights

- Built with Flask framework
- SQLite database for data storage
- Multi-API support (JavBus, DMM, etc.)
- Flexible online playback solutions
- Translation API compatibility (OpenAI, Ollama, etc.)
- Comprehensive caching mechanism
- Docker deployment support

## System Requirements (Skip for Docker Deployments)

- Python 3.6+
- Required Python packages (see `requirements.txt`)
- SQLite3
- Sufficient disk space (for image/metadata caching)

## Directory Structure

```
.
├── buspic/         # Image cache
├── config/         # Configuration files
├── data/           # Database files
├── logs/           # Log files
├── modules/        # Core modules
├── static/         # Static assets
├── templates/      # HTML templates
├── webserver.py    # Main entrypoint
└── requirements.txt # Dependencies
```

## Configuration

System settings are stored in `config/config.json`:

```json
{
  "api_url": "API server address", //Requires valid API from javbus-api
  "watch_url_prefix": "Video playback prefix", //e.g., missav.ai
  "base_url": "Data source URL", //Defaults to javbus.com
  "translation": {
    "api_url": "Translation API URL", //Ollama/OpenAI-compatible endpoint
    "source_lang": "Source language",
    "target_lang": "Target language",
    "api_token": "API key", //Required for non-Ollama APIs
    "model": "Translation model"
  },
  "cloud115": {
    "default_folder_id": "0", //Target directory ID (viewable via 115)
    "library_settings": {
      "category": "other",
      "min_file_size_mb": 50, //Set to 200 to filter small ad videos
      "default_delay_seconds": 5 //Adjust accordingly to reduce idle time
    }
  },
  "jellyfin": {
    "server_url": "Your Jellyfin server URL", //e.g., http://192.168.1.100:8096
    "username": "Your Jellyfin username",
    "password": "Your Jellyfin password",
    "api_key": "Your Jellyfin API key",
    "client_name": "BusPre",
    "client_id": "buspre-web-player",
    "device_name": "Web Browser",
    "device_id": "buspre-web-player-01",
    "transcoding": {
      "enable_auto_transcoding": true,
      "max_streaming_bitrate": 20000000,
      "preferred_video_codec": "h264",
      "preferred_audio_codec": "aac",
      "container": "ts"
    }
  }
}
```

### 115 Cloud Configuration

- `default_folder_id`: Default directory for downloads (use "0" for root)
- `library_settings`:
  - `category`: File type (`movies`, `tv`, or `other`)
  - `min_file_size_mb`: Minimum file size (MB) to filter small files
  - `default_delay_seconds`: Wait time after triggering downloads

### Jellyfin Configuration

- `server_url`: Your Jellyfin server URL (required for transcoding)
- `username`: Jellyfin account username
- `password`: Jellyfin account password
- `api_key`: API key generated from Jellyfin dashboard
- `transcoding`: Settings for automatic transcoding of unsupported formats
  - `enable_auto_transcoding`: Toggle for auto-transcoding feature
  - `max_streaming_bitrate`: Maximum bitrate for transcoded streams
  - `preferred_video_codec`: Preferred video codec for transcoding (h264 recommended)
  - `preferred_audio_codec`: Preferred audio codec for transcoding
  - `container`: Container format for transcoded streams

## Usage

1. Install dependencies:
   
   ```bash
   pip install -r requirements.txt
   ```

2. Configure system:  
   Edit `config/config.json`. ​**​Required​**​:
   
   - `api_url`: Deploy javbus-api separately

3. Start service:
   
   ```bash
   python webserver.py
   ```

### Docker Deployment

```bash
docker run -d -p 9080:8080 \
  -e API_URL=your_api_url \
  -v ./data:/app/data \
  -v ./buspic:/app/buspic \
  -v ./config:/app/config \
  -v ./logs:/app/logs \
  --name bus115-system furey79/bus115-system
```

```bash
docker-compose up -d
```

## Notes

- Ensure proper network configuration for external API access
- Use reverse proxy for production deployments
- Regularly back up database files
- Protect sensitive configuration data
- 115 Cloud features require active 115 account login

## License

MIT License
