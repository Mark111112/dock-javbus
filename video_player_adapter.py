"""
视频播放器适配器 - 用于将video_player2.py中的功能封装为Web应用可以使用的API
"""

import logging
import time
import re
from typing import Optional, Dict, Any

# 设置日志级别为WARNING，减少详细日志
logging.getLogger(__name__).setLevel(logging.WARNING)

# 尝试导入curl_cffi
try:
    from curl_cffi import requests
except ImportError:
    logging.warning("curl_cffi未安装，使用备用方法")
    requests = None

# 常量定义 - 从video_player2.py复制
VIDEO_M3U8_PREFIX = 'https://surrit.com/'
VIDEO_PLAYLIST_SUFFIX = '/playlist.m3u8'
MATCH_UUID_PATTERN = r'm3u8\|([a-f0-9\|]+)\|com\|surrit\|https\|video'
RESOLUTION_PATTERN = r'RESOLUTION=(\d+)x(\d+)'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
    'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1'
}

class VideoAPIAdapter:
    """视频API适配器类，提供获取视频流URL的功能"""
    
    def __init__(self, retry: int = 3, delay: int = 2):
        """初始化适配器
        
        Args:
            retry: 重试次数
            delay: 重试延迟(秒)
        """
        self.retry = retry
        self.delay = delay
        self.direct_url = None

    def _get_with_curl_cffi(self, url: str, headers: Dict[str, str] = None, 
                           cookies: Dict[str, str] = None) -> Optional[str]:
        """使用curl_cffi获取URL内容，更好地绕过Cloudflare保护
        
        Args:
            url: 要请求的URL
            headers: 请求头
            cookies: Cookie

        Returns:
            响应内容字符串或None(如果失败)
        """
        if not requests:
            return None
            
        try:
            response = requests.get(
                url=url,
                headers=headers or HEADERS,
                cookies=cookies,
                impersonate="chrome110",  # 模拟Chrome 110
                timeout=15,
                verify=False
            )
            
            if response.status_code == 200:
                return response.text
        except Exception as e:
            logging.error(f"curl_cffi请求失败: {str(e)}")
            
        return None

    def _get_with_requests(self, url: str, session, 
                          headers: Dict[str, str] = None, 
                          cookies: Dict[str, str] = None) -> Optional[str]:
        """使用标准requests获取URL内容
        
        Args:
            url: 要请求的URL
            session: 请求会话
            headers: 请求头
            cookies: Cookie

        Returns:
            响应内容字符串或None(如果失败)
        """
        try:
            for attempt in range(self.retry):
                try:
                    response = session.get(
                        url, 
                        headers=headers or HEADERS,
                        cookies=cookies,
                        timeout=15,
                        allow_redirects=True
                    )
                    
                    if response.status_code == 200:
                        return response.text
                    elif response.status_code == 403:
                        # 尝试添加随机头部绕过403
                        import random, string
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
                            
                        # 在cookie中设置domain
                        domain = url.replace("https://", "").replace("http://", "").split('/')[0]
                        session.cookies.set("missav_session", rand_str, domain=domain)
                        
                        logging.warning(f"Received 403, retrying with modified headers (attempt {attempt+1})")
                        time.sleep(self.delay)
                    else:
                        # 其他错误，等待后重试
                        logging.error(f"HTTP error {response.status_code}, retrying... (attempt {attempt+1})")
                        time.sleep(self.delay)
                except Exception as e:
                    logging.error(f"Request failed: {str(e)}, retrying... (attempt {attempt+1})")
                    time.sleep(self.delay)
        except Exception as e:
            logging.error(f"Get with requests failed: {str(e)}")
            
        return None

    def _fetch_metadata(self, movie_url: str, session) -> Optional[str]:
        """获取视频元数据(UUID或直接的m3u8 URL)
        
        Args:
            movie_url: 视频页面URL
            session: 请求会话
        
        Returns:
            UUID字符串、"direct_url"标记或None(如果失败)
        """
        logging.info(f"获取视频元数据: {movie_url}")
        
        # 准备cookie
        domain = movie_url.replace("https://", "").replace("http://", "").split('/')[0]
        cookies = {"age_verify": "true"}
        session.cookies.set("age_verify", "true", domain=domain)
        
        # 首先尝试使用curl_cffi，它更好地处理Cloudflare保护
        html = self._get_with_curl_cffi(movie_url, cookies=cookies)
        
        # 如果失败，回退到标准requests
        if not html:
            html = self._get_with_requests(movie_url, session, cookies=cookies)
            
        if not html:
            logging.error(f"无法获取网页内容: {movie_url}")
            return None
            
        # 尝试多种正则表达式匹配模式
        patterns = [
            # 标准的UUID匹配模式
            r'm3u8\|([a-f0-9\|]+)\|com\|surrit\|https\|video',
            # 备用模式 1: 直接找surrit.com相关URL
            r'https://surrit\.com/([a-f0-9-]+)/playlist\.m3u8',
            # 备用模式 2: 查找video标签的src
            r'video[^>]*src=["\'](https://surrit\.com/[^"\']+)["\']',
            # 备用模式 3: 查找所有UUID格式 (通用的UUID格式)
            r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',
            # 备用模式 4: 查找任何.m3u8链接
            r'https?://[^"\'<>\s]+\.m3u8',
            # 备用模式 5: 查找使用JS设置的视频源
            r'source\s*=\s*["\']+(https?://[^"\'<>\s]+\.m3u8)["\']+'
        ]
        
        # 直接返回的m3u8链接
        direct_m3u8_url = None
        
        logging.info("开始尝试匹配视频源...")
        for i, pattern in enumerate(patterns):
            match = re.search(pattern, html)
            if match:
                logging.info(f"成功通过模式 {i+1} 匹配到结果")
                
                # 根据不同模式处理匹配结果
                if i == 0:  # 原始模式：特殊格式的UUID
                    result = match.group(1)
                    uuid = "-".join(result.split("|")[::-1])
                    if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                        logging.info(f"UUID格式验证通过: {uuid}")
                        return uuid
                elif i == 1:  # 模式1：直接找到的playlist链接中的UUID
                    uuid = match.group(1)
                    if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                        logging.info(f"UUID格式验证通过: {uuid}")
                        return uuid
                elif i == 2:  # 模式2：video标签src中的URL
                    url_part = match.group(1)
                    # 检查是否是完整的m3u8链接
                    if url_part.endswith('.m3u8'):
                        logging.info(f"直接找到m3u8链接: {url_part}")
                        direct_m3u8_url = url_part
                    else:
                        # 尝试从URL中提取UUID
                        uuid_match = re.search(r'/([a-f0-9-]+)/', url_part)
                        if uuid_match:
                            uuid = uuid_match.group(1)
                            if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                                logging.info(f"UUID格式验证通过: {uuid}")
                                return uuid
                elif i == 3:  # 模式3：直接匹配UUID格式
                    uuid = match.group(0)
                    logging.info(f"成功获取视频UUID: {uuid}")
                    if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                        logging.info(f"UUID格式验证通过: {uuid}")
                        return uuid
                elif i in (4, 5):  # 模式4和5：直接的m3u8链接
                    direct_m3u8_url = match.group(1) if i == 5 else match.group(0)
                    logging.info(f"直接找到m3u8链接: {direct_m3u8_url}")
                    # 不立即返回，继续尝试其他模式，优先使用UUID方式
        
        # 如果找到了直接的m3u8链接但没找到UUID，使用直接链接
        if direct_m3u8_url:
            logging.info(f"未找到UUID，使用直接的m3u8链接: {direct_m3u8_url}")
            self.direct_url = direct_m3u8_url  # 保存直接URL
            return "direct_url"  # 返回特殊标记
        
        logging.error(f"无法匹配视频源: {movie_url}")
        return None

    def _get_playlist_url(self, uuid: str) -> str:
        """获取播放列表URL
        
        Args:
            uuid: 视频UUID
            
        Returns:
            播放列表URL
        """
        playlist_url = f"{VIDEO_M3U8_PREFIX}{uuid}{VIDEO_PLAYLIST_SUFFIX}"
        logging.info(f"播放列表URL: {playlist_url}")
        return playlist_url

    def _parse_playlist(self, playlist_url: str, playlist_content: str, quality: Optional[str] = None) -> Optional[str]:
        """解析播放列表，获取指定质量的流URL
        
        Args:
            playlist_url: 播放列表URL
            playlist_content: 播放列表内容
            quality: 指定的视频质量(例如"720p")
            
        Returns:
            视频流URL或None(如果解析失败)
        """
        try:
            # 检查是否包含分辨率信息
            matches = re.findall(RESOLUTION_PATTERN, playlist_content)
            if not matches:
                logging.info("播放列表中未找到分辨率信息，直接使用主播放列表")
                return playlist_url
                
            # 处理有多种分辨率的情况
            quality_map = {height: width for width, height in matches}
            quality_list = sorted([int(h) for h in quality_map.keys()])
            
            if not quality:
                # 获取最高分辨率
                highest_height = str(quality_list[-1])
                quality_str = f"{highest_height}p"
                logging.info(f"选择最高质量: {quality_str}")
                
                # 尝试查找对应分辨率的URL
                url_patterns = [
                    f"{quality_map[highest_height]}x{highest_height}/video.m3u8",
                    f"{highest_height}p/video.m3u8"
                ]
            else:
                # 清理质量字符串，确保格式为"数字p"
                quality_cleaned = quality.strip().lower()
                if not quality_cleaned.endswith('p'):
                    quality_cleaned += 'p'
                    
                # 获取数字部分
                quality_num = int(quality_cleaned.replace('p', ''))
                
                # 获取最接近指定分辨率的选项
                closest_height = min(quality_list, key=lambda x: abs(x - quality_num))
                quality_str = f"{closest_height}p"
                logging.info(f"选择质量: {quality_str} (接近请求的 {quality})")
                
                # 尝试查找对应分辨率的URL
                url_patterns = [
                    f"{quality_map[str(closest_height)]}x{closest_height}/video.m3u8",
                    f"{closest_height}p/video.m3u8"
                ]
            
            # 查找匹配的分辨率URL
            resolution_url = None
            for pattern in url_patterns:
                if pattern in playlist_content:
                    lines = playlist_content.splitlines()
                    for line in lines:
                        if pattern in line:
                            resolution_url = line
                            break
                    if resolution_url:
                        break
            
            # 如果没找到指定分辨率，使用最后一个非注释行
            if not resolution_url:
                non_comment_lines = [l for l in playlist_content.splitlines() if not l.startswith('#')]
                resolution_url = non_comment_lines[-1] if non_comment_lines else playlist_content.splitlines()[-1]
                logging.info(f"未找到指定分辨率URL，使用默认: {resolution_url}")
            else:
                logging.info(f"找到分辨率URL: {resolution_url}")
            
            # 检查resolution_url是否是完整URL
            if resolution_url.startswith('http'):
                return resolution_url
            else:
                # 拼接相对路径
                base_url = '/'.join(playlist_url.split('/')[:-1])
                return f"{base_url}/{resolution_url}"
                
        except Exception as e:
            logging.error(f"解析播放列表时出错: {str(e)}")
            return playlist_url  # 出错时返回原始播放列表URL
    
    def get_stream_url(self, movie_url: str, session, quality: Optional[str] = None) -> Optional[str]:
        """获取视频流URL
        
        Args:
            movie_url: 视频页面URL
            session: 请求会话
            quality: 视频质量(如"720p")
            
        Returns:
            视频流URL或None(如果失败)
        """
        # 获取视频元数据
        uuid_or_marker = self._fetch_metadata(movie_url, session)
        if not uuid_or_marker:
            return None
            
        # 检查是否是直接URL标记
        if uuid_or_marker == "direct_url" and self.direct_url:
            logging.info(f"使用直接的播放列表URL: {self.direct_url}")
            return self.direct_url
            
        # 标准UUID处理
        playlist_url = self._get_playlist_url(uuid_or_marker)
        
        # 获取播放列表内容
        playlist_content = None
        # 优先使用curl_cffi
        if requests:
            try:
                response = requests.get(playlist_url, impersonate="chrome110", timeout=15)
                if response.status_code == 200:
                    playlist_content = response.text
            except Exception as e:
                logging.error(f"获取播放列表失败(curl_cffi): {str(e)}")
        
        # 回退到标准requests
        if not playlist_content:
            try:
                response = session.get(playlist_url, timeout=15)
                if response.status_code == 200:
                    playlist_content = response.text
            except Exception as e:
                logging.error(f"获取播放列表失败(requests): {str(e)}")
        
        # 如果无法获取播放列表内容，直接返回播放列表URL
        if not playlist_content:
            logging.warning("无法获取播放列表内容，直接使用播放列表URL")
            return playlist_url
            
        # 解析播放列表，获取指定质量的流URL
        return self._parse_playlist(playlist_url, playlist_content, quality)


# 导出的主要API函数
def get_video_stream_url(movie_url: str, session, quality: Optional[str] = None) -> Optional[str]:
    """获取视频流URL (主要API函数)
    
    Args:
        movie_url: 视频页面URL
        session: 请求会话对象
        quality: 视频质量(如"720p")
        
    Returns:
        视频流URL或None(如果失败)
    """
    adapter = VideoAPIAdapter()
    return adapter.get_stream_url(movie_url, session, quality) 