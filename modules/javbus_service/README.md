# JavBus Service 模块

基于 javbus.com 网站爬虫的 JavBus 客户端实现。

## 架构说明

### 模块结构

- `base.py` - 定义 `JavbusClientProtocol` 接口协议
- `external_client.py` - 外部 API 客户端（HTTP 调用 javbus-api 服务）
- `internal_client.py` - 内部爬虫客户端（直接爬取 javbus.com）
- `javbus_scraper.py` - javbus.com 网站爬虫实现
- `__init__.py` - 工厂方法，根据配置返回客户端实例

### Internal Client 工作流程

#### 搜索影片 (`search_movies`)

1. **优先级策略**：
   - 如果有关键词：直接爬取 javbus.com（不查数据库）
   - 如果无关键词但有筛选条件：查询数据库缓存
   - 爬取结果自动保存到数据库作为缓存

2. **爬取逻辑**：
   ```
   关键词搜索 → javbus.com/search/{keyword}/{page}
   无码搜索   → javbus.com/uncensored/search/{keyword}/{page}
   ```

3. **数据流**：
   ```
   用户请求 → 检查缓存 → 爬取网站 → 解析HTML → 保存数据库 → 返回结果
   ```

#### 获取详情 (`get_movie`)

1. **优先级策略**：
   - 内存缓存（有效期内）
   - 数据库缓存（有效期内）
   - 爬取 javbus.com
   - 获取磁力链接（需要 gid 和 uc）
   - 外部 API 回退（可选）

2. **爬取逻辑**：
   ```
   影片详情 → javbus.com/{movie_id}  (有码和无码都使用相同格式)
   磁力链接 → POST javbus.com/ajax/uncledatoolsbyajax.php
   ```
   
   注意：无码影片的详情页 URL 也是 `javbus.com/{movie_id}`，不需要添加 `/uncensored/` 路径。
   只有搜索和列表页面需要 `/uncensored/` 路径。

3. **磁力获取**：
   - 从详情页解析 `gid` 和 `uc` 参数
   - POST 请求获取磁力列表
   - 解析磁力大小、日期、HD标识、字幕标识

## 配置说明

### config.json 示例

```json
{
  "base_url": "https://www.javbus.com",
  "javbus": {
    "mode": "internal",
    "external_api_url": "http://192.168.1.246:8922/api",
    "timeout": 10,
    "page_size": 30,
    "allow_external_fallback": false,
    "internal": {
      "enabled": true,
      "timeout": 10,
      "cache_ttl_seconds": 3600
    }
  }
}
```

### 配置项说明

- `base_url` - javbus.com 的访问地址（支持镜像站）
- `javbus.mode` - `external` 或 `internal`
- `javbus.allow_external_fallback` - 内部模式失败时是否回退外部 API
- `javbus.internal.timeout` - 爬虫请求超时时间（秒）
- `javbus.internal.cache_ttl_seconds` - 数据库缓存有效期（秒）

## 日志标记

所有日志都带有标记便于调试：

- `[爬虫-请求]` - HTTP 请求
- `[爬虫-搜索]` - 搜索操作
- `[爬虫-详情]` - 获取详情
- `[爬虫-磁力]` - 获取磁力链接
- `[搜索-缓存]` - 缓存命中
- `[搜索-爬取]` - 爬取网站
- `[搜索-已缓存]` - 保存到数据库
- `[搜索-完成]` - 搜索完成
- `[获取影片-缓存]` - 内存缓存命中
- `[获取影片-数据库]` - 数据库缓存命中
- `[获取影片-爬取]` - 爬取详情
- `[获取影片-磁力]` - 获取磁力
- `[获取影片-已保存]` - 保存到数据库
- `[获取影片-回退外部API]` - 回退外部 API

## 反爬策略

### 当前实现

1. **请求头伪装**：
   - User-Agent: 模拟 Chrome 浏览器
   - Referer: javbus.com
   - Accept/Accept-Language 等完整请求头

2. **请求频率控制**：
   - 最小请求间隔 1 秒（`_min_interval`）
   - 自动限速（`_rate_limit` 方法）

3. **Session 复用**：
   - 使用 `requests.Session` 保持 Cookie

### 未来扩展

- 代理支持
- User-Agent 轮换
- Cloudflare 绕过（如需要）
- 失败重试机制

## 数据格式

### 搜索结果

```python
{
    "movies": [
        {
            "id": "SSIS-406",
            "title": "...",
            "img": "https://...",
            "date": "2022-05-20",
            "tags": ["高清", "字幕"]
        }
    ],
    "pagination": {
        "currentPage": 1,
        "hasNextPage": true,
        "nextPage": 2,
        "pages": [1, 2, 3]
    }
}
```

### 影片详情

```python
{
    "id": "SSIS-406",
    "title": "...",
    "img": "https://...",  # 封面大图
    "date": "2022-05-20",
    "videoLength": 120,
    "director": {"id": "...", "name": "..."},
    "producer": {"id": "...", "name": "..."},
    "publisher": {"id": "...", "name": "..."},
    "series": {"id": "...", "name": "..."},
    "genres": [{"id": "...", "name": "..."}],
    "stars": [{"id": "...", "name": "..."}],
    "samples": [{
        "id": "...",
        "src": "https://...",      # 大图
        "thumbnail": "https://...", # 缩略图
        "alt": "..."
    }],
    "gid": "50217160940",  # 用于获取磁力
    "uc": "0",
    "magnets": [...]  # 磁力链接列表
}
```

### 磁力链接

```python
[
    {
        "id": "17508BF5C17CBDF7...",  # BTIH
        "link": "magnet:?xt=...",
        "title": "SSNI-730-C",
        "size": "6.57GB",
        "shareDate": "2021-03-14",
        "isHD": true,
        "hasSubtitle": true
    }
]
```

## 与 moviescraper 的关系

- `moviescraper` 主要用于无码站点（DMM/Fanza/Heyzo 等）
- `internal_client` 直接爬取 javbus.com（有码+无码）
- `moviescraper` 仅在影片详情页用于补充信息，搜索阶段不使用

## 测试建议

1. **测试搜索**：
   ```python
   result = client.search_movies(keyword="SSIS-001", page=1)
   ```
   
2. **测试详情**：
   ```python
   movie = client.get_movie("SSIS-406")
   ```

3. **检查日志**：
   - 观察 `[爬虫-*]` 和 `[搜索-*]` 标记
   - 确认爬取、缓存、保存的完整流程

4. **验证缓存**：
   - 第一次请求应该爬取
   - 第二次请求应该命中缓存























