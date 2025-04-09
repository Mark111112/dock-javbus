import sys
import traceback
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import os
import subprocess
import platform
import argparse
import time
import re
import threading
import json
from urllib.parse import urlparse, quote

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

try:
    import vlc
    logging.info("Successfully imported VLC")
except ImportError as e:
    logging.error(f"Error importing VLC: {e}")
    print(f"错误: 缺少python-vlc模块。请先安装: pip install python-vlc")
    sys.exit(1)

try:
    from curl_cffi import requests
    logging.info("Successfully imported curl_cffi")
except ImportError as e:
    logging.error(f"Error importing curl_cffi: {e}")
    print(f"错误: 缺少curl_cffi模块。请先安装: pip install curl_cffi")
    sys.exit(1)

from typing import Optional, Tuple

# 常量定义
VIDEO_M3U8_PREFIX = 'https://surrit.com/'
VIDEO_PLAYLIST_SUFFIX = '/playlist.m3u8'
MATCH_UUID_PATTERN = r'm3u8\|([a-f0-9\|]+)\|com\|surrit\|https\|video'
RESOLUTION_PATTERN = r'RESOLUTION=(\d+)x(\d+)'
DOWNLOAD_FOLDER = 'downloads'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
}

def check_vlc_installation():
    try:
        instance = vlc.Instance()
        player = instance.media_player_new()
        player.release()
        instance.release()
        logging.info("VLC media player is available")
        return True
    except Exception as e:
        logging.error(f"Error initializing VLC: {e}")
        print(f"错误: VLC媒体播放器初始化失败。请确保您已安装VLC并且可以正常运行。")
        return False

def check_ffmpeg_installation():
    try:
        result = subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            logging.info("FFmpeg is available")
            return True
        else:
            logging.error("FFmpeg command failed")
            print("错误: FFmpeg命令执行失败。请确保FFmpeg已正确安装。")
            return False
    except Exception as e:
        logging.error(f"Error checking FFmpeg: {e}")
        print(f"错误: 无法检查FFmpeg: {str(e)}")
        return False

class HttpClient:
    def __init__(self, retry: int = 5, delay: int = 2, timeout: int = 10, proxy: Optional[str] = None):
        self.retry = retry
        self.delay = delay
        self.timeout = timeout
        self.proxy = proxy
        
        # 尝试检测系统代理
        if not proxy:
            self.proxy = self._detect_system_proxy()
            if self.proxy:
                print(f"已检测到系统代理: {self.proxy}")

    def _detect_system_proxy(self) -> Optional[str]:
        """尝试检测系统代理设置"""
        try:
            # 检查环境变量
            for var in ['http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY']:
                if var in os.environ and os.environ[var]:
                    return os.environ[var]
                    
            # 在Windows上尝试检测IE代理设置
            if platform.system() == 'Windows':
                try:
                    import winreg
                    registry = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
                    proxy_key = winreg.OpenKey(registry, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
                    proxy_enable, _ = winreg.QueryValueEx(proxy_key, "ProxyEnable")
                    
                    if proxy_enable:
                        proxy_server, _ = winreg.QueryValueEx(proxy_key, "ProxyServer")
                        if proxy_server:
                            # 确保格式是 http://host:port
                            if not proxy_server.startswith('http'):
                                proxy_server = f"http://{proxy_server}"
                            return proxy_server
                except Exception as e:
                    print(f"检测Windows代理设置出错: {str(e)}")
        except Exception as e:
            print(f"检测系统代理出错: {str(e)}")
        return None

    def get(self, url: str, cookies: Optional[dict] = None) -> Optional[bytes]:
        from curl_cffi import requests as curl_requests
        
        for attempt in range(self.retry):
            try:
                print(f"尝试获取 URL: {url} (尝试 {attempt + 1}/{self.retry})")
                
                # 准备请求参数
                request_params = {
                    "url": url,
                    "headers": HEADERS,
                    "cookies": cookies,
                    "timeout": self.timeout,
                    "impersonate": "chrome110",  # 模拟Chrome 110版本
                    "verify": False
                }
                
                # 如果有代理，添加代理设置
                if self.proxy:
                    print(f"使用代理: {self.proxy}")
                    request_params["proxies"] = {"https": self.proxy, "http": self.proxy}
                
                # 使用curl_cffi的impersonate模式模拟Chrome浏览器
                response = curl_requests.get(**request_params)
                
                print(f"成功获取数据，状态码: {response.status_code}")
                
                # 检查响应内容
                if response.status_code == 200:
                    content = response.content
                    content_length = len(content) if content else 0
                    print(f"响应内容长度: {content_length} 字节")
                    
                    # 检查内容是否包含Cloudflare挑战或类似的反爬虫页面
                    if content and b"Checking your browser" in content or b"Just a moment" in content:
                        print(f"检测到Cloudflare挑战，尝试其他方法...")
                        # 增加延迟，让Cloudflare认为是真实用户
                        time.sleep(self.delay * 2)
                        continue
                    
                    return content
                elif response.status_code == 403:
                    print(f"请求被拒绝 (403 Forbidden)，可能是反爬虫措施，等待后重试...")
                    time.sleep(self.delay * 3)  # 更长的延迟
                else:
                    print(f"HTTP错误，状态码: {response.status_code}")
                    time.sleep(self.delay)
                    
            except Exception as e:
                error_message = str(e)
                logging.error(f"Failed to fetch data (attempt {attempt + 1}/{self.retry}): {error_message} url is: {url}")
                print(f"第 {attempt + 1} 次请求失败: {error_message}")
                
                # 对不同类型的错误进行分类处理
                if "Connection" in error_message or "Timeout" in error_message or "reset" in error_message:
                    print("网络连接问题，可能需要使用代理，稍后重试...")
                elif "SSL" in error_message:
                    print("SSL/TLS验证问题，尝试禁用验证...")
                elif "DNS" in error_message:
                    print("DNS解析问题，可能需要检查网络设置或使用代理...")
                
                time.sleep(self.delay)
        
        logging.error(f"Max retries reached. Failed to fetch data. url is: {url}")
        print(f"错误: 达到最大重试次数，无法获取数据: {url}")
        if not self.proxy:
            print("提示: 连接失败可能是因为网站限制了你的地区访问，请考虑使用代理。")
            print("可以使用命令行参数 --proxy http://your-proxy-server:port 来设置代理")
        return None

class VideoPlayer:
    def __init__(self, http_client: HttpClient):
        self.http_client = http_client
        try:
            self.instance = vlc.Instance()
            self.player = self.instance.media_player_new()
            self.current_media = None
            self.paused_position = 0
            logging.info("VLC player initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize VLC player: {e}")
            print(f"错误: 无法初始化VLC播放器: {str(e)}")
            raise

    def _fetch_metadata(self, movie_url: str) -> Optional[str]:
        """获取视频的UUID或直接的m3u8 URL"""
        print(f"正在获取视频元数据: {movie_url}")
        # 尝试多次获取，可能需要突破Cloudflare防护
        html = None
        for attempt in range(3):
            html_content = self.http_client.get(movie_url)
            if html_content:
                html = html_content.decode('utf-8', errors='ignore')
                break
            print(f"第 {attempt + 1} 次尝试获取HTML失败，等待后重试...")
            time.sleep(2)
            
        if not html:
            logging.error(f"Failed to fetch HTML for {movie_url}")
            print(f"错误: 无法获取网页内容")
            return None
            
        # 尝试多种正则表达式匹配模式，提高适应性
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
        
        print("开始尝试匹配视频源...")
        for i, pattern in enumerate(patterns):
            match = re.search(pattern, html)
            if match:
                print(f"成功通过模式 {i+1} 匹配到结果")
                
                # 根据不同模式处理匹配结果
                if i == 0:  # 原始模式：特殊格式的UUID
                    result = match.group(1)
                    uuid = "-".join(result.split("|")[::-1])
                    if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                        print(f"UUID格式验证通过: {uuid}")
                        return uuid
                elif i == 1:  # 模式1：直接找到的playlist链接中的UUID
                    uuid = match.group(1)
                    if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                        print(f"UUID格式验证通过: {uuid}")
                        return uuid
                elif i == 2:  # 模式2：video标签src中的URL
                    url_part = match.group(1)
                    # 检查是否是完整的m3u8链接
                    if url_part.endswith('.m3u8'):
                        print(f"直接找到m3u8链接: {url_part}")
                        direct_m3u8_url = url_part
                    else:
                        # 尝试从URL中提取UUID
                        uuid_match = re.search(r'/([a-f0-9-]+)/', url_part)
                        if uuid_match:
                            uuid = uuid_match.group(1)
                            if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                                print(f"UUID格式验证通过: {uuid}")
                                return uuid
                elif i == 3:  # 模式3：直接匹配UUID格式
                    uuid = match.group(0)
                    print(f"成功获取视频UUID: {uuid}")
                    if re.match(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', uuid):
                        print(f"UUID格式验证通过: {uuid}")
                        return uuid
                elif i in (4, 5):  # 模式4和5：直接的m3u8链接
                    direct_m3u8_url = match.group(1) if i == 5 else match.group(0)
                    print(f"直接找到m3u8链接: {direct_m3u8_url}")
                    # 不立即返回，继续尝试其他模式，优先使用UUID方式
        
        # 如果找到了直接的m3u8链接但没找到UUID，使用直接链接
        if direct_m3u8_url:
            print(f"未找到UUID，使用直接的m3u8链接: {direct_m3u8_url}")
            self.direct_url = direct_m3u8_url  # 保存直接URL供后续使用
            return "direct_url"  # 返回特殊标记
        
        # 保存HTML以便调试
        try:
            with open('debug_missav_html.txt', 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"已保存网页内容到 debug_missav_html.txt 用于调试")
        except Exception as e:
            print(f"保存调试信息时出错: {str(e)}")
            
        logging.error("Failed to match video source.")
        print(f"错误: 无法匹配视频源，请尝试手动访问URL并检查网站是否可用: {movie_url}")
        return None

    def _get_playlist_url(self, uuid: str) -> Optional[str]:
        """获取播放列表URL"""
        playlist_url = f"{VIDEO_M3U8_PREFIX}{uuid}{VIDEO_PLAYLIST_SUFFIX}"
        print(f"播放列表URL: {playlist_url}")
        
        # 验证URL是否可访问
        test_response = self.http_client.get(playlist_url)
        if not test_response:
            logging.error("Failed to access playlist URL.")
            print(f"错误: 无法访问播放列表URL")
            return None
            
        print(f"播放列表URL验证成功，可以访问")
        return playlist_url

    def get_stream_url(self, movie_url: str, quality: Optional[str] = None) -> Optional[str]:
        """获取视频流URL"""
        uuid_or_marker = self._fetch_metadata(movie_url)
        if not uuid_or_marker:
            return None
            
        # 检查是否是直接URL的标记
        if uuid_or_marker == "direct_url" and hasattr(self, 'direct_url'):
            print(f"使用直接的播放列表URL: {self.direct_url}")
            return self.direct_url

        # 标准UUID处理
        playlist_url = self._get_playlist_url(uuid_or_marker)
        if not playlist_url:
            return None
            
        # 尝试获取播放列表内容验证是否存在分辨率信息
        print(f"尝试分析播放列表内容...")
        playlist_content = self.http_client.get(playlist_url)
        if not playlist_content:
            print("无法获取播放列表内容，直接使用播放列表URL")
            return playlist_url
            
        playlist_content = playlist_content.decode('utf-8', errors='ignore')
        
        # 检查是否包含分辨率信息
        matches = re.findall(RESOLUTION_PATTERN, playlist_content)
        if not matches:
            print("播放列表中未找到分辨率信息，直接使用主播放列表")
            return playlist_url
            
        # 以下是原有逻辑，处理有多种分辨率的情况
        try:
            quality_map = {height: width for width, height in matches}
            quality_list = list(quality_map.keys())
            
            if quality is None:
                # 获取最高分辨率
                final_quality = quality_list[-1] + 'p'
                resolution_url = playlist_content.splitlines()[-2]
            else:
                # 获取最接近指定分辨率的选项
                target = int(quality.replace('p', ''))
                closest_height = min([int(h) for h in quality_list], key=lambda x: abs(x - target))
                final_quality = str(closest_height) + 'p'
                
                # 尝试找到对应分辨率的URL
                url_patterns = [
                    f"{quality_map[str(closest_height)]}x{closest_height}/video.m3u8",
                    f"{closest_height}p/video.m3u8"
                ]
                
                found = False
                for pattern in url_patterns:
                    if pattern in playlist_content:
                        lines = playlist_content.splitlines()
                        for i, line in enumerate(lines):
                            if pattern in line:
                                resolution_url = line
                                found = True
                                break
                        if found:
                            break
                
                if not found:
                    # 如果没找到，使用最后一个非注释行
                    non_comment_lines = [l for l in playlist_content.splitlines() if not l.startswith('#')]
                    resolution_url = non_comment_lines[-1] if non_comment_lines else playlist_content.splitlines()[-1]
                    
            print(f"选择质量: {final_quality}, URL: {resolution_url}")
            
            # 检查resolution_url是否是完整URL
            if resolution_url.startswith('http'):
                return resolution_url
            else:
                # 拼接相对路径
                base_url = '/'.join(playlist_url.split('/')[:-1])
                return f"{base_url}/{resolution_url}"
        except Exception as e:
            print(f"解析播放列表时出错: {str(e)}，将直接使用播放列表URL")
            return playlist_url

    def play(self, movie_url: str, quality: Optional[str] = None, window_handle: Optional[int] = None) -> bool:
        """播放视频"""
        print(f"开始播放: {movie_url}, 质量: {quality}")
        
        # 处理质量参数，确保格式正确
        if quality and not quality.endswith('p'):
            quality = f"{quality}p"
            
        # 获取流URL
        stream_url = self.get_stream_url(movie_url, quality)
        if not stream_url:
            logging.error("Failed to get stream URL.")
            print(f"错误: 无法获取视频流URL")
            return False
            
        # 创建媒体
        media = self.instance.media_new(stream_url)
        self.player.set_media(media)
        
        # 如果提供了窗口句柄，设置输出窗口
        if window_handle:
            if platform.system() == "Windows":
                self.player.set_hwnd(window_handle)
            else:
                self.player.set_xwindow(window_handle)
                
        # 开始播放
        play_result = self.player.play()
        
        if play_result == -1:
            logging.error("Failed to play video.")
            print(f"错误: 无法开始播放视频")
            return False
            
        return True

    def stop(self) -> None:
        """停止播放"""
        self.paused_position = 0
        self.player.stop()

    def pause(self) -> None:
        """暂停播放"""
        self.paused_position = self.player.get_position()
        self.player.pause()

    def resume(self) -> None:
        """恢复播放"""
        self.player.play()
        if self.paused_position > 0:
            self.player.set_position(self.paused_position)

    def set_volume(self, volume: int) -> None:
        """设置音量 (0-100)"""
        self.player.audio_set_volume(volume)

    def get_position(self) -> float:
        """获取播放位置 (0.0-1.0)"""
        return self.player.get_position()

    def set_position(self, position: float) -> None:
        """设置播放位置 (0.0-1.0)"""
        self.paused_position = position
        self.player.set_position(position)

    def get_length(self) -> int:
        """获取视频总长度(毫秒)"""
        return self.player.get_length()

    def is_playing(self) -> bool:
        """检查是否正在播放"""
        return self.player.is_playing()

    def release(self) -> None:
        """释放资源"""
        self.player.release()
        self.instance.release()

    def download_with_ffmpeg(self, movie_url: str, save_path: str, quality: Optional[str] = None) -> bool:
        """使用FFmpeg下载视频"""
        try:
            # 处理质量参数，确保格式正确
            if quality and not quality.endswith('p'):
                quality = f"{quality}p"
                
            # 获取视频流URL
            stream_url = self.get_stream_url(movie_url, quality)
            if not stream_url:
                print(f"错误: 无法获取视频流URL")
                return False
                
            print(f"开始下载视频: {movie_url}")
            print(f"保存路径: {save_path}")
            print(f"视频流URL: {stream_url}")
            
            # 确保下载目录存在
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            
            # 检查FFmpeg是否已安装
            if not check_ffmpeg_installation():
                print("错误: 请先安装FFmpeg才能下载视频")
                return False
                
            # 构建ffmpeg命令
            cmd = [
                "ffmpeg", 
                "-y",                  # 覆盖输出文件
                "-loglevel", "info",   # 信息级日志
                "-headers", f"User-Agent: {HEADERS['User-Agent']}\r\n",  # 设置User-Agent
                "-i", stream_url,      # 输入URL
                "-c", "copy",          # 复制编解码器（无需重编码）
                "-bsf:a", "aac_adtstoasc",  # 音频格式转换
                save_path              # 输出文件
            ]
            
            # 如果HTTP客户端有代理设置，为FFmpeg添加代理
            if self.http_client.proxy:
                proxy = self.http_client.proxy
                # 确保代理格式正确（http://host:port）
                if proxy.startswith('http://') or proxy.startswith('https://'):
                    cmd.extend(["-http_proxy", proxy])
                    print(f"为FFmpeg设置代理: {proxy}")
            
            print(f"执行命令: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                universal_newlines=True
            )
            
            # 读取输出直到进程结束
            for line in iter(process.stdout.readline, ''):
                print(f"FFmpeg: {line.strip()}")
                
            # 等待进程结束
            process.wait()
            
            # 检查进程退出码
            if process.returncode == 0:
                print(f"视频下载成功: {save_path}")
                return True
            else:
                print(f"视频下载失败，FFmpeg退出码: {process.returncode}")
                return False
                
        except Exception as e:
            error_msg = f"下载出错: {str(e)}"
            print(error_msg)
            logging.error(error_msg)
            return False

    def play_video(self, movie_url: str, quality: Optional[str] = None) -> bool:
        """播放视频"""
        # 创建窗口前先检查URL有效性
        print(f"开始处理视频链接: {movie_url}")
        
        # 确保窗口尚未创建或已关闭
        if self.window and self.window.winfo_exists():
            print("发现已存在的播放器窗口，关闭中...")
            self.window.destroy()
            self.window = None
            
        # 处理质量参数，确保格式正确
        if quality and not quality.endswith('p'):
            quality = f"{quality}p"
            
        # 获取视频流URL
        print("正在获取视频流URL...")
        stream_url = self.get_stream_url(movie_url, quality)
        if not stream_url:
            messagebox.showerror("错误", f"无法获取视频流URL，请检查网络连接或直接访问网站: {movie_url}")
            return False
            
        print(f"成功获取视频流URL: {stream_url}")
        
        # 保存当前视频信息用于下载
        self.current_video = {
            'url': movie_url,
            'stream_url': stream_url,
            'title': os.path.basename(movie_url),
            'quality': quality
        }
        
        # 创建主窗口
        try:
            self.window = tk.Tk()
            self.window.title(f"视频播放器 - {os.path.basename(movie_url)}")
            self.window.geometry("1280x720")
            self.window.protocol("WM_DELETE_WINDOW", self._on_close)
            
            # 尝试初始化VLC实例
            try:
                print("正在初始化VLC播放器...")
                self.instance = vlc.Instance('--input-repeat=1 --no-video-title-show')
            except Exception as e:
                print(f"VLC初始化错误: {str(e)}")
                messagebox.showerror("VLC错误", f"初始化VLC失败: {str(e)}\n请确保已安装VLC媒体播放器")
                self.window.destroy()
                return False
                
            self.player = self.instance.media_player_new()
            
            # 在Windows上设置窗口句柄
            if platform.system() == "Windows":
                self.window.update()
                self.player.set_hwnd(self.window.winfo_id())
            # 在Linux/macOS上设置XWindow
            else:
                self.player.set_xwindow(self.window.winfo_id())
            
            # 创建视频显示区域，用于捕获鼠标事件
            self.video_area = tk.Frame(self.window, bg="black")
            self.video_area.pack(expand=True, fill=tk.BOTH)
            
            # 创建右上角全屏按钮
            self.fullscreen_btn_frame = tk.Frame(self.window, bg="#000000")
            self.fullscreen_btn_frame.place(relx=1.0, rely=0.0, anchor="ne", width=50, height=50)
            self.fullscreen_btn = tk.Button(self.fullscreen_btn_frame, text="⛶", font=("Arial", 16), 
                                            command=self._toggle_fullscreen, bg="#333333", fg="white", 
                                            activebackground="#555555", activeforeground="white",
                                            relief=tk.FLAT, borderwidth=0, highlightthickness=0)
            self.fullscreen_btn.pack(fill=tk.BOTH, expand=True)
            
            # 创建控制框架
            self.control_frame = tk.Frame(self.window, bg="#333333")
            self.control_frame.pack(side=tk.BOTTOM, fill=tk.X)
            
            # 创建进度条
            self.progress_frame = tk.Frame(self.control_frame, bg="#333333")
            self.progress_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5, 0))
            
            # 播放时间标签
            self.time_label = tk.Label(self.progress_frame, text="00:00:00 / 00:00:00", bg="#333333", fg="white")
            self.time_label.pack(side=tk.LEFT, padx=5)
            
            # 播放进度条
            self.progress_var = tk.DoubleVar()
            self.progress_scale = tk.Scale(self.progress_frame, variable=self.progress_var, from_=0, to=100,
                                         orient=tk.HORIZONTAL, command=self._seek, 
                                         bg="#333333", fg="white", activebackground="#555555",
                                         highlightthickness=0, troughcolor="#555555", sliderlength=15)
            self.progress_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            
            # 创建按钮控制框架
            self.button_frame = tk.Frame(self.control_frame, bg="#333333")
            self.button_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
            
            # 播放/暂停按钮
            self.play_pause_btn = tk.Button(self.button_frame, text="暂停", command=self._toggle_play_pause,
                                          bg="#555555", fg="white", activebackground="#777777")
            self.play_pause_btn.pack(side=tk.LEFT, padx=5)
            
            # 停止按钮
            stop_btn = tk.Button(self.button_frame, text="停止", command=self._stop,
                               bg="#555555", fg="white", activebackground="#777777")
            stop_btn.pack(side=tk.LEFT, padx=5)
            
            # 音量控制
            volume_label = tk.Label(self.button_frame, text="音量:", bg="#333333", fg="white")
            volume_label.pack(side=tk.LEFT, padx=5)
            
            self.volume_var = tk.IntVar()
            self.volume_var.set(70)  # 默认音量70%
            
            volume_scale = tk.Scale(self.button_frame, variable=self.volume_var, from_=0, to=100,
                                  orient=tk.HORIZONTAL, command=self._change_volume,
                                  length=100, bg="#333333", fg="white", troughcolor="#555555",
                                  highlightthickness=0, sliderlength=15)
            volume_scale.pack(side=tk.LEFT, padx=5)
            
            # 保存视频按钮
            save_btn = tk.Button(self.button_frame, text="保存视频", command=self._save_video, 
                               bg="#4CAF50", fg="white", activebackground="#45a049")
            save_btn.pack(side=tk.LEFT, padx=10)
            
            # 全屏按钮
            fullscreen_btn = tk.Button(self.button_frame, text="全屏", command=self._toggle_fullscreen,
                                     bg="#555555", fg="white", activebackground="#777777")
            fullscreen_btn.pack(side=tk.RIGHT, padx=5)
            
            # 设置状态标签
            self.status_label = tk.Label(self.control_frame, text="正在加载...", anchor=tk.W, 
                                       bg="#333333", fg="white")
            self.status_label.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
            
            # 初始状态下控制栏隐藏
            self.is_fullscreen = False
            self.control_visible = True
            self.last_mouse_movement = time.time()
            
            # 创建一个媒体对象
            print(f"正在加载媒体: {stream_url}")
            try:
                media = self.instance.media_new(stream_url)
                media.get_mrl()
                self.player.set_media(media)
            except Exception as e:
                print(f"媒体加载错误: {str(e)}")
                messagebox.showerror("媒体错误", f"无法加载媒体: {str(e)}")
                self.window.destroy()
                return False
            
            # 设置初始音量
            self.player.audio_set_volume(self.volume_var.get())
            
            # 开始播放
            print("开始播放视频...")
            play_result = self.player.play()
            if play_result == -1:
                print("播放失败，返回代码: -1")
                messagebox.showerror("播放错误", "无法播放该视频")
                self.window.destroy()
                return False
                
            print(f"播放器状态: {self.player.get_state()}")
            
            # 启动更新计时器
            self._start_update_timer()
            
            # 绑定鼠标移动事件
            self.window.bind("<Motion>", self._on_mouse_move)
            
            # 绑定键盘事件
            self.window.bind("<space>", lambda e: self._toggle_play_pause())
            self.window.bind("<Escape>", lambda e: self._exit_fullscreen())
            self.window.bind("f", lambda e: self._toggle_fullscreen())
            self.window.bind("s", lambda e: self._save_video())
            
            # 3秒后自动隐藏控制栏
            self.window.after(3000, self._check_mouse_idle)
            
            print("视频播放器初始化完成")
            self.window.mainloop()
            return True
            
        except Exception as e:
            error_msg = f"播放器错误: {str(e)}"
            print(error_msg)
            logging.error(error_msg)
            
            if hasattr(self, 'window') and self.window and self.window.winfo_exists():
                messagebox.showerror("错误", error_msg)
                self.window.destroy()
                
            return False

    def _on_mouse_move(self, event):
        """处理鼠标移动事件"""
        self.last_mouse_movement = time.time()
        
        # 如果控制栏当前是隐藏的，则显示
        if not self.control_visible:
            self._show_controls()
    
    def _check_mouse_idle(self):
        """检查鼠标是否空闲"""
        # 如果是全屏模式，并且鼠标超过3秒未移动，则隐藏控制栏
        if self.is_fullscreen and time.time() - self.last_mouse_movement > 3:
            self._hide_controls()
        
        # 继续检查
        if hasattr(self, 'window') and self.window and self.window.winfo_exists():
            self.window.after(1000, self._check_mouse_idle)
    
    def _show_controls(self):
        """显示控制栏"""
        if not self.control_visible:
            self.control_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.fullscreen_btn_frame.place(relx=1.0, rely=0.0, anchor="ne", width=50, height=50)
            self.control_visible = True
    
    def _hide_controls(self):
        """隐藏控制栏"""
        if self.control_visible and self.is_fullscreen:
            self.control_frame.pack_forget()
            self.fullscreen_btn_frame.place_forget()
            self.control_visible = False

    def _exit_fullscreen(self):
        """退出全屏模式"""
        if self.is_fullscreen:
            print("强制退出全屏")
            self.window.attributes("-fullscreen", False)
            self.player.set_fullscreen(False)
            self.is_fullscreen = False
            self.fullscreen_btn.configure(text="⛶")
            # 确保控制栏可见
            self._show_controls()

    def exit_fullscreen(self):
        """退出全屏模式"""
        try:
            # 检查是否处于全屏状态
            if bool(self.root.attributes('-fullscreen')):
                # 恢复窗口标题栏
                self.root.overrideredirect(False)
                
                # 退出全屏
                self.root.attributes('-fullscreen', False)
                self.fullscreen_button.configure(text="全屏")
                
                # 重新显示所有UI元素
                controls_frame = self.root.nametowidget(self.video_frame.master.winfo_children()[0].winfo_name())
                controls_frame.pack(fill=tk.X, padx=5, pady=5, before=self.video_frame)
                self.status_bar.pack(fill=tk.X)
                
                # 恢复视频框架原有大小
                self.video_frame.configure(width=800, height=450)
                
                # 解除鼠标点击事件绑定
                self.video_frame.unbind("<Button-1>")
        except Exception as e:
            print(f"退出全屏错误: {str(e)}")
            
    def _update_ui(self):
        """更新UI元素"""
        try:
            # 更新播放进度
            if self.player.is_playing():
                # 获取播放进度百分比
                length = self.player.get_length()
                position = self.player.get_time()
                
                if length > 0:
                    progress = (position / length) * 100
                    self.progress_var.set(progress)
                    
                    # 更新状态标签，显示当前时间/总时间
                    current_time = self._format_time(position)
                    total_time = self._format_time(length)
                    self.status_label.config(text=f"播放中: {current_time} / {total_time}")
                    # 更新进度栏时间标签
                    self.time_label.config(text=f"{current_time} / {total_time}")
                else:
                    self.status_label.config(text="正在缓冲...")
                    self.time_label.config(text="00:00:00 / 00:00:00")
            elif self.player.get_state() == vlc.State.Paused:
                self.status_label.config(text="已暂停")
                self.play_pause_btn.config(text="播放")
            elif self.player.get_state() == vlc.State.Ended:
                self.status_label.config(text="播放结束")
                self.play_pause_btn.config(text="播放")
            elif self.player.get_state() == vlc.State.Error:
                self.status_label.config(text="播放错误")
            
            # 更新计时器
            self.update_id = self.window.after(1000, self._update_ui)
            
        except Exception as e:
            print(f"更新UI时出错: {str(e)}")
            # 尝试继续更新
            self.update_id = self.window.after(1000, self._update_ui)

    def _start_update_timer(self):
        """启动UI更新计时器"""
        self.update_id = self.window.after(1000, self._update_ui)
        print("UI更新计时器已启动")
        
    def _format_time(self, ms):
        """格式化毫秒为时:分:秒"""
        seconds = ms // 1000
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
        
    def _toggle_play_pause(self):
        """切换播放/暂停状态"""
        if self.player.is_playing():
            print("暂停视频")
            self.player.pause()
            self.play_pause_btn.config(text="播放")
        else:
            print("继续播放")
            self.player.play()
            self.play_pause_btn.config(text="暂停")
            
    def _stop(self):
        """停止播放"""
        print("停止播放")
        self.player.stop()
        
    def _change_volume(self, value):
        """调整音量"""
        volume = int(float(value))
        print(f"设置音量: {volume}%")
        self.player.audio_set_volume(volume)
        
    def _seek(self, value):
        """跳转到指定进度"""
        if self.player.get_length() > 0:
            position = (float(value) / 100.0) * self.player.get_length()
            print(f"跳转到: {self._format_time(int(position))}")
            self.player.set_time(int(position))
            
    def _on_close(self):
        """关闭窗口时的处理"""
        print("关闭播放器")
        if hasattr(self, 'update_id') and self.update_id:
            self.window.after_cancel(self.update_id)
            
        # 停止播放
        if self.player:
            self.player.stop()
            
        # 释放资源
        if self.instance:
            del self.player
            del self.instance
            
        # 关闭窗口
        if self.window:
            self.window.destroy()
            self.window = None

    def _save_video(self):
        """保存当前播放的视频"""
        if not hasattr(self, 'current_video') or not self.current_video:
            messagebox.showerror("错误", "没有可保存的视频")
            return
            
        # 创建下载目录
        if not os.path.exists(DOWNLOAD_FOLDER):
            os.makedirs(DOWNLOAD_FOLDER)
            
        # 显示保存对话框
        save_path = filedialog.asksaveasfilename(
            initialdir=DOWNLOAD_FOLDER,
            initialfile=f"{self.current_video['title']}.mp4",
            defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4"), ("所有文件", "*.*")]
        )
        
        if not save_path:
            print("保存已取消")
            return
            
        # 创建下载进度窗口
        progress_window = tk.Toplevel(self.window)
        progress_window.title("下载进度")
        progress_window.geometry("400x150")
        progress_window.resizable(False, False)
        progress_window.transient(self.window)
        progress_window.grab_set()
        
        # 添加进度条
        progress_label = tk.Label(progress_window, text=f"正在下载: {os.path.basename(save_path)}")
        progress_label.pack(pady=10)
        
        progress_bar = ttk.Progressbar(progress_window, orient="horizontal", length=350, mode="indeterminate")
        progress_bar.pack(pady=10)
        progress_bar.start()
        
        status_label = tk.Label(progress_window, text="准备下载...")
        status_label.pack(pady=10)
        
        # 在新线程中执行下载
        def download_thread():
            try:
                progress_window.after(100, lambda: status_label.config(text="开始下载..."))
                success, msg = self._download_m3u8_video(self.current_video['stream_url'], save_path, status_label, progress_window)
                
                if success:
                    progress_window.after(100, lambda: messagebox.showinfo("完成", f"视频已保存到: {save_path}"))
                else:
                    progress_window.after(100, lambda: messagebox.showerror("下载失败", msg))
                    
                progress_window.after(100, lambda: progress_window.destroy())
                
            except Exception as e:
                error_msg = f"下载出错: {str(e)}"
                print(error_msg)
                progress_window.after(100, lambda: messagebox.showerror("错误", error_msg))
                progress_window.after(100, lambda: progress_window.destroy())
                
        import threading
        download_thread = threading.Thread(target=download_thread)
        download_thread.daemon = True
        download_thread.start()
        
    def _download_m3u8_video(self, m3u8_url, save_path, status_label, progress_window):
        """使用FFmpeg下载m3u8视频"""
        try:
            # 首先检查FFmpeg是否已安装
            if not check_ffmpeg_installation():
                return False, "请先安装FFmpeg才能下载视频"
                
            # 使用FFmpeg下载
            progress_window.after(100, lambda: status_label.config(text="正在使用FFmpeg下载..."))
            
            # 构建命令，添加错误处理和重试
            cmd = [
                "ffmpeg", 
                "-y",  # 覆盖输出文件
                "-loglevel", "warning",  # 降低日志级别
                "-i", m3u8_url,  # 输入URL
                "-c", "copy",  # 复制编解码器（无需重编码）
                "-bsf:a", "aac_adtstoasc",  # 音频格式转换
                save_path  # 输出文件
            ]
            
            print(f"执行命令: {' '.join(cmd)}")
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            
            # 监控进程输出
            while process.poll() is None:
                output_line = process.stderr.readline().strip()
                if output_line:
                    print(f"FFmpeg: {output_line}")
                    progress_window.after(1, lambda line=output_line: status_label.config(text=f"下载中: {line[:50]}..."))
                    
            # 检查是否成功
            if process.returncode == 0:
                print("下载完成")
                progress_window.after(1, lambda: status_label.config(text="下载完成！"))
                return True, "下载完成"
            else:
                stderr = process.stderr.read()
                error_msg = f"FFmpeg下载失败，代码: {process.returncode}\n{stderr}"
                print(error_msg)
                return False, error_msg
                
        except Exception as e:
            error_msg = f"下载过程中发生错误: {str(e)}"
            print(error_msg)
            return False, error_msg

    def _toggle_fullscreen(self):
        """切换全屏模式"""
        if self.is_fullscreen:
            print("退出全屏")
            self.window.attributes("-fullscreen", False)
            self.player.set_fullscreen(False)
            self.is_fullscreen = False
            self.fullscreen_btn.configure(text="⛶")
            # 确保控制栏可见
            self._show_controls()
        else:
            print("进入全屏")
            self.window.attributes("-fullscreen", True)
            self.player.set_fullscreen(True)
            self.is_fullscreen = True
            self.fullscreen_btn.configure(text="⮌")
            # 3秒后隐藏控制栏
            self.window.after(3000, lambda: self._hide_controls() if self.is_fullscreen else None)

class VideoPlayerGUI:
    def __init__(self, root, http_client=None):
        self.root = root
        self.root.title("MissAV Video Player")
        self.root.geometry("800x600")
        
        # 创建下载目录
        if not os.path.exists(DOWNLOAD_FOLDER):
            os.makedirs(DOWNLOAD_FOLDER)
        
        try:
            # 使用传入的HTTP客户端或创建新的
            self.http_client = http_client if http_client else HttpClient()
            self.player = VideoPlayer(self.http_client)
            
            self.setup_ui()
            logging.info("GUI initialized successfully")
        except Exception as e:
            logging.error(f"Failed to initialize GUI: {e}")
            print(f"GUI初始化失败: {str(e)}")
            messagebox.showerror("Error", f"Failed to initialize GUI: {e}")
            raise
        
    def setup_ui(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 创建顶部控制区域
        controls_frame = ttk.Frame(main_frame)
        controls_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # URL输入框和播放控制区域
        url_frame = ttk.Frame(controls_frame)
        url_frame.pack(fill=tk.X)
        
        # URL输入框
        ttk.Label(url_frame, text="视频URL:").grid(row=0, column=0, sticky=tk.W)
        self.url_entry = ttk.Entry(url_frame, width=50)
        self.url_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(5, 5))
        
        # 质量选择
        ttk.Label(url_frame, text="质量:").grid(row=0, column=2, sticky=tk.W, padx=(10, 0))
        self.quality_var = tk.StringVar()
        quality_combo = ttk.Combobox(url_frame, textvariable=self.quality_var, values=["自动", "1080", "720", "480", "360"], width=8)
        quality_combo.grid(row=0, column=3, sticky=tk.W, padx=(5, 10))
        quality_combo.set("720")
        
        # 播放按钮区域
        btn_frame = ttk.Frame(url_frame)
        btn_frame.grid(row=0, column=4, columnspan=4, sticky=tk.E)
        
        # 播放按钮
        self.play_button = ttk.Button(btn_frame, text="播放", command=self.play_video, width=8)
        self.play_button.pack(side=tk.LEFT, padx=2)
        
        # 暂停按钮
        self.pause_button = ttk.Button(btn_frame, text="暂停", command=self.pause_video, width=8)
        self.pause_button.pack(side=tk.LEFT, padx=2)
        self.pause_button.state(['disabled'])
        
        # 停止按钮
        self.stop_button = ttk.Button(btn_frame, text="停止", command=self.stop_video, width=8)
        self.stop_button.pack(side=tk.LEFT, padx=2)
        
        # 全屏按钮
        self.fullscreen_button = ttk.Button(btn_frame, text="全屏", command=self.toggle_fullscreen, width=8)
        self.fullscreen_button.pack(side=tk.LEFT, padx=2)
        
        # 下载按钮
        self.download_button = ttk.Button(btn_frame, text="下载", command=self.download_video, width=8)
        self.download_button.pack(side=tk.LEFT, padx=2)
        
        # 帮助按钮
        help_button = ttk.Button(btn_frame, text="帮助", command=self.show_help, width=8)
        help_button.pack(side=tk.LEFT, padx=2)
        
        # 代理设置区域
        proxy_frame = ttk.Frame(controls_frame)
        proxy_frame.pack(fill=tk.X, pady=(5, 0))
        
        # 代理输入框
        ttk.Label(proxy_frame, text="代理:").grid(row=0, column=0, sticky=tk.W)
        self.proxy_var = tk.StringVar()
        if self.http_client.proxy:
            self.proxy_var.set(self.http_client.proxy)
        self.proxy_entry = ttk.Entry(proxy_frame, textvariable=self.proxy_var, width=40)
        self.proxy_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(5, 5))
        ttk.Label(proxy_frame, text="示例: http://127.0.0.1:7890").grid(row=0, column=2, sticky=tk.W)
        
        # 应用代理按钮
        self.apply_proxy_button = ttk.Button(proxy_frame, text="应用代理", command=self.apply_proxy, width=10)
        self.apply_proxy_button.grid(row=0, column=3, padx=5)
        
        # 音量控制区域
        volume_frame = ttk.Frame(controls_frame)
        volume_frame.pack(fill=tk.X, pady=(5, 0))
        
        # 音量控制
        ttk.Label(volume_frame, text="音量:").pack(side=tk.LEFT)
        self.volume_scale = ttk.Scale(volume_frame, from_=0, to=100, orient=tk.HORIZONTAL, command=self.set_volume, length=200)
        self.volume_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        self.volume_scale.set(70)
        
        # 音量显示值
        self.volume_label = ttk.Label(volume_frame, text="70%", width=5)
        self.volume_label.pack(side=tk.LEFT)
        
        # 创建播放进度区域
        progress_frame = ttk.Frame(controls_frame)
        progress_frame.pack(fill=tk.X, pady=(5, 0))
        
        # 时间标签 (当前时间)
        self.time_current = ttk.Label(progress_frame, text="00:00:00")
        self.time_current.pack(side=tk.LEFT)
        
        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_scale = ttk.Scale(progress_frame, variable=self.progress_var, from_=0, to=1, orient=tk.HORIZONTAL, 
                                       command=self.set_position)
        self.progress_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        self.progress_scale.bind("<ButtonRelease-1>", self.on_progress_release)
        
        # 总时长标签
        self.time_total = ttk.Label(progress_frame, text="00:00:00")
        self.time_total.pack(side=tk.LEFT)
        
        # 视频显示区域
        self.video_frame = ttk.Frame(main_frame, width=800, height=450, relief="sunken", borderwidth=1)
        self.video_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 状态栏
        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        self.status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(fill=tk.X)
        
        # 设置列和行的权重，确保组件可以适当调整大小
        url_frame.columnconfigure(1, weight=3)  # URL输入框占更多空间
        proxy_frame.columnconfigure(1, weight=1) # 代理输入框可以扩展
        
        # 为确保UI元素正确显示，设置最小窗口大小
        self.root.update()
        self.root.minsize(800, 600)
        
        # 初始化全屏状态变量
        self.is_fullscreen = False
        self.controls_visible = True
        self.last_mouse_movement = time.time()
        
        # 绑定窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 绑定鼠标移动事件
        self.root.bind("<Motion>", self._on_mouse_move)
        
        # 开始更新进度条
        self.update_progress()
        
    def apply_proxy(self):
        """应用新的代理设置"""
        proxy = self.proxy_var.get().strip()
        
        # 验证代理格式
        if proxy and not (proxy.startswith('http://') or proxy.startswith('https://') or proxy.startswith('socks5://')):
            messagebox.showwarning("代理格式错误", "代理地址应以 http://, https:// 或 socks5:// 开头")
            return
            
        # 更新HTTP客户端的代理设置
        self.http_client.proxy = proxy if proxy else None
        
        if proxy:
            self.status_var.set(f"已设置代理: {proxy}")
            messagebox.showinfo("代理设置", f"已成功设置代理: {proxy}")
        else:
            self.status_var.set("已清除代理设置")
            messagebox.showinfo("代理设置", "已清除代理设置，将直接连接")
        
    def play_video(self):
        url = self.url_entry.get()
        if not url:
            messagebox.showwarning("Warning", "请输入视频URL")
            return
            
        quality = self.quality_var.get()
        quality = None if quality == "自动" else quality
        
        # 更新状态
        self.status_var.set(f"正在加载视频: {url}")
        
        # 获取视频框架的窗口句柄
        window_handle = self.video_frame.winfo_id()
        logging.info(f"Playing video with window handle: {window_handle}")
        print(f"开始播放视频，窗口句柄: {window_handle}")
        
        if self.player.play(url, quality, window_handle):
            self.play_button.state(['disabled'])
            self.pause_button.state(['!disabled'])
            self.stop_button.state(['!disabled'])
            logging.info("Video playback started successfully")
            print("视频开始播放")
            self.status_var.set(f"正在播放: {url}")
        else:
            messagebox.showerror("Error", "无法开始播放视频")
            print("无法开始播放视频")
            self.status_var.set("播放失败")
    
    def pause_video(self):
        if self.player.is_playing():
            self.player.pause()
            self.pause_button.configure(text="继续")
        else:
            self.player.resume()
            self.pause_button.configure(text="暂停")
    
    def stop_video(self):
        self.player.stop()
        self.play_button.state(['!disabled'])
        self.pause_button.state(['disabled'])
        self.pause_button.configure(text="暂停")
        self.stop_button.state(['disabled'])
        self.progress_var.set(0)
    
    def set_volume(self, value):
        """设置音量"""
        volume = int(float(value))
        self.player.set_volume(volume)
        if hasattr(self, 'volume_label'):
            self.volume_label.config(text=f"{volume}%")
        
    def set_position(self, value):
        # 只在拖动时更新值，但不立即设置位置
        self.progress_var.set(float(value))
    
    def on_progress_release(self, event):
        # 松开鼠标时才设置位置
        if self.player.is_playing() or self.pause_button.cget('text') == "继续":
            position = self.progress_var.get()
            print(f"设置播放位置到: {position}")
            self.player.set_position(position)
    
    def update_progress(self):
        """更新进度条和时间显示"""
        try:
            if self.player and self.player.is_playing():
                position = self.player.get_position()
                if not self.progress_scale.instate(['pressed']):  # 只在不拖动时更新
                    self.progress_var.set(position)
                
                # 更新时间显示
                length = self.player.get_length()
                if length > 0:
                    current_ms = int(position * length)
                    total_ms = length
                    current_time = self._format_time(current_ms)
                    total_time = self._format_time(total_ms)
                    
                    # 更新时间标签
                    self.time_current.config(text=current_time)
                    self.time_total.config(text=total_time)
                    
                    # 更新状态栏
                    self.status_var.set(f"播放中: {os.path.basename(self.url_entry.get())} - {current_time} / {total_time}")
                    
            self.root.after(1000, self.update_progress)
        except Exception as e:
            logging.error(f"Failed to update progress: {e}")
            self.root.after(1000, self.update_progress)  # 即使出错也继续更新
    
    def _format_time(self, ms):
        """格式化毫秒为时:分:秒"""
        seconds = ms // 1000
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    
    def download_video(self):
        url = self.url_entry.get()
        if not url:
            messagebox.showwarning("Warning", "请输入视频URL")
            return
        
        if not check_ffmpeg_installation():
            messagebox.showerror("Error", "FFmpeg未安装或不可用")
            return
        
        quality = self.quality_var.get()
        quality = None if quality == "自动" else quality
        
        # 更新状态
        self.status_var.set(f"准备下载视频: {url}")
        
        # 从URL中提取影片ID
        movie_id = url.split('/')[-1]
        save_path = filedialog.asksaveasfilename(
            initialdir=DOWNLOAD_FOLDER,
            initialfile=f"{movie_id}_{quality if quality else 'auto'}.mp4",
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4")]
        )
        
        if not save_path:
            self.status_var.set("下载已取消")
            return
        
        # 显示正在下载的信息
        self.download_button.state(['disabled'])
        self.download_button.configure(text="下载中...")
        self.status_var.set(f"正在解析视频源: {url}")
        
        # 在新线程中下载视频
        def download_thread():
            try:
                # 更新状态
                self.root.after(0, lambda: self.status_var.set(f"正在获取视频流URL..."))
                
                # 获取视频流URL
                stream_url = self.player.get_stream_url(url, quality)
                if not stream_url:
                    self.root.after(0, lambda: self.download_complete(False, "无法获取视频流URL，请检查网络或使用代理"))
                    return
                
                self.root.after(0, lambda: self.status_var.set(f"开始下载视频到: {os.path.basename(save_path)}"))
                
                # 下载视频
                success = self.player.download_with_ffmpeg(url, save_path, quality)
                self.root.after(0, lambda: self.download_complete(success, save_path))
            except Exception as e:
                error_msg = f"下载失败: {str(e)}"
                logging.error(error_msg)
                self.root.after(0, lambda: self.download_complete(False, error_msg))
        
        thread = threading.Thread(target=download_thread)
        thread.daemon = True
        thread.start()
    
    def download_complete(self, success, result):
        self.download_button.state(['!disabled'])
        self.download_button.configure(text="下载")
        
        if success:
            self.status_var.set(f"下载完成: {os.path.basename(result)}")
            messagebox.showinfo("下载完成", f"视频已下载到:\n{result}")
        else:
            self.status_var.set(f"下载失败: {result}")
            
            # 如果没有设置代理，提示用户考虑使用代理
            if not self.http_client.proxy:
                messagebox.showerror("下载失败", f"{result}\n\n提示: 如果您的网络无法直接访问该网站，请考虑使用代理。")
            else:
                messagebox.showerror("下载失败", f"{result}")
                
    def on_closing(self):
        # 确保退出全屏
        try:
            self.exit_fullscreen()
        except:
            pass
            
        if hasattr(self, 'player'):
            self.player.release()
        self.root.destroy()

    def show_help(self):
        """显示帮助对话框"""
        help_window = tk.Toplevel(self.root)
        help_window.title("使用帮助")
        help_window.geometry("600x500")
        help_window.transient(self.root)  # 设置为主窗口的子窗口
        help_window.grab_set()  # 模态对话框
        
        # 使用选项卡组织不同类别的帮助信息
        notebook = ttk.Notebook(help_window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 基本使用选项卡
        basic_frame = ttk.Frame(notebook)
        notebook.add(basic_frame, text="基本使用")
        
        basic_text = tk.Text(basic_frame, wrap=tk.WORD, padx=10, pady=10)
        basic_text.pack(fill=tk.BOTH, expand=True)
        basic_text.insert(tk.END, """
基本使用说明:

1. 播放视频:
   - 在顶部输入框中输入视频URL (例如: https://missav.ai/KTRA-678)
   - 从质量下拉菜单选择所需的视频质量
   - 点击"播放"按钮开始播放
   
2. 控制播放:
   - 暂停/继续: 点击"暂停"按钮或按空格键
   - 停止: 点击"停止"按钮
   - 调整音量: 使用音量滑块
   - 全屏: 按F键切换全屏模式
   - 跳转: 拖动进度条以跳转到视频的不同部分
   
3. 下载视频:
   - 输入视频URL并选择质量
   - 点击"下载"按钮
   - 选择保存位置
   - 等待下载完成
        """)
        basic_text.config(state=tk.DISABLED)  # 设为只读
        
        # 代理设置选项卡
        proxy_frame = ttk.Frame(notebook)
        notebook.add(proxy_frame, text="代理设置")
        
        proxy_text = tk.Text(proxy_frame, wrap=tk.WORD, padx=10, pady=10)
        proxy_text.pack(fill=tk.BOTH, expand=True)
        proxy_text.insert(tk.END, """
代理设置说明:

由于网站限制，在某些地区可能需要使用代理才能正常访问和播放视频。

1. 设置代理:
   - 在"代理"输入框中输入代理地址 (例如: http://127.0.0.1:7890)
   - 点击"应用代理"按钮使其生效
   
2. 支持的代理格式:
   - HTTP代理: http://host:port
   - HTTPS代理: https://host:port
   - SOCKS5代理: socks5://host:port
   
3. 常见代理工具:
   - Clash
   - V2Ray
   - Shadowsocks
   
4. 检查代理是否生效:
   如果设置正确，状态栏会显示"已设置代理"，且视频应该能够正常加载。
        """)
        proxy_text.config(state=tk.DISABLED)  # 设为只读
        
        # 命令行使用选项卡
        cli_frame = ttk.Frame(notebook)
        notebook.add(cli_frame, text="命令行使用")
        
        cli_text = tk.Text(cli_frame, wrap=tk.WORD, padx=10, pady=10)
        cli_text.pack(fill=tk.BOTH, expand=True)
        cli_text.insert(tk.END, """
命令行使用说明:

除了GUI界面，本程序也支持命令行操作:

1. 播放视频:
   python video_player2.py https://missav.ai/VIDEO-ID
   
2. 下载视频:
   python video_player2.py https://missav.ai/VIDEO-ID --download -o 保存路径.mp4
   
3. 指定质量:
   python video_player2.py https://missav.ai/VIDEO-ID --quality 720
   
4. 使用代理:
   python video_player2.py https://missav.ai/VIDEO-ID --proxy http://127.0.0.1:7890
   
5. 查看帮助:
   python video_player2.py --help
        """)
        cli_text.config(state=tk.DISABLED)  # 设为只读
        
        # 故障排除选项卡
        trouble_frame = ttk.Frame(notebook)
        notebook.add(trouble_frame, text="故障排除")
        
        trouble_text = tk.Text(trouble_frame, wrap=tk.WORD, padx=10, pady=10)
        trouble_text.pack(fill=tk.BOTH, expand=True)
        trouble_text.insert(tk.END, """
常见问题和解决方法:

1. 无法获取视频流URL
   - 检查网络连接
   - 尝试使用代理
   - 确认您输入的URL格式正确
   
2. 播放器无法启动
   - 确保已安装VLC媒体播放器
   - 确保已安装python-vlc模块 (pip install python-vlc)
   
3. 下载失败
   - 确保已安装FFmpeg并添加到系统PATH
   - 检查磁盘空间是否充足
   - 尝试使用代理下载
   
4. 视频卡顿或无法加载
   - 尝试选择较低的视频质量
   - 检查网络速度是否稳定
   - 使用代理可能会提高加载速度
   
5. 程序崩溃
   - 查看控制台输出的错误信息
   - 尝试更新Python和相关模块
   - 检查VLC播放器版本是否兼容
        """)
        trouble_text.config(state=tk.DISABLED)  # 设为只读
        
        # 关于选项卡
        about_frame = ttk.Frame(notebook)
        notebook.add(about_frame, text="关于")
        
        about_text = tk.Text(about_frame, wrap=tk.WORD, padx=10, pady=10)
        about_text.pack(fill=tk.BOTH, expand=True)
        about_text.insert(tk.END, """
关于 MissAV Video Player:

版本: 1.0.0
更新日期: 2025-03-20

本程序是一个视频播放器和下载工具，专为特定网站设计。

依赖项:
- Python 3.7+
- VLC媒体播放器
- python-vlc
- curl_cffi
- FFmpeg (用于下载功能)

使用须知:
本程序仅用于个人学习和研究目的，请尊重内容版权和相关法律法规。用户需自行承担使用本程序的一切责任。

如有任何问题或建议，请联系开发者。
        """)
        about_text.config(state=tk.DISABLED)  # 设为只读
        
        # 添加关闭按钮
        close_button = ttk.Button(help_window, text="关闭", command=help_window.destroy)
        close_button.pack(pady=10)

    def toggle_fullscreen(self):
        """切换纯视频全屏模式"""
        try:
            # 纯视频全屏模式 - 隐藏所有UI元素，只保留视频区域
            if self.is_fullscreen:
                # 退出全屏模式
                print("退出纯视频全屏模式")
                self.root.attributes('-fullscreen', False)
                self.fullscreen_button.configure(text="全屏")
                
                # 恢复窗口标题栏
                self.root.overrideredirect(False)
                
                # 重新显示所有控制UI
                if not self.controls_visible:
                    self._show_controls()
                
                # 恢复视频框架原有大小
                self.video_frame.configure(width=800, height=450)
                
                # 更新状态
                self.is_fullscreen = False
                self.controls_visible = True
                
                # 解除鼠标点击事件绑定
                self.video_frame.unbind("<Double-Button-1>")
            else:
                # 进入纯视频全屏模式
                print("进入纯视频全屏模式")
                
                # 先隐藏所有控制UI元素
                if self.controls_visible:
                    self._hide_controls()
                
                # 设置全屏
                self.root.attributes('-fullscreen', True)
                
                # 可选：隐藏窗口标题栏，实现真正的纯视频界面
                self.root.overrideredirect(True)
                
                # 将视频区域设置为全屏大小
                self.video_frame.configure(width=self.root.winfo_width(), height=self.root.winfo_height())
                
                # 更新状态
                self.is_fullscreen = True
                self.controls_visible = False
                
                # 绑定双击退出全屏
                self.video_frame.bind("<Double-Button-1>", lambda e: self.exit_fullscreen())
                
            # 绑定键盘快捷键控制
            self.root.bind("<f>", lambda e: self.toggle_fullscreen())
            self.root.bind("<F>", lambda e: self.toggle_fullscreen())
            self.root.bind("<Escape>", lambda e: self.exit_fullscreen())
                
        except Exception as e:
            print(f"全屏切换错误: {str(e)}")
            messagebox.showerror("错误", f"无法切换全屏: {str(e)}")
            
    def exit_fullscreen(self):
        """退出全屏模式"""
        if self.is_fullscreen:
            try:
                # 恢复窗口标题栏
                self.root.overrideredirect(False)
                
                # 退出全屏
                self.root.attributes('-fullscreen', False)
                self.fullscreen_button.configure(text="全屏")
                
                # 重新显示所有UI元素
                if not self.controls_visible:
                    self._show_controls()
                
                # 恢复视频框架原有大小
                self.video_frame.configure(width=800, height=450)
                
                # 解除鼠标点击事件绑定
                self.video_frame.unbind("<Double-Button-1>")
                
                # 更新状态
                self.is_fullscreen = False
                self.controls_visible = True
                
            except Exception as e:
                print(f"退出全屏错误: {str(e)}")

    def _on_mouse_move(self, event):
        """处理鼠标移动事件，全屏模式下显示控制UI"""
        self.last_mouse_movement = time.time()
        
        # 如果处于全屏模式且控制栏被隐藏，则显示控制栏
        if self.is_fullscreen and not self.controls_visible:
            self._show_controls()
            # 3秒后再次检查是否应该隐藏控制栏
            self.root.after(3000, self._check_mouse_idle)
        
    def _check_mouse_idle(self):
        """检查鼠标是否空闲，如果空闲且处于全屏模式，则隐藏控制栏"""
        if self.is_fullscreen and time.time() - self.last_mouse_movement > 2.5:
            self._hide_controls()
    
    def _show_controls(self):
        """显示控制栏"""
        if self.is_fullscreen and not self.controls_visible:
            try:
                # 显示控制栏但保持全屏状态
                controls_frame = self.root.nametowidget(self.video_frame.master.winfo_children()[0].winfo_name())
                controls_frame.pack(fill=tk.X, padx=5, pady=5, before=self.video_frame)
                self.status_bar.pack(fill=tk.X)
                self.controls_visible = True
                print("显示控制栏")
            except Exception as e:
                print(f"显示控制栏错误: {str(e)}")
    
    def _hide_controls(self):
        """隐藏控制栏"""
        if self.is_fullscreen and self.controls_visible:
            try:
                # 隐藏控制栏但保持全屏状态
                controls_frame = self.root.nametowidget(self.video_frame.master.winfo_children()[0].winfo_name())
                controls_frame.pack_forget()
                self.status_bar.pack_forget()
                self.controls_visible = False
                print("隐藏控制栏")
            except Exception as e:
                print(f"隐藏控制栏错误: {str(e)}")

def main():
    """主函数入口点，支持命令行运行"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='MissAV视频播放器')
    parser.add_argument('url', nargs='?', help='要播放的视频URL')
    parser.add_argument('--download', '-d', action='store_true', help='仅下载视频而不播放')
    parser.add_argument('--output', '-o', help='下载视频的保存路径')
    parser.add_argument('--proxy', '-p', help='指定代理服务器，格式如 http://127.0.0.1:7890')
    parser.add_argument('--quality', '-q', help='指定视频质量，如720p, 1080p等')
    args = parser.parse_args()
    
    # 检查VLC安装
    try:
        import vlc
    except ImportError:
        print("错误: 未安装python-vlc模块。请运行命令安装: pip install python-vlc")
        print("注意: 您还需要安装VLC媒体播放器")
        sys.exit(1)
    
    # 如果未提供URL，打开GUI模式
    if not args.url:
        # 创建Tk根窗口
        root = tk.Tk()
        root.title("MissAV Video Player")
        root.geometry("900x650")  # 设置初始窗口大小
        
        # 设置应用程序图标 (如果有)
        try:
            if platform.system() == "Windows":
                root.iconbitmap(default="icon.ico")
            else:
                img = tk.PhotoImage(file="icon.png")
                root.tk.call('wm', 'iconphoto', root._w, img)
        except Exception:
            pass  # 忽略图标加载错误
        
        # 创建HTTP客户端（带代理支持）
        http_client = HttpClient(retry=5, delay=2, timeout=10, proxy=args.proxy)
        
        # 创建播放器GUI（传递HTTP客户端）
        player_gui = VideoPlayerGUI(root, http_client)
        
        # 居中显示窗口
        root.update_idletasks()
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x = (screen_width - root.winfo_width()) // 2
        y = (screen_height - root.winfo_height()) // 2
        root.geometry(f"+{x}+{y}")
        
        root.mainloop()
    else:
        # 创建HTTP客户端
        http_client = HttpClient(retry=5, delay=2, timeout=10, proxy=args.proxy)
        # 创建视频播放器
        player = VideoPlayer(http_client)
        
        if args.download:
            # 仅下载模式
            print(f"准备下载视频: {args.url}")
            
            # 确定输出文件名
            output_file = args.output if args.output else os.path.join(
                DOWNLOAD_FOLDER, 
                f"{os.path.basename(args.url)}.mp4"
            )
            
            # 创建下载目录
            os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
            
            # 下载视频
            success = player.download_with_ffmpeg(args.url, output_file, args.quality)
            
            if success:
                print(f"视频下载完成: {output_file}")
            else:
                print(f"视频下载失败")
                sys.exit(1)
        else:
            # 播放模式
            print(f"准备播放视频: {args.url}")
            player.play_video(args.url)


if __name__ == "__main__":
    main() 