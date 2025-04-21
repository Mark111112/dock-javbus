

# BUS影片管理系统

## 项目简介

这是一个基于Python Flask开发的JAV影片管理系统，提供了视频元数据管理、在线播放、115网盘集成、STRM文件库等功能。

## 主要功能

### 1. 影片管理

- 影片搜索与浏览
- 演员信息管理
- 收藏夹功能
- 影片详情展示(包含标题、演员、发行日期、封面图等)
- 影片简介自动翻译(支持多种翻译API)

### 2. 视频播放

- 支持在线HLS流播放
- 115网盘视频在线播放
- 自动代理解决跨域问题
- 多清晰度切换

### 3. 115网盘集成

- 云盘文件浏览与管理
- 视频文件识别与关联
- 扫码登录功能
- 支持在线播放云盘视频

### 4. STRM文件库

- STRM文件生成与管理
- 目录自动扫描
- 分类管理
- 支持按添加时间、标题等多种方式排序
- 支持搜索功能

### 5. 其他功能

- 磁力链接管理
- 图片缓存和代理
- 支持有码/无码影片
- 完善的配置管理
- 日志记录系统

## 技术特点

- 基于Flask框架开发
- 使用SQLite数据库存储数据
- 支持多种API接口(JavBus、DMM等)
- 支持多种翻译API(OpenAI、Ollama等)
- 实现了完整的缓存机制
- 支持Docker部署

## 系统要求

- Python 3.6+
- 必要的Python包(requirements.txt)
- SQLite3
- 足够的磁盘空间(用于缓存图片和元数据)

## 目录结构

```
.
├── 115/            # 115云盘相关API文档
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
  "api_url": "API服务器地址",
  "watch_url_prefix": "视频播放前缀",
  "base_url": "数据源基础URL",
  "fanza_mappings": "番号映射配置",
  "translation": {
    "api_url": "翻译API地址",
    "source_lang": "源语言",
    "target_lang": "目标语言",
    "api_token": "API密钥",
    "model": "翻译模型"
  }
}
```

## 使用方法

1. 安装依赖：
   
   ```bash
   pip install -r requirements.txt
   ```

2. 配置系统：
   编辑`config/config.json`文件，设置必要的API地址和密钥

3. 启动服务：
   
   ```bash
   python webserver.py
   ```

4. 使用Docker部署：
   
   ```bash
   docker-compose up -d
   ```

## 注意事项

- 请确保有足够的磁盘空间用于缓存
- 建议使用反向代理来保护服务
- 定期备份数据库文件
- 注意配置文件中的敏感信息安全

## License

MIT License
