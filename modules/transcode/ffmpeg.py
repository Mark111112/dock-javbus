"""
FFmpeg 进程管理模块

负责构建和执行 FFmpeg 转码命令。
"""

import os
import subprocess
import logging
from typing import List, Optional, Dict, Any

from .config import TranscodeConfig
from .task import TranscodeTask

logger = logging.getLogger(__name__)


class FFmpegRunner:
    """FFmpeg 进程管理器

    构建 FFmpeg 命令并管理转码进程。
    """

    # 旧编码容器列表，需要软解 + 硬编
    LEGACY_CONTAINERS = {"avi", "asf"}
    LEGACY_CODECS = {"mpeg4", "msmpeg4v2", "msmpeg4v3", "mpeg1video"}

    def __init__(self, config: TranscodeConfig, ffmpeg_path: str = "ffmpeg"):
        """初始化 FFmpeg 运行器

        Args:
            config: 转码配置
            ffmpeg_path: ffmpeg 可执行文件路径
        """
        self.config = config
        self.ffmpeg_path = ffmpeg_path

    def build_command(
        self,
        task: TranscodeTask,
        start_number: int = 0
    ) -> List[str]:
        """构建 FFmpeg 命令

        Args:
            task: 转码任务
            start_number: 起始切片编号（用于 seek 后的转码）

        Returns:
            FFmpeg 命令列表
        """
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", self.config.loglevel,
        ]

        # 添加 HTTP 头（用于 115 直链）
        if task.header_string:
            cmd.extend(["-headers", task.header_string])

        # 输入参数：添加 -ss 进行输入端 seek（更快）
        cmd.extend(["-ss", str(task.current_seek_time)])

        # 检测是否需要软解（旧编码格式）
        use_legacy_decode = self._should_use_legacy_decode(task)

        # 硬件加速设置：旧格式需要软解，其他情况可以尝试硬解
        # 注意：-hwaccel 是输入选项，必须放在 -i 之前
        use_hwaccel = self.config.use_hwaccel and not use_legacy_decode
        if use_hwaccel and self.config.video_encoder.startswith("h264_qsv"):
            cmd.extend(["-hwaccel", "qsv"])
            cmd.extend(["-hwaccel_output_format", "qsv"])

        # 输入文件
        cmd.extend(["-i", task.source_url])

        # 视频编码器
        video_encoder = self.config.get_effective_video_encoder(use_hwaccel)
        cmd.extend(["-c:v", video_encoder])

        # 添加视频编码参数
        cmd.extend(self._get_video_params(use_legacy_decode, use_hwaccel, task))

        # 音频编码参数
        cmd.extend(["-c:a", self.config.audio_encoder])
        cmd.extend(self._get_audio_params())

        # 硬件编码预设
        if use_hwaccel and "qsv" in video_encoder.lower():
            cmd.extend(["-preset", self.config.qsv_preset])
        elif "x264" in video_encoder.lower() or "libx264" in video_encoder.lower():
            cmd.extend(["-preset", self.config.x264_preset])

        # 输出参数
        cmd.extend([
            "-map_metadata", "-1",  # 去除全局元数据
            "-map_chapters", "-1",   # 去除章节
            "-threads", "4",
        ])

        # HLS 输出参数
        cmd.extend(self._get_hls_params(task, start_number))

        # 输出文件（FFmpeg 内部使用，客户端不读取）
        output_path = os.path.join(task.output_dir, "internal.m3u8")
        cmd.extend(["-y", output_path])

        return cmd

    def _should_use_legacy_decode(self, task: TranscodeTask) -> bool:
        """判断是否需要使用软解（旧编码格式）

        Args:
            task: 转码任务

        Returns:
            是否需要软解
        """
        media_info = task.media_info or {}

        # 检查视频编码
        video_codec = (media_info.get("video_codec", "") or "").lower()
        if video_codec in self.LEGACY_CODECS:
            return True

        # 检查封装格式
        format_name = (media_info.get("format", "") or "").lower()
        for container in self.LEGACY_CONTAINERS:
            if container in format_name:
                return True

        # 检查文件扩展名
        file_name = (task.file_name or "").lower()
        for ext in (".avi", ".asf", ".wmv"):
            if file_name.endswith(ext):
                return True

        return False

    def _get_video_params(self, use_legacy_decode: bool, use_hwaccel: bool, task: TranscodeTask) -> List[str]:
        """获取视频编码参数

        Args:
            use_legacy_decode: 是否使用软解
            use_hwaccel: 是否使用硬件加速
            task: 转码任务

        Returns:
            视频编码参数列表
        """
        params = []

        # 比特率参数
        if self.config.video_bitrate:
            params.extend(["-b:v", self.config.video_bitrate])
        if self.config.maxrate:
            params.extend(["-maxrate", self.config.maxrate])
        if self.config.bufsize:
            params.extend(["-bufsize", self.config.bufsize])

        # QSV 编码器不需要 sc_threshold 和 -pix_fmt
        is_qsv = use_hwaccel and self.config.video_encoder.startswith("h264_qsv")

        if not is_qsv:
            # 场景切换检测（x264 支持，QSV 不支持）
            params.extend(["-sc_threshold", "0"])
            # 颜色空间（软件编码需要指定，QSV 自动使用 nv12）
            params.extend(["-pix_fmt", "yuv420p"])
        else:
            # QSV: 使用 vpp_qsv 滤镜确保格式正确
            params.extend(["-vf", "vpp_qsv=format=nv12"])

        # GOP 大小（关键帧间隔）
        params.extend(["-g", str(self.config.gop_size)])
        params.extend(["-keyint_min", str(self.config.gop_size)])

        return params

    def _get_audio_params(self) -> List[str]:
        """获取音频编码参数

        Returns:
            音频编码参数列表
        """
        params = []

        if self.config.audio_bitrate:
            params.extend(["-b:a", self.config.audio_bitrate])
        if self.config.audio_channels:
            params.extend(["-ac", str(self.config.audio_channels)])
        if self.config.audio_sample_rate:
            params.extend(["-ar", str(self.config.audio_sample_rate)])

        return params

    def _get_hls_params(self, task: TranscodeTask, start_number: int) -> List[str]:
        """获取 HLS 输出参数

        Args:
            task: 转码任务
            start_number: 起始切片编号

        Returns:
            HLS 参数列表
        """
        params = []

        # 复制时间戳（避免时间戳问题）
        params.extend(["-copyts"])
        params.extend(["-avoid_negative_ts", "disabled"])

        # 多路复用队列大小
        params.extend(["-max_muxing_queue_size", "1024"])

        # 最大延迟
        params.extend(["-max_delay", "5000000"])  # 5 秒

        # HLS 格式
        params.extend(["-f", "hls"])

        # 播放列表类型：VOD
        params.extend(["-hls_playlist_type", "vod"])

        # 切片列表大小：0 表示保留所有切片
        params.extend(["-hls_list_size", "0"])

        # 切片时长
        params.extend(["-hls_time", str(self.config.segment_duration)])

        # 切片类型
        params.extend(["-hls_segment_type", "mpegts"])

        # 起始切片编号（用于 seek 后的转码）
        params.extend(["-start_number", str(start_number)])

        # 切片文件命名
        output_prefix = os.path.join(task.output_dir, "segment")
        params.extend(["-hls_segment_filename", f"{output_prefix}%d.ts"])

        return params

    def build_command_simple(
        self,
        source_url: str,
        output_dir: str,
        start_time: float,
        start_number: int,
        headers: Optional[str] = None
    ) -> List[str]:
        """构建简单的 FFmpeg 命令（不依赖 TranscodeTask）

        Args:
            source_url: 视频源 URL
            output_dir: 输出目录
            start_time: 起始时间（秒）
            start_number: 起始切片编号
            headers: HTTP 请求头

        Returns:
            FFmpeg 命令列表
        """
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel", self.config.loglevel,
        ]

        if headers:
            cmd.extend(["-headers", headers])

        cmd.extend(["-ss", str(start_time)])

        # 硬件加速：注意 -hwaccel 是输入选项，必须放在 -i 之前
        video_encoder = self.config.get_effective_video_encoder(self.config.use_hwaccel)
        if self.config.use_hwaccel and "qsv" in video_encoder.lower():
            cmd.extend(["-hwaccel", "qsv"])

        cmd.extend(["-i", source_url])

        # 视频编码器
        cmd.extend(["-c:v", video_encoder])

        if self.config.use_hwaccel and "qsv" in video_encoder.lower():
            cmd.extend(["-preset", self.config.qsv_preset])
        elif "x264" in video_encoder.lower():
            cmd.extend(["-preset", self.config.x264_preset])

        # 添加编码参数
        if self.config.video_bitrate:
            cmd.extend(["-b:v", self.config.video_bitrate])
        if self.config.maxrate:
            cmd.extend(["-maxrate", self.config.maxrate])
        if self.config.bufsize:
            cmd.extend(["-bufsize", self.config.bufsize])

        cmd.extend(["-g", str(self.config.gop_size)])
        cmd.extend(["-keyint_min", str(self.config.gop_size)])

        # QSV 编码器特殊处理
        is_qsv = self.config.use_hwaccel and "qsv" in video_encoder.lower()
        if not is_qsv:
            cmd.extend(["-sc_threshold", "0"])
            cmd.extend(["-pix_fmt", "yuv420p"])
        else:
            cmd.extend(["-vf", "vpp_qsv=format=nv12"])

        # 音频编码
        cmd.extend(["-c:a", self.config.audio_encoder])
        if self.config.audio_bitrate:
            cmd.extend(["-b:a", self.config.audio_bitrate])
        if self.config.audio_channels:
            cmd.extend(["-ac", str(self.config.audio_channels)])

        # 通用参数
        cmd.extend([
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-threads", "4",
            "-copyts",
            "-avoid_negative_ts", "disabled",
            "-max_muxing_queue_size", "1024",
            "-max_delay", "5000000",
        ])

        # HLS 输出
        output_prefix = os.path.join(output_dir, "segment")
        cmd.extend([
            "-f", "hls",
            "-hls_playlist_type", "vod",
            "-hls_list_size", "0",
            "-hls_time", str(self.config.segment_duration),
            "-hls_segment_type", "mpegts",
            "-start_number", str(start_number),
            "-hls_segment_filename", f"{output_prefix}%d.ts",
            "-y",
            os.path.join(output_dir, "internal.m3u8")
        ])

        return cmd

    def start_process(
        self,
        command: List[str],
        output_dir: str
    ) -> Optional[subprocess.Popen]:
        """启动 FFmpeg 进程

        Args:
            command: FFmpeg 命令
            output_dir: 输出目录

        Returns:
            subprocess.Popen 对象，失败返回 None
        """
        try:
            # 确保输出目录存在
            os.makedirs(output_dir, exist_ok=True)

            # 打开日志文件
            log_path = os.path.join(output_dir, "transcode.log")
            log_file = open(log_path, "w")

            # 启动进程
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE
            )

            logger.info(f"Started FFmpeg process with PID {process.pid}")
            return process

        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {e}")
            return None

    def get_command_line_string(self, command: List[str]) -> str:
        """获取命令行字符串（用于日志记录）

        Args:
            command: FFmpeg 命令列表

        Returns:
            命令行字符串
        """
        # 对 HTTP 头进行脱敏处理
        sanitized = []
        for i, arg in enumerate(command):
            if arg == "-headers" and i + 1 < len(command):
                sanitized.append(arg)
                # 脱敏 HTTP 头
                sanitized.append("<headers>")
            else:
                sanitized.append(arg)
        return " ".join(sanitized)


def get_ffmpeg_runner(config: TranscodeConfig, ffmpeg_path: str = "ffmpeg") -> FFmpegRunner:
    """获取 FFmpeg 运行器实例

    Args:
        config: 转码配置
        ffmpeg_path: ffmpeg 可执行文件路径

    Returns:
        FFmpegRunner 实例
    """
    return FFmpegRunner(config, ffmpeg_path)
