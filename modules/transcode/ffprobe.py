"""
FFprobe 媒体信息获取模块

使用 ffprobe 获取视频的媒体信息，包括时长、编码格式等。
这是实现流畅播放体验的前提：预先知道视频总时长。
"""

import json
import subprocess
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class FFprobeRunner:
    """FFprobe 运行器

    使用 ffprobe 获取视频流的媒体信息。
    """

    def __init__(self, ffprobe_path: str = "ffprobe"):
        """初始化 FFprobe 运行器

        Args:
            ffprobe_path: ffprobe 可执行文件路径
        """
        self.ffprobe_path = ffprobe_path

    def get_media_info(
        self,
        source_url: str,
        headers: Optional[str] = None,
        timeout: int = 30
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """获取媒体信息

        Args:
            source_url: 视频源 URL
            headers: HTTP 请求头（用于 115 直链）
            timeout: 超时时间（秒）

        Returns:
            (成功标志, 媒体信息字典, 错误信息)
        """
        try:
            cmd = [
                self.ffprobe_path,
                "-hide_banner",
                "-loglevel", "error",
                "-show_format",
                "-show_streams",
                "-print_format", "json",
                source_url
            ]

            # 添加 HTTP 头
            env = None
            if headers:
                # 构建带 HTTP 头的命令
                cmd = [
                    self.ffprobe_path,
                    "-hide_banner",
                    "-loglevel", "error",
                    "-headers", headers,
                    "-show_format",
                    "-show_streams",
                    "-print_format", "json",
                    source_url
                ]

            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() or "Unknown ffprobe error"
                logger.warning(f"ffprobe error (code {result.returncode}): {error_msg}")
                # 记录 URL 的前 100 个字符用于调试
                url_preview = source_url[:100] + "..." if len(source_url) > 100 else source_url
                logger.warning(f"ffprobe failed for URL: {url_preview}")
                return False, {}, f"ffprobe failed: {error_msg}"

            # 解析 JSON 输出
            media_info = json.loads(result.stdout)

            # 提取关键信息
            parsed_info = self._parse_media_info(media_info)

            # 记录获取到的时长
            duration = parsed_info.get("duration", 0.0)
            if duration > 0:
                logger.info(f"ffprobe got duration: {duration}s for {source_url[:80]}...")
            else:
                logger.warning(f"ffprobe got duration=0 for {source_url[:80]}...")

            return True, parsed_info, None

        except subprocess.TimeoutExpired:
            logger.error(f"ffprobe timeout after {timeout}s for {source_url[:80]}...")
            return False, {}, f"ffprobe timeout ({timeout}s)"
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse ffprobe output: {e}, stdout: {result.stdout[:200] if 'result' in locals() else 'N/A'}")
            return False, {}, f"Failed to parse ffprobe output: {e}"
        except FileNotFoundError:
            logger.error("ffprobe executable not found")
            return False, {}, "ffprobe not found"
        except Exception as e:
            logger.error(f"Error running ffprobe: {e}")
            return False, {}, str(e)

    def _parse_media_info(self, raw_info: Dict[str, Any]) -> Dict[str, Any]:
        """解析 ffprobe 输出的原始信息

        Args:
            raw_info: ffprobe 原始输出

        Returns:
            解析后的媒体信息
        """
        result = {
            "duration": 0.0,
            "format": "",
            "size": 0,
            "video_codec": "",
            "video_width": 0,
            "video_height": 0,
            "video_fps": 0.0,
            "video_bitrate": 0,
            "audio_codec": "",
            "audio_channels": 0,
            "audio_bitrate": 0,
            "audio_sample_rate": 0,
            "streams": [],
        }

        # 解析格式信息
        format_info = raw_info.get("format", {})
        result["format"] = format_info.get("format_name", "")
        result["size"] = int(format_info.get("size", 0) or 0)

        # 获取时长（秒）
        duration_str = format_info.get("duration", "0")
        try:
            result["duration"] = float(duration_str)
        except (ValueError, TypeError):
            result["duration"] = 0.0

        # 解析流信息
        streams = raw_info.get("streams", [])
        for stream in streams:
            codec_type = stream.get("codec_type", "")
            result["streams"].append({
                "codec_type": codec_type,
                "codec_name": stream.get("codec_name", ""),
                "index": stream.get("index", -1),
            })

            if codec_type == "video":
                result["video_codec"] = stream.get("codec_name", "")
                result["video_width"] = int(stream.get("width", 0) or 0)
                result["video_height"] = int(stream.get("height", 0) or 0)

                # 获取帧率
                fps_str = stream.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_str.split("/")
                    result["video_fps"] = float(num) / float(den) if int(den) > 0 else 0.0
                except (ValueError, ZeroDivisionError):
                    result["video_fps"] = 0.0

                # 获取比特率
                bitrate_str = stream.get("bit_rate", "0")
                try:
                    result["video_bitrate"] = int(bitrate_str)
                except ValueError:
                    result["video_bitrate"] = 0

            elif codec_type == "audio":
                result["audio_codec"] = stream.get("codec_name", "")
                result["audio_channels"] = int(stream.get("channels", 0) or 0)

                # 获取比特率
                bitrate_str = stream.get("bit_rate", "0")
                try:
                    result["audio_bitrate"] = int(bitrate_str)
                except ValueError:
                    result["audio_bitrate"] = 0

                # 获取采样率
                sample_rate_str = stream.get("sample_rate", "0")
                try:
                    result["audio_sample_rate"] = int(sample_rate_str)
                except ValueError:
                    result["audio_sample_rate"] = 0

        return result

    def get_duration(
        self,
        source_url: str,
        headers: Optional[str] = None,
        timeout: int = 30
    ) -> Tuple[bool, float, Optional[str]]:
        """快捷获取视频时长

        Args:
            source_url: 视频源 URL
            headers: HTTP 请求头
            timeout: 超时时间（秒）

        Returns:
            (成功标志, 时长秒数, 错误信息)
        """
        success, media_info, error = self.get_media_info(source_url, headers, timeout)
        if success:
            return True, media_info.get("duration", 0.0), None
        return False, 0.0, error

    def should_transcode(
        self,
        media_info: Dict[str, Any],
        file_name: str = ""
    ) -> Tuple[bool, list]:
        """判断视频是否需要转码

        Args:
            media_info: 媒体信息
            file_name: 文件名（用于扩展名检查）

        Returns:
            (是否需要转码, 原因列表)
        """
        reasons = []

        # 检查视频编码
        video_codec = media_info.get("video_codec", "").lower()
        if not video_codec:
            reasons.append("no_video_codec")
        elif video_codec not in ("h264", "hevc", "h265"):
            reasons.append(f"unsupported_codec:{video_codec}")

        # 检查音频编码
        audio_codec = media_info.get("audio_codec", "").lower()
        if audio_codec and audio_codec not in ("aac", "mp3", "opus", "vorbis"):
            reasons.append(f"unsupported_audio_codec:{audio_codec}")

        # 检查封装格式
        format_name = media_info.get("format", "").lower()
        if format_name and "mp4" not in format_name:
            # 不是 MP4 封装，可能需要转码
            if "matroska" in format_name or "mkv" in format_name:
                reasons.append("mkv_container")
            elif "avi" in format_name:
                reasons.append("avi_container")

        # 检查文件扩展名
        if file_name:
            file_lower = file_name.lower()
            if file_lower.endswith((".avi", ".mkv", ".wmv", ".rmvb", ".flv")):
                reasons.append("legacy_container")

        should = len(reasons) > 0
        return should, reasons


def get_ffprobe_runner(ffprobe_path: str = "ffprobe") -> FFprobeRunner:
    """获取 FFprobe 运行器实例

    Args:
        ffprobe_path: ffprobe 可执行文件路径

    Returns:
        FFprobeRunner 实例
    """
    return FFprobeRunner(ffprobe_path)
