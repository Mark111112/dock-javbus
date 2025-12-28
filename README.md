# BUS115 影片库管理系统使用说明 / BUS115 Usage Guide

UPDATE 20251228

  播放器

  - 修复了全屏后无法显示转录字幕的问题
  - 自定义控件 - 隐藏原生控件，添加自定义全屏按钮，统一了除Jellyfin Player以外的播放控件
  - 播放位置记忆 - 恢复播放进度保存/恢复功能，进入播放器时询问是否跳转

  115 Explorer

  - 磁力离线 - 不希望启动115网盘的时候凑合用
  - 下载功能 - 不希望启动115网盘的时候凑合用
  - 删除功能 - 不希望启动115网盘的时候凑合用
  - 移动功能 - 不希望启动115网盘的时候凑合用

---

## 一、应用整体功能（中文）

BUS115 是一个在本机或私有服务器上运行的 **网页影片库管理与播放系统**。  
它可以把散落在各处的 JAV 资源（115、Jellyfin、STRM等）集中起来统一管理：

- 通过 **番号 / 影片 ID / 关键字** 搜索影片  
- 自动从多个网站抓取 **封面、截图、简介、演员、标签** 等元数据  
- 整合多个片源：
  - **115 网盘**（文件浏览、在线播放、离线下载后一键入库）
  - **本地 / 远程 STRM 文件库**
  - **Jellyfin 媒体库**
- 使用统一的播放器界面，在浏览器中直接播放：
  - 正常 HTTP / HLS 流
  - 115 官方播放（多清晰度）
  - 115 直链播放（可配合本地转码，重点见下文）
  - 通过 Alist 的代理播放
- **实时字幕 & 译文**（依赖外部 Faster Whisper Service，推荐 GPU 部署）
- 影片标题与简介翻译（需要配置翻译服务）
- 收藏夹、最近影片等便捷入口

---

## 二、Web 界面主要入口

打开浏览器访问：`http://服务器IP:8080`（本机默认是 `http://localhost:8080`）。

常用入口：

- **首页**：最近添加/更新的影片简要列表
- **搜索页**：顶部搜索框可以输入番号或关键字
- **影片详情页**：展示单部影片完整信息与可用片源
- **115 片库**：浏览 115 网盘中的目录与文件
- **STRM 片库**：浏览本地 STRM 文件映射的片库
- **Jellyfin 片库**：将 Jellyfin 中的影片呈现在同一个界面中
- **收藏夹**：管理自己收藏的影片列表

---

## 三、搜索与影片详情

### 3.1 搜索影片

1. 在顶部输入框中输入 **番号或关键字**：
   - 例如：`SSIS-406`、`ABW-123`、`HEYZO-1234` 等
2. 点击搜索后，系统会：
   - 先从本地数据库中查找是否已有记录
   - 若没有，则尝试从配置好的数据源（如 JavBus / FANZA / DMM 等）抓取信息并缓存

如果搜索的是演员，在搜索结果里也会出现演员信息及其相关影片列表。

### 3.2 影片详情页

点击任意一部影片，可以进入详情页，通常包括：

- 标题 / 番号 / 日期 / 厂牌 / 系列 等基本信息
- 原始封面与样本截图
- 影片简介（summary）与中文译文（如已配置翻译）
- 演员列表与标签
- 可用片源：
  - STRM 文件
  - 115 网盘文件
  - Jellyfin 媒体
- 收藏按钮：可以把影片加入收藏夹

在简介区域，可以展开/收起原文与译文，并通过按钮触发翻译或刷新译文。

---

## 四、115 网盘功能与 115 直链转码播放（重点）

115 集成是本应用的一大核心，包含 **文件管理** 和 **多种播放方式**，尤其是 **115 直链 + 本地转码**。

### 4.1 登录与基础配置

要使用 115 功能，需要先在配置中完成登录信息设置（详情见原始 `readme.md`），通常包括：

- 选择认证模式：
  - 使用 115 官方 OpenAPI（扫码登录）
  - 使用浏览器复制的 Cookie（通过“driver”模式访问）
  - 或自动模式：优先使用 Cookie，失败时回退到 OpenAPI
- 可选：配置 Alist，用于「Alist 播放」模式  
- 确保运行环境中已安装 `ffmpeg` / `ffprobe`（Docker 镜像中已包含）

完成配置并重启服务后，即可在 Web 界面使用 115 片库与播放功能。

### 4.2 115 文件浏览与一键播放

1. 在导航栏中进入 **115 片库** 页面。  
2. 可以像在 115 官方网页中一样：
   - 浏览文件夹、切换目录
   - 查看文件大小、修改时间等信息
   - 搜索文件（按名称）
3. 对于识别为视频文件的条目，点击后会进入 **115 播放器页面**。

### 4.3 115 播放器页面的三种播放模式

在 115 播放器页面底部，你会看到 **“播放模式切换”** 按钮组：

- **Alist 播放**
  - 前提：已在配置中正确设置 Alist 服务器并挂载了 115 网盘。
  - 作用：通过 Alist 提供的直链进行播放，适合大文件、断点续传场景。

- **115 直链**
  - 利用你的 115 登录信息，从服务器端获取 **直接下载 URL**。
  - 适合浏览器原生支持的格式（MP4 等），在不转码的前提下速度非常快。
  - 若结合本地转码功能，可在不依赖 115 官方播放器的情况下播放更多编码/封装格式。

- **115 播放**
  - 调用 115 官方的在线播放接口，支持多清晰度切换（标清 / 高清 / 超清 / 1080P / 4K 等）。
  - 对已转码到 115 官方格式的视频最为稳妥。

当前使用中的模式会在 **“播放信息”** 区域的「播放模式」字段中显示，如：

- `Alist 播放`
- `115 直链播放`
- `115 播放`

### 4.4 115 直链 + 本地转码播放（重点说明）

很多视频在 115中的原始封装或编码并不完全适合浏览器直接播放，  
或者你希望 **不依赖 115 官方转码**、直接用本机服务器以更灵活的方式转码。  
这时可以使用 BUS115 的 **115 直链转码** 功能。

#### 4.4.1 工作方式（用户视角）

1. 在 115 片库中点击视频，进入 **115 播放器页面**。
2. 在「播放模式切换」中点击 **“115 直链”**：
   - 系统会根据当前文件的封装 / 编码情况，判断是否需要转码：
     - 如果认为可以直接播放，则直接使用 115 提供的下载 URL 播放；
     - 如果需要转码，或你手动触发转码，系统会：
       - 通过已配置的 115 登录信息获取该文件的直链下载地址；
       - 在服务器上启动一个转码任务（使用 ffmpeg），将直链流式转为 **HLS 分片**；
       - 播放器会使用一个自定义的进度条播放这个 HLS 流。
3. 页面底部会显示转码相关状态信息，例如：
   - “正在请求转码服务…”
   - “转码处理中，请稍候…”
   - 转码完成后自动开始播放，并更新总时长、缓冲进度等信息。

整个过程对用户是透明的：  
你只需要选择「115 直链」，其余由系统自动决定是 **直接播放**，还是 **通过本地转码播放**。

#### 4.4.2 自定义进度条与转码进度

当使用 115 直链转码播放时：

- 播放器会隐藏浏览器原生进度条，启用自定义控制条；
- 自定义进度条可以显示：
  - 已播放部分
  - 已转码但尚未播放的缓冲部分
  - 还未转码的部分在进度条上会有明显界限
- 你可以拖动进度条进行快进，播放器会根据当前转码状态决定是否立即跳转或等待转码。

#### 4.4.3 转码任务管理

在 115 播放器页面中，还有一组 **“转码控制”** 按钮：

- **停止转码**：对当前任务发送停止指令（适合误触或不再需要转码时）
- **转码管理**：跳转到 `/cloud115/transcode/tasks` 管理页面

在 **转码管理页面** 中，你可以：

- 查看所有当前/历史转码任务
- 查看每个任务的状态、文件名、开始时间、最近更新时间、时长、进程 ID 等
- 对任务执行：
  - 停止
  - 删除（包括清理临时文件）

> 小提示：如果经常使用 115 直链转码播放，建议定期检查转码任务列表，清理不再需要的历史任务和临时文件，以节省磁盘空间。

#### 4.4.4 使用前提和建议

为了顺利使用 115 直链转码功能，建议确保：

- 服务端能访问 115（网络与账号状态正常）
- 配置中已填入有效的 115 Cookie 或已完成 OpenAPI 登录
- 环境中有可用的 `ffmpeg` / `ffprobe`（Docker 镜像已经预装）
- 磁盘空间足够用于存储转码产生的缓存文件

如果转码启动失败，页面状态栏通常会给出简单的错误提示，可据此排查配置问题。

---

## 五、STRM 与 Jellyfin 片库用法

### 5.1 STRM 片库

STRM 文件是一种「链接占位符」，文件本身只有一个播放 URL，而不保存视频内容。  
BUS115 可以把 STRM 文件当作影片条目来统一管理和播放。

典型步骤：

1. 在配置中指定 STRM 根目录；
2. 将包含番号的 STRM 文件放在对应路径；
3. 在 Web 界面的 **STRM 片库** 中浏览和播放；
4. 系统会尝试根据 STRM 文件名匹配对应的影片元数据（例如番号），并在详情页中展示。

### 5.2 Jellyfin 片库

如果你已经在运行 Jellyfin，可以在 BUS115 中配置：

- Jellyfin 服务器地址（URL）
- 用户名 / 密码
- API Key

配置完成后，在 BUS115 中可以：

- 浏览 Jellyfin 中的影片列表；
- 在影片详情页中看到来自 Jellyfin 的片源入口；
- 尝试通过 Jellyfin 的转码能力提供 HLS 流，在 BUS115 的统一播放器中观看。

---

## 六、翻译功能

在影片详情页，如果系统检测到尚未生成译文，会显示「翻译」按钮：

- 点击后，系统会调用配置好的翻译服务（如 OpenAI 接口、Ollama 等）：
  - 翻译标题
  - 翻译影片简介
- 翻译结果会缓存到数据库，下次访问同一影片时直接读取。

你也可以使用「刷新翻译」按钮来重新生成译文（比如更新到了更好的模型）。

---

## 六.1 实时字幕 / 译文（新增）

- 前端播放器（影片页、115 播放页）提供 **“实时字幕”** 按钮开启音频转写，并有独立 **“译文”** 开关决定是否显示翻译。
- 后端通过 **外部 Faster Whisper Service** 的 WebSocket 接口完成转写；该服务不在本仓库内，需自行部署（推荐 GPU 环境，接口通常在 `ws://host:8001/ws/realtime`）。
- 配置要求：
  - `config/config.json` 的 `transcription` 小节选择 `provider: "fwhisper"`，并设置 `api_base_url`/`ws_url` 指向你的 Faster Whisper Service。
  - `translation` 小节配置翻译 API（例如 SiliconFlow/OpenAI/Ollama 等，如果要翻译实时字幕强烈建议配置本地ollama），用于实时字幕的译文输出；未开启“译文”开关时仅显示原文。
- 性能注意：
  - 实时字幕依赖外部服务算力，GPU 环境强烈推荐；翻译默认关闭以避免拖慢字幕显示，打开“译文”开关后才会请求翻译。
  - 若转写或翻译服务不可达，会先显示原文或空白，并在下一次消息恢复后继续。

---

## 七、启动与初次配置（简要）

### 7.1 Docker 方式

1. 安装 Docker 与 docker‑compose  
2. 在项目根目录执行：

```bash
docker-compose up -d
```

3. 默认浏览器访问：`http://localhost:8080`

### 7.2 本地 Python 方式

1. 安装 Python 3.9+ 与 ffmpeg  
2. 在项目根目录执行：

```bash
pip install -r requirements.txt
python webserver.py
```

3. 浏览器访问 `http://localhost:8080`

### 7.3 初次配置建议

1. 打开 `config/config.json`，至少检查：
   - JavBus / 数据源 URL 是否可访问
   - 115 配置（认证方式、默认目录）
   - 是否启用 Alist（如有）
   - 翻译服务配置（可选）
2. 修改后重启服务，使配置生效。

---

## 八、常见使用问题（简短 FAQ）

- **115 直链/转码按钮是灰色的或不可用？**  
  - 检查是否已经配置 115 登录信息；  
  - 确认当前文件是否从 115 片库页面进入，而不是其他来源。

- **点击 115 直链后提示无法获取直链或转码启动失败？**  
  - 检查 Cookie 是否过期，或 OpenAPI 是否仍然有效；  
  - 服务器是否能直接访问 115 网站；  
  - 是否安装了 `ffmpeg` / `ffprobe`。

- **115 官方播放卡顿或画质不理想？**  
  - 可以尝试改用「Alist 播放」或「115 直链（配合转码）」模式。

---

## 九、JavBus 数据源模式（内部 Scraper / 外部 API）

本应用的「番号搜索 / 影片详情」主要依赖 JavBus 数据，可以通过两种方式获取：

1. **内部 Scraper 模式（默认）**
   - 配置示例（`config/config.json` 中）：
     ```json
     "javbus": {
       "mode": "internal",
       "external_api_url": "",
       "timeout": 10,
       "page_size": 30,
       "allow_external_fallback": false
     }
     ```
   - 特点：
     - 不需要额外部署服务，由本应用直接抓取 javbus.com 网页并解析；  
     - 适合内网 / 无法访问外部 API 的环境；  
     - 可配合本地数据库缓存，减轻对 JavBus 网站的压力。

2. **外部 API 模式（使用独立的 javbus-api 服务）**
   - 配置示例：
     ```json
     "javbus": {
       "mode": "external",
       "external_api_url": "http://your-javbus-api-server/api",
       "timeout": 10,
       "page_size": 30
     }
     ```
   - 含义：
     - 所有 JavBus 搜索 / 详情请求会转发到你自己部署的 **javbus-api** 服务，本应用只作为 HTTP 客户端使用；  
     - 可以减轻本机的爬虫负担，也更容易复用同一套 JavBus 数据给多个前端。
   - 外部 API 部署建议：
     - 推荐使用原作者的开源项目：  
       **ovnrain/javbus-api：一个自我托管的 JavBus API 服务**  
       GitHub：<https://github.com/ovnrain/javbus-api>
     - 简单做法（详细请以该仓库 README 为准）：
       - 使用 Docker 启动，或部署到 Vercel 等平台；  
       - 确保 HTTP 访问路径形如：`http://你的服务器:端口/api`；  
       - 把这个地址填入本项目的 `javbus.external_api_url`。

3. **混合模式：内部为主，外部兜底**
   - 如果你同时部署了外部 API，又希望 **优先使用内部 Scraper**，可以：
     ```json
     "javbus": {
       "mode": "internal",
       "external_api_url": "http://your-javbus-api-server/api",
       "page_size": 30,
       "allow_external_fallback": true,
       "internal": {
         "enabled": true,
         "cache_ttl_seconds": 3600,
         "allow_external_fallback": true
       }
     }
     ```
   - 行为：
     - 正常情况下使用内部 Scraper；  
     - 当内部抓取失败或超时时，会尝试回退到外部 `javbus-api` 服务获取结果。

> 兼容说明：旧版本配置中的顶层 `api_url` 字段仍然会被自动识别并映射到 `javbus.external_api_url`，但推荐今后统一使用 `javbus` 小节中的配置。

---

# BUS115 Usage Guide (English)

This English section mirrors the Chinese content but is slightly more concise.

---

## 1. What the App Does

BUS115 is a self‑hosted web application for **managing and playing JAV videos**.  
It unifies:

- Metadata from multiple sites (e.g. JavBus, FANZA/DMM)
- Video sources from:
  - 115 cloud drive
  - STRM files
  - Jellyfin

into a single web UI where you can:

- Search by movie ID or keyword
- Browse rich metadata (cover, samples, summary, cast, tags)
- Play videos directly in the browser using different pipelines
- Translate titles and summaries
- Maintain a favorites list

---

## 2. Main UI Entrances

- **Home** – recently added / updated movies  
- **Search** – search by movie ID or keyword  
- **Movie Detail** – full metadata and available sources for a single title  
- **115 Library** – browse your 115 cloud drive and open videos  
- **STRM Library** – manage and play STRM‑based entries  
- **Jellyfin Library** – integrate existing Jellyfin collections  
- **Favorites** – quick access to bookmarked movies

---

## 3. Searching & Movie Details

- Use the top search box to enter an ID like `SSIS-406`, `ABW-123`, `HEYZO-1234`, etc.  
- The app first checks the local database; if missing, it fetches metadata from the configured sources and caches it.

The detail page shows:

- Basic info (ID, title, date, maker, series)  
- Cover and sample images  
- Summary and translated summary (if enabled)  
- Cast and tags  
- Links to available sources (115, STRM, Jellyfin)  
- A button to add/remove the movie from favorites

---

## 4. 115 Cloud & Direct‑Link Transcoding (Highlight)

### 4.1 Login & Basic Setup

To use 115 features you need to configure:

- An authentication method:
  - Official OpenAPI (QR‑code login), or
  - Browser cookie string (“driver” mode), or
  - Auto mode (prefer cookie, fall back to OpenAPI)
- Optional Alist server for the “Alist playback” mode  
- `ffmpeg` / `ffprobe` installed on the server (already included in the Docker image)

After editing `config/config.json`, restart the app.

### 4.2 Browsing 115 and Opening the Player

In the **115 Library** page you can:

- Browse folders and files  
- See size and modification time  
- Search by file name  
- Click on a video to open the **115 player page**.

### 4.3 Three Playback Modes on the 115 Player Page

On the bottom of the player page there is a **playback mode selector**:

- **Alist playback** – uses your Alist server to proxy 115 and provide direct links (good for large files and resume).
- **115 direct link** – obtains a direct HTTP download URL from 115 and plays it in the browser; can be combined with local transcoding (see below).
- **115 playback** – uses the official 115 online player with multiple quality levels.

The current mode is shown in the “Playback info” area as:

- `Alist 播放` (Alist)  
- `115 直链播放` (direct link)  
- `115 播放` (official 115)

### 4.4 115 Direct‑Link with Local Transcoding

Some 115 videos are not in a browser‑friendly format, or you may prefer not to rely on 115’s own transcoding.  
BUS115 can **fetch the direct download URL and transcode it to HLS on your server**.

User‑level behavior:

1. Open a video from the 115 library – the 115 player page appears.  
2. Select **“115 直链”** in the playback mode selector.
3. The app:
   - Fetches the direct download URL for this file using your 115 login;  
   - Decides whether it can be played as‑is or should be transcoded;  
   - If transcoding is needed, creates a background ffmpeg task that converts the stream to HLS segments;  
   - Starts playback using a custom progress bar once enough data is ready.
4. Status messages at the bottom show:
   - “Requesting transcoding service…”
   - “Transcoding in progress…”  
   - Errors if something goes wrong.

When direct‑link transcoding is active:

- The built‑in HTML video controls are hidden;  
- A custom control bar shows playback progress and, as far as possible, the converted portion of the stream;  
- You can seek within the part that has already been transcoded, and the player will request additional segments as they become available.

There is also a **“Transcoding control”** group:

- **Stop transcoding** – sends a stop signal to the current transcoding task  
- **Transcode management** – opens `/cloud115/transcode/tasks`, where you can:
  - View task list and status
  - Stop individual tasks
  - Delete tasks and their temporary files

Requirements and tips:

- A working 115 login (cookie or OpenAPI)  
- Reachable 115 servers from your host  
- `ffmpeg`/`ffprobe` installed  
- Sufficient disk space for temporary transcoding files  
- If you see repeated errors, check the status messages and adjust your configuration.

---

## 5. STRM & Jellyfin Libraries

- **STRM**: point STRM files at your video URLs, let BUS115 scan them, and play them from the STRM Library page. The app tries to match STRM names with movie IDs to attach metadata.
- **Jellyfin**: configure your Jellyfin server (URL, username, password, API key). BUS115 can list Jellyfin movies, and in many cases reuse Jellyfin’s own transcoding to provide an HLS stream into the unified player.

---

## 6. Translation

On the movie detail page, a **Translate** button can be used to:

- Translate the title  
- Translate the summary

The translated text is stored in the local database for subsequent visits. A **Refresh translation** button can regenerate the translation if you change the translation backend.

---

## 7. Running the App (Short Version)

- **Docker**:
  ```bash
  docker-compose up -d
  # browse http://localhost:8080
  ```

- **Native Python**:
  ```bash
  pip install -r requirements.txt
  python webserver.py
  # browse http://localhost:8080
  ```

Edit `config/config.json` for:

- Data sources (JavBus, etc.)  
- 115 settings (auth mode, default folder, Alist if used)  
- Jellyfin settings  
- Translation backend

After editing, restart the app.

---

## 8.1 Live Captions & Translation (New)

- The player pages have a **“Live captions”** toggle to stream audio for transcription, and a separate **“Translation”** toggle to show translated lines.
- Transcription depends on an **external Faster Whisper Service** over WebSocket (`/ws/realtime`); this wrapper is **not** included in this repo—deploy it yourself (GPU strongly recommended).
- Configure `config/config.json`:
  - `transcription.provider = "fwhisper"` and `api_base_url`/`ws_url` pointing to your Faster Whisper Service.
  - `translation` section for your translation API (OpenAI/SiliconFlow/Ollama, etc.). Translation stays off unless you enable the toggle to avoid latency.
- When the service is unreachable, only original text may appear; it resumes once the service is back.

---

## 8. JavBus Data Sources (Internal Scraper vs External API)

The movie search/detail features rely heavily on **JavBus**. There are two ways to get this data:

1. **Internal scraper mode (default)**
   - Example in `config/config.json`:
     ```json
     "javbus": {
       "mode": "internal",
       "external_api_url": "",
       "timeout": 10,
       "page_size": 30,
       "allow_external_fallback": false
     }
     ```
   - The app scrapes javbus.com directly and parses HTML.  
   - No extra service is required, good for offline / LAN‑only setups.  
   - Results are cached in the local database to reduce load on JavBus.

2. **External API mode (using a separate javbus-api service)**
   - Example:
     ```json
     "javbus": {
       "mode": "external",
       "external_api_url": "http://your-javbus-api-server/api",
       "timeout": 10,
       "page_size": 30
     }
     ```
   - Behavior:
     - All search/detail calls go to your own **javbus-api** server; this app just acts as an HTTP client.  
     - Useful if you already maintain a central JavBus API for multiple frontends.
   - Recommended external API implementation:
     - Original project: **ovnrain/javbus-api – A self‑hosted JavBus API service**  
       GitHub: <https://github.com/ovnrain/javbus-api>
     - Typical steps (see that repo’s README for details):
       - Run the service via Docker or deploy to a platform like Vercel;  
       - Expose an endpoint like `http://your-host:port/api`;  
       - Put that URL into this project’s `javbus.external_api_url`.

3. **Hybrid mode: internal first, external as fallback**
   - If you want to keep the internal scraper but also have an external API as backup:
     ```json
     "javbus": {
       "mode": "internal",
       "external_api_url": "http://your-javbus-api-server/api",
       "page_size": 30,
       "allow_external_fallback": true,
       "internal": {
         "enabled": true,
         "cache_ttl_seconds": 3600,
         "allow_external_fallback": true
       }
     }
     ```
   - Behavior:
     - The app will try the internal scraper first.  
     - If scraping fails or times out, it will fall back to the external `javbus-api` service.

> Compatibility note: the legacy top‑level `api_url` config key is still recognized and mapped to `javbus.external_api_url`, but new deployments are encouraged to configure JavBus under the `javbus` section instead.

---