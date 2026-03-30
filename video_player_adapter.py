"""
视频播放器适配器

负责从影片页解析真实播放地址（优先 m3u8）。
设计目标：
1. 保留旧的 requests/curl_cffi 轻量解析能力
2. 为 Cloudflare / 风控场景提供结构化失败原因
3. 预留 Playwright 浏览器态 fallback
4. 避免调用方在失败时拿到 None 却不知道为什么
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

# 设置日志级别为WARNING，减少详细日志
logging.getLogger(__name__).setLevel(logging.WARNING)

# 尝试导入 curl_cffi
try:
    from curl_cffi import requests as curl_requests
except ImportError:
    logging.warning("curl_cffi未安装，使用备用方法")
    curl_requests = None

# 尝试导入 Playwright（可选）
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False

# 常量定义
VIDEO_M3U8_PREFIX = 'https://surrit.com/'
VIDEO_PLAYLIST_SUFFIX = '/playlist.m3u8'
RESOLUTION_PATTERN = r'RESOLUTION=(\d+)x(\d+)'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'sec-ch-ua': '"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1'
}


@dataclass
class StreamResolveResult:
    success: bool
    stream_url: Optional[str] = None
    source: str = "none"
    stage: str = "init"
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    target_url: Optional[str] = None
    status_code: Optional[int] = None
    html_title: Optional[str] = None
    challenge_detected: bool = False
    used_browser_fallback: bool = False
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VideoAPIAdapter:
    """视频API适配器类，提供获取视频流URL的功能"""

    def __init__(self, retry: int = 3, delay: int = 2, enable_browser_fallback: bool = False, browser_timeout_ms: int = 20000):
        self.retry = retry
        self.delay = delay
        self.direct_url = None
        self.enable_browser_fallback = enable_browser_fallback
        self.browser_timeout_ms = browser_timeout_ms

    @staticmethod
    def _is_cf_challenge(text: Optional[str]) -> bool:
        if not text:
            return False
        lowered = text.lower()
        return (
            'just a moment' in lowered
            or 'performing security verification' in lowered
            or '执行安全验证' in text
            or 'cloudflare' in lowered and 'ray id' in lowered
        )

    @staticmethod
    def _extract_title(html: Optional[str]) -> Optional[str]:
        if not html:
            return None
        m = re.search(r'<title>(.*?)</title>', html, re.I | re.S)
        if not m:
            return None
        return re.sub(r'\s+', ' ', m.group(1)).strip()

    def _get_with_curl_cffi(self, url: str, headers: Dict[str, str] = None, cookies: Dict[str, str] = None):
        if not curl_requests:
            return None
        try:
            response = curl_requests.get(
                url=url,
                headers=headers or HEADERS,
                cookies=cookies,
                impersonate="chrome136",
                timeout=20,
                verify=False
            )
            return response
        except Exception as e:
            logging.error(f"curl_cffi请求失败: {str(e)}")
            return None

    def _get_with_requests(self, url: str, session, headers: Dict[str, str] = None, cookies: Dict[str, str] = None):
        try:
            for attempt in range(self.retry):
                try:
                    response = session.get(
                        url,
                        headers=headers or HEADERS,
                        cookies=cookies,
                        timeout=20,
                        allow_redirects=True,
                    )
                    if response.status_code == 200:
                        return response
                    elif response.status_code == 403:
                        import random
                        import string
                        rand_str = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
                        if headers:
                            headers["X-Requested-With"] = f"XMLHttpRequest-{rand_str}"
                        else:
                            headers = dict(HEADERS)
                            headers["X-Requested-With"] = f"XMLHttpRequest-{rand_str}"
                        if cookies:
                            cookies["missav_session"] = rand_str
                        else:
                            cookies = {"missav_session": rand_str, "age_verify": "true"}
                        domain = url.replace("https://", "").replace("http://", "").split('/')[0]
                        session.cookies.set("missav_session", rand_str, domain=domain)
                        logging.warning(f"Received 403, retrying with modified headers (attempt {attempt+1})")
                        time.sleep(self.delay)
                    else:
                        logging.error(f"HTTP error {response.status_code}, retrying... (attempt {attempt+1})")
                        time.sleep(self.delay)
                except Exception as e:
                    logging.error(f"Request failed: {str(e)}, retrying... (attempt {attempt+1})")
                    time.sleep(self.delay)
        except Exception as e:
            logging.error(f"Get with requests failed: {str(e)}")
        return None

    async def _fetch_with_playwright(self, movie_url: str) -> Dict[str, Any]:
        if not PLAYWRIGHT_AVAILABLE:
            return {"ok": False, "error": "playwright_not_installed"}

        browser = None
        pw = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(
                user_agent=HEADERS['User-Agent'],
                locale='zh-CN'
            )
            page = await context.new_page()
            await page.goto(movie_url, wait_until='domcontentloaded', timeout=self.browser_timeout_ms)
            await page.wait_for_timeout(8000)
            html = await page.content()
            return {
                "ok": True,
                "html": html,
                "url": page.url,
                "title": await page.title(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                if browser:
                    await browser.close()
            finally:
                if pw:
                    await pw.stop()

    def _fetch_page_html(self, movie_url: str, session) -> Dict[str, Any]:
        logging.info(f"获取视频元数据: {movie_url}")
        domain = movie_url.replace("https://", "").replace("http://", "").split('/')[0]
        cookies = {"age_verify": "true"}
        session.cookies.set("age_verify", "true", domain=domain)

        response = self._get_with_curl_cffi(movie_url, cookies=cookies)
        if response is not None:
            html = getattr(response, 'text', None)
            status_code = getattr(response, 'status_code', None)
            title = self._extract_title(html)
            challenge = self._is_cf_challenge(html)
            if status_code == 200 and html and not challenge:
                return {
                    "ok": True,
                    "html": html,
                    "status_code": status_code,
                    "source": "curl_cffi",
                    "challenge_detected": False,
                    "title": title,
                }
            if challenge:
                return {
                    "ok": False,
                    "html": html,
                    "status_code": status_code,
                    "source": "curl_cffi",
                    "challenge_detected": True,
                    "title": title,
                    "error_code": "cf_challenge",
                    "error_message": "源站返回 Cloudflare 安全验证页",
                }

        response = self._get_with_requests(movie_url, session, cookies=cookies)
        if response is not None:
            html = getattr(response, 'text', None)
            status_code = getattr(response, 'status_code', None)
            title = self._extract_title(html)
            challenge = self._is_cf_challenge(html)
            if status_code == 200 and html and not challenge:
                return {
                    "ok": True,
                    "html": html,
                    "status_code": status_code,
                    "source": "requests",
                    "challenge_detected": False,
                    "title": title,
                }
            if challenge or status_code == 403:
                return {
                    "ok": False,
                    "html": html,
                    "status_code": status_code,
                    "source": "requests",
                    "challenge_detected": challenge or status_code == 403,
                    "title": title,
                    "error_code": "cf_challenge",
                    "error_message": "源站返回 Cloudflare 安全验证页",
                }

        return {
            "ok": False,
            "html": None,
            "status_code": None,
            "source": "requests",
            "challenge_detected": False,
            "title": None,
            "error_code": "fetch_failed",
            "error_message": f"无法获取网页内容: {movie_url}",
        }

    def _extract_stream_metadata(self, html: str) -> Dict[str, Any]:
        patterns = [
            ("uuid_pipe", r'm3u8\|([a-f0-9\|]+)\|com\|surrit\|https\|video'),
            ("playlist_uuid", r'https://surrit\.com/([a-f0-9-]+)/playlist\.m3u8'),
            ("video_src", r'video[^>]*src=["\'](https://surrit\.com/[^"\']+)["\']'),
            ("plain_uuid", r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}'),
            ("direct_m3u8", r'https?://[^"\'<>\s]+\.m3u8'),
            ("js_source", r'source\s*=\s*["\']+(https?://[^"\'<>\s]+\.m3u8)["\']+'),
        ]

        direct_m3u8_url = None
        for name, pattern in patterns:
            match = re.search(pattern, html)
            if not match:
                continue
            if name == "uuid_pipe":
                result = match.group(1)
                uuid = "-".join(result.split("|")[::-1])
                if re.match(r'^[a-f0-9-]{36}$', uuid):
                    return {"kind": "uuid", "value": uuid, "pattern": name}
            elif name == "playlist_uuid":
                uuid = match.group(1)
                if re.match(r'^[a-f0-9-]{36}$', uuid):
                    return {"kind": "uuid", "value": uuid, "pattern": name}
            elif name == "video_src":
                url_part = match.group(1)
                if url_part.endswith('.m3u8'):
                    direct_m3u8_url = url_part
                else:
                    uuid_match = re.search(r'/([a-f0-9-]+)/', url_part)
                    if uuid_match:
                        uuid = uuid_match.group(1)
                        if re.match(r'^[a-f0-9-]{36}$', uuid):
                            return {"kind": "uuid", "value": uuid, "pattern": name}
            elif name == "plain_uuid":
                return {"kind": "uuid", "value": match.group(0), "pattern": name}
            elif name == "direct_m3u8":
                direct_m3u8_url = match.group(0)
            elif name == "js_source":
                direct_m3u8_url = match.group(1)

        if direct_m3u8_url:
            self.direct_url = direct_m3u8_url
            return {"kind": "direct_url", "value": direct_m3u8_url, "pattern": "direct_m3u8"}

        return {"kind": "none", "value": None, "pattern": None}

    def _get_playlist_url(self, uuid: str) -> str:
        playlist_url = f"{VIDEO_M3U8_PREFIX}{uuid}{VIDEO_PLAYLIST_SUFFIX}"
        logging.info(f"播放列表URL: {playlist_url}")
        return playlist_url

    def _parse_playlist(self, playlist_url: str, playlist_content: str, quality: Optional[str] = None) -> Optional[str]:
        try:
            matches = re.findall(RESOLUTION_PATTERN, playlist_content)
            if not matches:
                logging.info("播放列表中未找到分辨率信息，直接使用主播放列表")
                return playlist_url

            quality_map = {height: width for width, height in matches}
            quality_list = sorted([int(h) for h in quality_map.keys()])

            if not quality:
                highest_height = str(quality_list[-1])
                url_patterns = [
                    f"{quality_map[highest_height]}x{highest_height}/video.m3u8",
                    f"{highest_height}p/video.m3u8"
                ]
            else:
                quality_cleaned = quality.strip().lower()
                if not quality_cleaned.endswith('p'):
                    quality_cleaned += 'p'
                quality_num = int(quality_cleaned.replace('p', ''))
                closest_height = min(quality_list, key=lambda x: abs(x - quality_num))
                url_patterns = [
                    f"{quality_map[str(closest_height)]}x{closest_height}/video.m3u8",
                    f"{closest_height}p/video.m3u8"
                ]

            resolution_url = None
            for pattern in url_patterns:
                if pattern in playlist_content:
                    for line in playlist_content.splitlines():
                        if pattern in line:
                            resolution_url = line
                            break
                    if resolution_url:
                        break

            if not resolution_url:
                non_comment_lines = [l for l in playlist_content.splitlines() if l and not l.startswith('#')]
                resolution_url = non_comment_lines[-1] if non_comment_lines else None

            if not resolution_url:
                return playlist_url
            if resolution_url.startswith('http'):
                return resolution_url
            base_url = '/'.join(playlist_url.split('/')[:-1])
            return f"{base_url}/{resolution_url}"
        except Exception as e:
            logging.error(f"解析播放列表时出错: {str(e)}")
            return playlist_url

    def _resolve_from_playlist(self, playlist_url: str, session, quality: Optional[str] = None) -> StreamResolveResult:
        playlist_content = None
        try:
            if curl_requests:
                response = curl_requests.get(playlist_url, impersonate="chrome136", timeout=20)
                if response.status_code == 200:
                    playlist_content = response.text
        except Exception as e:
            logging.error(f"获取播放列表失败(curl_cffi): {str(e)}")

        if not playlist_content:
            try:
                response = session.get(playlist_url, timeout=20)
                if response.status_code == 200:
                    playlist_content = response.text
            except Exception as e:
                logging.error(f"获取播放列表失败(requests): {str(e)}")

        if not playlist_content:
            return StreamResolveResult(
                success=True,
                stream_url=playlist_url,
                source="playlist_url",
                stage="playlist_fetch",
                target_url=playlist_url,
                details={"reason": "playlist_content_unavailable"},
            )

        stream_url = self._parse_playlist(playlist_url, playlist_content, quality)
        if stream_url:
            return StreamResolveResult(
                success=True,
                stream_url=stream_url,
                source="playlist_parsed",
                stage="playlist_parse",
                target_url=playlist_url,
            )

        return StreamResolveResult(
            success=False,
            stage="playlist_parse",
            source="playlist_parsed",
            target_url=playlist_url,
            error_code="playlist_parse_failed",
            error_message="无法从播放列表解析流地址",
        )

    def resolve_stream(self, movie_url: str, session, quality: Optional[str] = None) -> StreamResolveResult:
        page_result = self._fetch_page_html(movie_url, session)
        if page_result.get("ok"):
            metadata = self._extract_stream_metadata(page_result["html"])
            if metadata["kind"] == "direct_url":
                return StreamResolveResult(
                    success=True,
                    stream_url=metadata["value"],
                    source="direct_url",
                    stage="html_parse",
                    target_url=movie_url,
                    status_code=page_result.get("status_code"),
                    html_title=page_result.get("title"),
                    details={"pattern": metadata.get("pattern"), "fetch_source": page_result.get("source")},
                )
            if metadata["kind"] == "uuid":
                playlist_url = self._get_playlist_url(metadata["value"])
                playlist_result = self._resolve_from_playlist(playlist_url, session, quality)
                playlist_result.status_code = page_result.get("status_code")
                playlist_result.html_title = page_result.get("title")
                details = playlist_result.details or {}
                details.update({"pattern": metadata.get("pattern"), "fetch_source": page_result.get("source")})
                playlist_result.details = details
                return playlist_result
            return StreamResolveResult(
                success=False,
                stage="html_parse",
                source=page_result.get("source", "html"),
                target_url=movie_url,
                status_code=page_result.get("status_code"),
                html_title=page_result.get("title"),
                error_code="stream_not_found_in_html",
                error_message="已获取页面，但未找到可用的 m3u8 或 UUID",
                details={"fetch_source": page_result.get("source")},
            )

        # 可选浏览器 fallback
        if page_result.get("error_code") == "cf_challenge" and self.enable_browser_fallback:
            try:
                browser_result = asyncio.run(self._fetch_with_playwright(movie_url))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    browser_result = loop.run_until_complete(self._fetch_with_playwright(movie_url))
                finally:
                    loop.close()

            if browser_result.get("ok"):
                html = browser_result.get("html") or ""
                if not self._is_cf_challenge(html):
                    metadata = self._extract_stream_metadata(html)
                    if metadata["kind"] == "direct_url":
                        return StreamResolveResult(
                            success=True,
                            stream_url=metadata["value"],
                            source="playwright_direct_url",
                            stage="browser_html_parse",
                            target_url=movie_url,
                            html_title=browser_result.get("title"),
                            used_browser_fallback=True,
                            details={"pattern": metadata.get("pattern")},
                        )
                    if metadata["kind"] == "uuid":
                        playlist_url = self._get_playlist_url(metadata["value"])
                        playlist_result = self._resolve_from_playlist(playlist_url, session, quality)
                        playlist_result.used_browser_fallback = True
                        playlist_result.source = f"playwright_{playlist_result.source}"
                        playlist_result.stage = "browser_playlist_parse"
                        playlist_result.html_title = browser_result.get("title")
                        details = playlist_result.details or {}
                        details.update({"pattern": metadata.get("pattern")})
                        playlist_result.details = details
                        return playlist_result
                return StreamResolveResult(
                    success=False,
                    stage="browser_fetch",
                    source="playwright",
                    target_url=movie_url,
                    html_title=browser_result.get("title"),
                    challenge_detected=self._is_cf_challenge(browser_result.get("html") or ""),
                    used_browser_fallback=True,
                    error_code="cf_challenge_browser",
                    error_message="浏览器态仍被 Cloudflare 安全验证拦截",
                )

            return StreamResolveResult(
                success=False,
                stage="browser_fetch",
                source="playwright",
                target_url=movie_url,
                used_browser_fallback=True,
                error_code="browser_fetch_failed",
                error_message=f"浏览器 fallback 失败: {browser_result.get('error')}",
            )

        return StreamResolveResult(
            success=False,
            stage="page_fetch",
            source=page_result.get("source", "requests"),
            target_url=movie_url,
            status_code=page_result.get("status_code"),
            html_title=page_result.get("title"),
            challenge_detected=page_result.get("challenge_detected", False),
            error_code=page_result.get("error_code", "fetch_failed"),
            error_message=page_result.get("error_message", "无法获取视频流URL"),
        )

    def get_stream_url(self, movie_url: str, session, quality: Optional[str] = None) -> Optional[str]:
        result = self.resolve_stream(movie_url, session, quality)
        return result.stream_url if result.success else None


# 导出的主要 API 函数

def resolve_video_stream(movie_url: str, session, quality: Optional[str] = None, enable_browser_fallback: bool = False) -> Dict[str, Any]:
    adapter = VideoAPIAdapter(enable_browser_fallback=enable_browser_fallback)
    return adapter.resolve_stream(movie_url, session, quality).to_dict()


def get_video_stream_url(movie_url: str, session, quality: Optional[str] = None) -> Optional[str]:
    adapter = VideoAPIAdapter()
    return adapter.get_stream_url(movie_url, session, quality)
