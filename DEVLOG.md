# 开发日志

## 2026-05-04

### 搜索分页增强
- 搜索页新增首页/上一页/下一页/尾页/Go跳页控件
- FC2 分页改为滑动窗口（与 JavBus/影片库一致），移除多余的尾部页码
- 后端分页数据结构统一：total_pages, total_pages_known, has_prev, prev_page, first_page, last_page, page_out_of_range
- JavBus 不强求总页数，保持保守逻辑
- 修复了 total_pages = len(pages) 的错误计算

### FANZA 影片简介 - 现状与已知问题

#### 当前获取路径
1. GraphQL content_id 猜测（优先）
   - 地址：api.video.dmm.co.jp/graphql
   - 方法：_build_video_dmm_ids() 生成候选（label+000nn 和 1+label+000nn）
   - 优点：快，不需要浏览器
   - 缺点：只能覆盖标准格式，不规则 content_id（如 h_ 厂商前缀）会猜不中
2. FANZA 网页搜索（fallback）
   - mono 通贩搜索
   - digital/videoa 数字版搜索
   - video.dmm.co.jp/list 视频搜索
3. 直连 URL 兜底

#### 已知问题
- FANZA 通贩(mono)搜索：部分番号（如 HAME-076）在通贩无商品，搜索返回零结果
- FANZA digital/videoa 搜索：被地区/JS 墙拦截，requests 直连返回空页面
- video.dmm.co.jp 搜索：需要 Playwright 有头浏览器过年龄认证，requests 被重定向到年龄确认页
- GraphQL API：没有 keyword 搜索接口，只支持 content_id 直查
- content_id 格式不统一：目前只覆盖 label+000nn 和 1+label+000nn 两种，缺少 h_ 前缀等变体

#### 验证结果

| 番号 | GraphQL 猜测 | 网页搜索 | 最终结果 |
|------|-------------|---------|---------|
| HAME-076 | 1hame00076 命中 | 通贩无商品/digital被墙 | 通过 GraphQL 获取 |
| SAN-453 | 两个候选均未命中 | 通贩搜索找到 | 通过搜索获取 |
| SSIS-001 | ssis00001 应可命中 | 常规番号无问题 | 无问题 |

#### 已备但未集成的方案
- tools/fanza_search_service.py：Playwright 有头搜索服务
- 原型已验证可返回正确 content_id
- 暂未集成：为部分影片开常驻 Playwright 服务性价比不高

#### 改进方向（优先级低）
1. 扩展 _build_video_dmm_ids() 候选格式（h_ 前缀等）
2. 等 FANZA 开放搜索 API 或网络环境变化后再优化搜索链路
3. 如确有需要，可将 Playwright 搜索服务集成到 fanza_scraper 的 fallback 链路

### FC2 搜索增强（近期）
- FC2 专用 scraper + list provider（基于 JAVten/fc2hub 聚合站）
- 番号识别、关键词搜索、分页、详情抓取全链路
