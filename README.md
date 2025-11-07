# BUS115影片库管理系统

**更新2025106**

115浏览器页面。登录后可以查看115网盘文件，播放视频文件和查看图片。

115认证方式增加了cookie。openapi限流严重，文件浏览体验不好。

115视频播放功能增加了alist直链播放和利用cookie认证的直链播放，兼容的mp4格式跳转速度极佳。但不兼容的格式（avi/wmv/hvec等）还是需要切换回115转码播放。

统一并优化播放器界面；修正了有问题的scraper，影片简介中把fanza的用户评论也拉进来了。

**后期更新计划**

· 增加类似alist的播放方式，抛弃alist播放；**（已完成）**

· 集成javbusapi（这个有点麻烦，api的速度没得说，切换到python后端可能会降低效率）。

## 项目简介

这是一个基于Python Flask开发的JAV影片管理系统，提供了视频搜索、元数据管理、在线播放、115网盘集成、Jellyfin媒体库集成和STRM文件库等功能。

## 主要功能

### 1. 影片管理

- 影片搜索与浏览
- 在bus网站提供的元数据基础上加入了多来源的影片简介（强力！）
- bus未收录影片的元数据刮削（kin8等）（强力！）
- 影片标题、简介自动翻译(支持多种翻译API)

### 2. 视频播放

- 支持在线HLS流播放
- 在线网站清洁播放（强力！）、网站播放
- STR片库在线播放
- **统一美观的播放器界面**（强力！）
  - 支持115和Jellyfin片库在线转码播放（自动HLS推流）
  - **支持Alist代理播放模式**，稳定流畅，支持断点续传（需要预先用alist挂载115网盘并配置config对应内容）
  - **支持115直链播放**（类似alist的方法，需要cookie，但不是特别稳）
  - **支持115官方多清晰度播放**（标清/高清/超清/1080P/4K/原画，需要openapi登录）
  - 智能播放模式切换（Alist播放/115直链/115转码）
  - 播放进度保存、清晰度记忆
  - 响应式设计，适配各种设备
- 多清晰度切换

### 3. 115网盘/Jellyfin影片库

- **115网盘文件浏览器**（强力！）
  - 完整的目录浏览和文件管理功能
  - 支持文件搜索、目录切换
  - 实时显示文件大小、修改时间等信息
  - 支持直接播放云盘视频文件
- 视频文件识别与关联
- 数据库方式对影片库的管理
- **双重登录认证方式**（强力！）
  - **OpenAPI扫码登录**：使用115官方API，安全便捷
  - **Cookie登录（115driver）**：绕过OpenAPI限流，高速稳定
    - 解决频繁403限流问题
    - 支持热更新Cookie，无需重启服务
    - 自动故障转移：OpenAPI失败时自动使用Driver
  - **统一登录管理界面**：实时查看登录状态，灵活切换登录方式
- **增加导入Jellyfin影片库功能，和115影片库完美融合，你的一体化终极影片库！**
- **在线播放云盘视频（包括115和Jellyfin，自动转码hls推流）**
  - **Alist播放模式**：通过Alist代理，稳定流畅，支持大文件播放
  - **115官方播放模式**：多清晰度选择，适合115转码视频
  - 智能路径解析：支持Token和Cookie两种认证方式
- **浏览搜索影片一键离线下载并加入片库播放** （强力！）
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
- **115driver集成**：基于逆向工程的115内部API，绕过官方OpenAPI限流
- **Alist集成**：支持通过Alist代理播放115视频，提升稳定性
- **统一播放器架构**：美观的播放界面，支持多种播放源和清晰度切换
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
    "auth_mode": "auto", //Authentication mode: "openapi", "driver", or "auto"
    "token_file": "data/cloud115_token.json", //OpenAPI token storage
    "request_timeout": 15, //Request timeout in seconds
    "driver": {
      "enabled": true, //Enable 115driver client
      "cookie": "", //115 browser cookies (UID=xxx; CID=xxx; SEID=xxx; KID=xxx)
      "cookie_file": "data/cloud115_cookie.txt", //Cookie file path
      "user_agent": "Mozilla/5.0 115Browser/27.0.5.7", //User agent for driver requests
      "timeout": 15, //Driver request timeout
      "api_urls": [
        "https://webapi.115.com/files",
        "http://web.api.115.com/files"
      ], //115 internal API endpoints
      "login_check_interval": 300 //Cookie validity check interval (seconds)
    },
    "library_settings": {
      "category": "other",
      "min_file_size_mb": 50, //Set to 200 to filter small ad videos
      "default_delay_seconds": 5 //Adjust accordingly to reduce idle time
    },
    "alist": {
      "enabled": true, //Enable Alist playback integration
      "base_url": "http://localhost:5244", //Alist server URL
      "root_path": "/115", //115 mount path in Alist
      "username": "", //Alist username (optional)
      "password": "", //Alist password (optional)
      "timeout": 30, //Alist request timeout
      "url_cache_seconds": 300 //Alist URL cache duration
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

### 115云盘配置

`cloud115`配置项用于控制115云盘的所有功能：

#### 基础配置

- `default_folder_id`: 设置默认的115文件夹ID，下载任务将保存到此目录
  - 可以通过115网盘获取文件夹ID，默认为"0"(根目录)
- `auth_mode`: 认证模式
  - `"openapi"`: 仅使用OpenAPI（扫码登录）
  - `"driver"`: 仅使用115driver（Cookie登录）
  - `"auto"`: 自动模式，优先使用driver，失败时自动回退到OpenAPI（推荐）
- `token_file`: OpenAPI token存储文件路径
- `request_timeout`: 请求超时时间（秒）

#### Driver配置（115driver - 解决限流问题）

- `driver.enabled`: 是否启用115driver客户端
- `driver.cookie`: 115浏览器Cookie字符串（格式：`UID=xxx; CID=xxx; SEID=xxx; KID=xxx`）
  - 如何获取Cookie：
    1. 打开115网盘网页版（https://115.com）
    2. 登录你的账号
    3. 打开浏览器开发者工具（F12）
    4. 在Console中运行：`document.cookie`
    5. 复制输出的Cookie字符串，粘贴到配置中
- `driver.cookie_file`: Cookie文件路径（可选，如果配置了cookie字段则优先使用）
- `driver.user_agent`: 请求使用的User-Agent
- `driver.timeout`: Driver请求超时时间
- `driver.api_urls`: 115内部API端点列表（通常无需修改）
- `driver.login_check_interval`: Cookie有效性检查间隔（秒）

**优势**：

- ✅ 绕过OpenAPI的403限流问题
- ✅ 更快的目录浏览和文件操作速度
- ✅ 支持热更新Cookie，无需重启服务
- ✅ 自动故障转移机制

#### Alist播放配置

- `alist.enabled`: 是否启用Alist播放功能
- `alist.base_url`: Alist服务器地址
- `alist.root_path`: 115在Alist中的挂载路径（通常是`/115`）
- `alist.username`: Alist用户名（可选，如果Alist未设置密码则不需要）
- `alist.password`: Alist密码（可选）
- `alist.timeout`: Alist请求超时时间
- `alist.url_cache_seconds`: Alist播放URL缓存时长

**Alist播放优势**：

- ✅ 稳定性更好，支持断点续传
- ✅ 适合大文件播放
- ✅ 网络条件较差时表现更好
- ✅ 可以缓存播放URL，提升响应速度

#### 片库设置

- `library_settings.category`: 文件分类，可选值为"movies"、"tv"或"other"
- `library_settings.min_file_size_mb`: 最小文件大小(MB)，小于此大小的文件将被忽略
- `library_settings.default_delay_seconds`: 添加离线下载后等待的时间(秒)

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
   
   - `api_url`为必选项，否则首页的搜索功能不可用。请通过docker或vercel等方式部署javbus-api。感谢原作者：[ovnrain/javbus-api: 一个自我托管的 JavBus API 服务](https://github.com/ovnrain/javbus-api)
   
   - **115云盘登录配置**（二选一或同时使用）：
     
     - **方式1：OpenAPI扫码登录**（推荐新手）
       1. 启动服务后，访问 `http://localhost:8080/cloud115/login`
       2. 点击"扫码登录"按钮
       3. 使用115手机APP扫描二维码
       4. 登录成功后，Token会自动保存
     - **方式2：Cookie登录**（推荐，解决限流问题）
       1. 在浏览器中打开 https://115.com 并登录
       2. 按F12打开开发者工具，在Console中输入：`document.cookie`
       3. 复制输出的Cookie字符串
       4. 在登录管理页面（`/cloud115/login`）粘贴Cookie并保存
       5. 或者直接编辑`config/config.json`，在`cloud115.driver.cookie`字段中填入Cookie
     - **推荐配置**：同时配置两种方式，设置`auth_mode: "auto"`，系统会自动选择最佳认证方式
   
   - **Alist播放配置**（可选，但强烈推荐）：
     
     1. 确保已部署Alist服务，并将115网盘挂载到Alist
     2. 在`config/config.json`中配置`cloud115.alist`相关参数
     3. 配置后，播放器会自动显示"Alist播放"和"115播放"两个选项

3. 启动服务：
   
   ```bash
   python webserver.py
   ```

### 使用Docker部署：

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

## 使用指南

### 115网盘文件浏览器

1. 访问 `http://localhost:8080/explorer` 进入115网盘文件浏览器
2. 浏览目录结构，点击文件夹进入子目录
3. 点击视频文件可直接播放
4. 顶部显示当前登录状态，点击"登录管理"可进入登录管理页面

### 115登录管理

1. 访问 `http://localhost:8080/cloud115/login` 或从文件浏览器点击"登录管理"
2. 查看当前登录状态：
   - OpenAPI状态：显示Token登录状态
   - Driver状态：显示Cookie登录状态
   - 当前使用方式：显示系统当前使用的认证方式
3. 登录方式：
   - **扫码登录**：点击"开始扫码登录"，使用115手机APP扫描二维码
   - **Cookie登录**：在输入框中粘贴Cookie，选择认证模式，点击"更新Cookie"
4. 热更新：Cookie更新后无需重启服务，立即生效

### 视频播放

1. 从影片库或文件浏览器选择视频文件
2. 播放器自动加载，显示播放模式选择：
   - **Alist播放**：通过Alist代理播放，稳定流畅（推荐）
   - **115播放**：使用115官方HLS流播放，支持多清晰度
3. 如果配置了Alist，播放器默认显示Alist播放选项
4. 如果没有Token但有Cookie，115播放会自动使用Alist原始地址作为"原画"清晰度播放

### 故障排除

#### 115登录问题

- **OpenAPI登录失败**：检查网络连接，确保可以访问115官方API
- **Cookie登录失败**：
  1. 确认Cookie格式正确（包含UID、CID、SEID、KID）
  2. 检查Cookie是否过期（重新获取）
  3. 查看日志文件了解详细错误信息

#### 播放问题

- **无法获取播放地址**：
  1. 检查115登录状态（Token或Cookie至少一个有效）
  2. 如果使用Alist播放，确保Alist服务正常运行
  3. 检查文件路径是否正确（系统会自动尝试解析完整路径）
- **Alist播放失败**：
  1. 确认Alist配置正确（`base_url`、`root_path`）
  2. 检查115是否在Alist中正确挂载
  3. 测试Alist API是否可访问

#### 限流问题

- 如果遇到频繁的403错误，建议：
  1. 启用115driver（Cookie登录）
  2. 设置`auth_mode: "auto"`让系统自动切换
  3. 使用Alist播放模式，减少对115 API的依赖

## 注意事项

- 请确保网络环境科学，或预先在可访问的科学环境中部署可以不科学访问的API
- 建议使用反向代理来保护服务
- 定期备份数据库文件
- 注意配置文件中的敏感信息安全（Cookie、Token等）
- 使用115播放和离线下载功能需要先登录115云盘（Token或Cookie至少一个有效）
- **推荐配置**：同时配置OpenAPI和Driver两种登录方式，启用Alist播放，设置`auth_mode: "auto"`，获得最佳体验和稳定性

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
- **Unified and Beautiful Player Interface** (Powerful!)
  - 115 and Jellyfin Cloud video streaming (auto HLS transcoding)
  - **Alist proxy playback mode** - stable, smooth, supports resume
  - **115 official multi-quality playback** (SD/HD/Full HD/1080P/4K/Original)
  - Smart playback mode switching (Alist/115)
  - Playback progress saving, quality memory
  - Responsive design, adapts to various devices
- Multi-quality switching

### 3. 115 Cloud & Jellyfin Integration

- **115 Cloud File Explorer** (Powerful!)
  - Complete directory browsing and file management
  - File search and directory navigation
  - Real-time file size and modification time display
  - Direct playback of cloud video files
- Video file recognition and metadata association
- Database-driven library management
- **Dual Authentication Methods** (Powerful!)
  - **OpenAPI QR Code Login**: Uses official 115 API, secure and convenient
  - **Cookie Login (115driver)**: Bypasses OpenAPI rate limits, fast and stable
    - Solves frequent 403 rate limiting issues
    - Supports hot-reload cookies without restart
    - Automatic failover: falls back to Driver when OpenAPI fails
  - **Unified Login Management Interface**: Real-time status view, flexible login switching
- ​**​Jellyfin library import feature for seamless integration with 115 Cloud library - your ultimate unified video library!​**​
- ​**​Online cloud video playback (supports 115 Cloud & Jellyfin, auto HLS transcoding)​**
  - **Alist Playback Mode**: Via Alist proxy, stable and smooth, supports large files
  - **115 Official Playback Mode**: Multi-quality selection, suitable for 115 transcoded videos
  - Smart path resolution: supports both Token and Cookie authentication
- ​**​One-click offline download & library integration​**​ (Powerful!)
  - Auto-saves to default directory
  - Auto-adds to 115 Cloud library
  - Auto-extracts video ID for metadata association
  - Ready to play in 10 seconds!

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
- **115driver Integration**: Based on reverse-engineered 115 internal APIs, bypasses official OpenAPI rate limits
- **Alist Integration**: Supports Alist proxy playback for 115 videos, improves stability
- **Unified Player Architecture**: Beautiful playback interface, supports multiple playback sources and quality switching
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
    "auth_mode": "auto", //Authentication mode: "openapi", "driver", or "auto"
    "token_file": "data/cloud115_token.json", //OpenAPI token storage
    "request_timeout": 15, //Request timeout in seconds
    "driver": {
      "enabled": true, //Enable 115driver client
      "cookie": "", //115 browser cookies (UID=xxx; CID=xxx; SEID=xxx; KID=xxx)
      "cookie_file": "data/cloud115_cookie.txt", //Cookie file path
      "user_agent": "Mozilla/5.0 115Browser/27.0.5.7", //User agent for driver requests
      "timeout": 15, //Driver request timeout
      "api_urls": [
        "https://webapi.115.com/files",
        "http://web.api.115.com/files"
      ], //115 internal API endpoints
      "login_check_interval": 300 //Cookie validity check interval (seconds)
    },
    "library_settings": {
      "category": "other",
      "min_file_size_mb": 50, //Set to 200 to filter small ad videos
      "default_delay_seconds": 5 //Adjust accordingly to reduce idle time
    },
    "alist": {
      "enabled": true, //Enable Alist playback integration
      "base_url": "http://localhost:5244", //Alist server URL
      "root_path": "/115", //115 mount path in Alist
      "username": "", //Alist username (optional)
      "password": "", //Alist password (optional)
      "timeout": 30, //Alist request timeout
      "url_cache_seconds": 300 //Alist URL cache duration
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

The `cloud115` configuration controls all 115 Cloud features:

#### Basic Configuration

- `default_folder_id`: Default directory ID for downloads (use "0" for root)
- `auth_mode`: Authentication mode
  - `"openapi"`: Use OpenAPI only (QR code login)
  - `"driver"`: Use 115driver only (Cookie login)
  - `"auto"`: Auto mode, prioritize driver, fallback to OpenAPI on failure (recommended)
- `token_file`: OpenAPI token storage file path
- `request_timeout`: Request timeout in seconds

#### Driver Configuration (115driver - Solves Rate Limiting)

- `driver.enabled`: Enable 115driver client
- `driver.cookie`: 115 browser cookie string (format: `UID=xxx; CID=xxx; SEID=xxx; KID=xxx`)
  - How to get cookies:
    1. Open 115 Cloud web version (https://115.com)
    2. Login to your account
    3. Open browser developer tools (F12)
    4. Run in Console: `document.cookie`
    5. Copy the output cookie string and paste into config
- `driver.cookie_file`: Cookie file path (optional, cookie field takes priority if configured)
- `driver.user_agent`: User-Agent for requests
- `driver.timeout`: Driver request timeout
- `driver.api_urls`: List of 115 internal API endpoints (usually no need to modify)
- `driver.login_check_interval`: Cookie validity check interval (seconds)

**Advantages**:

- ✅ Bypasses OpenAPI 403 rate limiting
- ✅ Faster directory browsing and file operations
- ✅ Supports hot-reload cookies without restart
- ✅ Automatic failover mechanism

#### Alist Playback Configuration

- `alist.enabled`: Enable Alist playback feature
- `alist.base_url`: Alist server address
- `alist.root_path`: 115 mount path in Alist (usually `/115`)
- `alist.username`: Alist username (optional, not needed if Alist has no password)
- `alist.password`: Alist password (optional)
- `alist.timeout`: Alist request timeout
- `alist.url_cache_seconds`: Alist playback URL cache duration

**Alist Playback Advantages**:

- ✅ Better stability, supports resume
- ✅ Suitable for large file playback
- ✅ Better performance under poor network conditions
- ✅ Can cache playback URLs, improves response speed

#### Library Settings

- `library_settings.category`: File category (`movies`, `tv`, or `other`)
- `library_settings.min_file_size_mb`: Minimum file size (MB), files smaller than this will be ignored
- `library_settings.default_delay_seconds`: Wait time after triggering downloads

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
   Edit `config/config.json`. ​**​Required​**​:
   
   - `api_url`: Deploy javbus-api separately. Thanks to the original author: [ovnrain/javbus-api: A self-hosted JavBus API service](https://github.com/ovnrain/javbus-api)
   
   - **115 Cloud Login Configuration** (choose one or both):
     
     - **Method 1: OpenAPI QR Code Login** (recommended for beginners)
       1. After starting the service, visit `http://localhost:8080/cloud115/login`
       2. Click "Start QR Code Login" button
       3. Use 115 mobile APP to scan the QR code
       4. After successful login, token will be automatically saved
     - **Method 2: Cookie Login** (recommended, solves rate limiting)
       1. Open https://115.com in browser and login
       2. Press F12 to open developer tools, enter in Console: `document.cookie`
       3. Copy the output cookie string
       4. Paste cookie in login management page (`/cloud115/login`) and save
       5. Or directly edit `config/config.json`, fill cookie in `cloud115.driver.cookie` field
     - **Recommended Configuration**: Configure both methods, set `auth_mode: "auto"`, system will automatically choose the best authentication method
   
   - **Alist Playback Configuration** (optional, but highly recommended):
     
     1. Ensure Alist service is deployed and 115 Cloud is mounted in Alist
     2. Configure `cloud115.alist` related parameters in `config/config.json`
     3. After configuration, player will automatically display "Alist Playback" and "115 Playback" options

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

## Usage Guide

### 115 Cloud File Explorer

1. Visit `http://localhost:8080/explorer` to enter 115 Cloud file explorer
2. Browse directory structure, click folders to enter subdirectories
3. Click video files to play directly
4. Top bar shows current login status, click "Login Management" to enter login management page

### 115 Login Management

1. Visit `http://localhost:8080/cloud115/login` or click "Login Management" from file explorer
2. View current login status:
   - OpenAPI Status: Shows token login status
   - Driver Status: Shows cookie login status
   - Current Method: Shows currently used authentication method
3. Login methods:
   - **QR Code Login**: Click "Start QR Code Login", use 115 mobile APP to scan QR code
   - **Cookie Login**: Paste cookie in input box, select authentication mode, click "Update Cookie"
4. Hot Reload: Cookie updates take effect immediately without restart

### Video Playback

1. Select video file from library or file explorer
2. Player automatically loads, displays playback mode selection:
   - **Alist Playback**: Via Alist proxy, stable and smooth (recommended)
   - **115 Playback**: Use 115 official HLS stream, supports multiple qualities
3. If Alist is configured, player defaults to Alist playback option
4. If no token but cookie exists, 115 playback will automatically use Alist raw URL as "Original" quality

### Troubleshooting

#### 115 Login Issues

- **OpenAPI login failure**: Check network connection, ensure 115 official API is accessible
- **Cookie login failure**:
  1. Confirm cookie format is correct (contains UID, CID, SEID, KID)
  2. Check if cookie has expired (re-obtain)
  3. Check log files for detailed error information

#### Playback Issues

- **Cannot get playback URL**:
  1. Check 115 login status (at least one of token or cookie must be valid)
  2. If using Alist playback, ensure Alist service is running normally
  3. Check if file path is correct (system will automatically try to resolve full path)
- **Alist playback failure**:
  1. Confirm Alist configuration is correct (`base_url`, `root_path`)
  2. Check if 115 is correctly mounted in Alist
  3. Test if Alist API is accessible

#### Rate Limiting Issues

- If encountering frequent 403 errors, recommend:
  1. Enable 115driver (Cookie login)
  2. Set `auth_mode: "auto"` to let system automatically switch
  3. Use Alist playback mode to reduce dependency on 115 API

## Notes

- Ensure proper network configuration for external API access
- Use reverse proxy for production deployments
- Regularly back up database files
- Protect sensitive configuration data (cookies, tokens, etc.)
- 115 Cloud features require active 115 account login (at least one of token or cookie must be valid)
- **Recommended Configuration**: Configure both OpenAPI and Driver login methods, enable Alist playback, set `auth_mode: "auto"` for best experience and stability

## License

MIT License
