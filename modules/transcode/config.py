"""
转码配置模块

定义转码相关的配置参数和默认值。
"""

import os
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class TranscodeConfig:
    """转码配置

    从全局配置中读取转码相关参数，提供默认值。
    """

    # 基础配置
    enabled: bool = True
    work_dir: str = "data/transcode"
    segment_duration: int = 3  # 切片时长（秒），借鉴 Jellyfin 的 6 秒，但使用 3 秒以更快响应
    seek_tolerance: int = 24  # Seek 容忍窗口（秒），借鉴 Jellyfin 的设计

    # 编码器配置
    use_hwaccel: bool = True  # 是否使用硬件加速
    video_encoder: str = "h264_qsv"  # 硬件编码器
    video_encoder_sw: str = "libx264"  # 软件编码器（回退用）
    audio_encoder: str = "aac"
    qsv_device: str = ""  # QSV 设备

    # 视频编码参数
    video_bitrate: Optional[str] = None  # 如 "2000k"
    maxrate: Optional[str] = None  # 如 "2200k"
    bufsize: Optional[str] = None  # 如 "4400k"
    gop_size: int = 60  # GOP 大小（帧数）

    # 音频编码参数
    audio_bitrate: Optional[str] = None  # 如 "128k"
    audio_channels: Optional[int] = None  # 如 2
    audio_sample_rate: Optional[int] = None  # 如 48000

    # HLS 参数
    hls_mode: str = "vod"  # vod 或 streaming（不再使用，但保留配置兼容性）
    hls_flags: str = ""  # 自动设置，不再需要手动配置

    # FFmpeg 日志级别
    loglevel: str = "warning"

    # 并发限制
    max_concurrent_tasks: int = 2  # 最大并发转码任务数

    # 超时配置
    task_timeout: int = 3600  # 任务超时时间（秒）
    cleanup_interval: int = 300  # 清理间隔（秒）
    probe_timeout: int = 30  # ffprobe 探测超时时间（秒）

    # 硬件编码预设
    qsv_preset: str = "7"  # QSV 预设
    x264_preset: str = "medium"  # x264 预设

    # 自动启动转码
    auto_start: bool = True

    @classmethod
    def from_app_config(cls, app_config: dict) -> 'TranscodeConfig':
        """从应用配置创建 TranscodeConfig

        Args:
            app_config: 全局配置字典

        Returns:
            TranscodeConfig 实例
        """
        cloud115_config = app_config.get("cloud115", {}) or {}
        transcode_config = cloud115_config.get("transcode", {}) or {}

        # 合并默认值
        config = cls()

        # 更新基础配置
        if "enabled" in transcode_config:
            config.enabled = transcode_config["enabled"]
        if "work_dir" in transcode_config:
            config.work_dir = transcode_config["work_dir"]
        if "segment_duration" in transcode_config:
            config.segment_duration = int(transcode_config["segment_duration"] or 3)
        if "seek_tolerance_seconds" in transcode_config:
            config.seek_tolerance = int(transcode_config["seek_tolerance_seconds"] or 24)

        # 更新编码器配置
        if "use_hwaccel" in transcode_config:
            config.use_hwaccel = transcode_config["use_hwaccel"]
        if "video_encoder" in transcode_config:
            config.video_encoder = transcode_config["video_encoder"]
        if "video_encoder_sw" in transcode_config:
            config.video_encoder_sw = transcode_config["video_encoder_sw"]
        if "audio_encoder" in transcode_config:
            config.audio_encoder = transcode_config["audio_encoder"]
        if "qsv_device" in transcode_config:
            config.qsv_device = transcode_config["qsv_device"]

        # 更新视频编码参数
        if "video_bitrate" in transcode_config:
            config.video_bitrate = transcode_config["video_bitrate"]
        if "maxrate" in transcode_config:
            config.maxrate = transcode_config["maxrate"]
        if "bufsize" in transcode_config:
            config.bufsize = transcode_config["bufsize"]
        if "gop_size" in transcode_config:
            config.gop_size = int(transcode_config["gop_size"] or 60)

        # 更新音频编码参数
        if "audio_bitrate" in transcode_config:
            config.audio_bitrate = transcode_config["audio_bitrate"]
        if "audio_channels" in transcode_config:
            config.audio_channels = int(transcode_config["audio_channels"]) if transcode_config["audio_channels"] else None
        if "audio_sample_rate" in transcode_config:
            config.audio_sample_rate = int(transcode_config["audio_sample_rate"]) if transcode_config["audio_sample_rate"] else None

        # 更新 HLS 参数
        if "hls_mode" in transcode_config:
            config.hls_mode = transcode_config["hls_mode"]
        if "hls_flags" in transcode_config:
            config.hls_flags = transcode_config["hls_flags"]

        # 更新日志级别
        if "loglevel" in transcode_config:
            config.loglevel = transcode_config["loglevel"]

        # 更新并发限制
        if "max_concurrent_tasks" in transcode_config:
            config.max_concurrent_tasks = int(transcode_config["max_concurrent_tasks"] or 2)

        # 更新超时配置
        if "task_timeout" in transcode_config:
            config.task_timeout = int(transcode_config["task_timeout"] or 3600)
        if "cleanup_interval" in transcode_config:
            config.cleanup_interval = int(transcode_config["cleanup_interval"] or 300)
        if "probe_timeout" in transcode_config:
            config.probe_timeout = int(transcode_config["probe_timeout"] or 30)

        # 更新预设
        if "qsv_preset" in transcode_config:
            config.qsv_preset = str(transcode_config["qsv_preset"])
        if "x264_preset" in transcode_config:
            config.x264_preset = transcode_config["x264_preset"]

        # 更新自动启动
        if "auto_start" in transcode_config:
            config.auto_start = transcode_config["auto_start"]

        return config

    def get_effective_video_encoder(self, use_hwaccel: bool) -> str:
        """获取有效的视频编码器

        Args:
            use_hwaccel: 是否使用硬件加速

        Returns:
            编码器名称
        """
        if use_hwaccel:
            return self.video_encoder
        return self.video_encoder_sw

    def get_output_dir(self, pickcode: str) -> str:
        """获取转码输出目录（基于 pickcode，同一视频共享目录）

        Args:
            pickcode: 115 文件 pickcode

        Returns:
            输出目录路径
        """
        return os.path.join(self.work_dir, pickcode)

    def get_segment_path(self, pickcode: str, segment_id: int) -> str:
        """获取切片文件路径

        Args:
            pickcode: 115 文件 pickcode
            segment_id: 切片 ID

        Returns:
            切片文件路径
        """
        output_dir = self.get_output_dir(pickcode)
        return os.path.join(output_dir, f"segment{segment_id}.ts")

    def get_internal_playlist_path(self, pickcode: str) -> str:
        """获取 FFmpeg 内部使用的 m3u8 文件路径

        Args:
            pickcode: 115 文件 pickcode

        Returns:
            内部 m3u8 文件路径（FFmpeg 生成，服务端不使用）
        """
        output_dir = self.get_output_dir(pickcode)
        return os.path.join(output_dir, "internal.m3u8")

    def get_segment_pattern(self, pickcode: str) -> str:
        """获取切片文件名模式（用于 FFmpeg）

        Args:
            pickcode: 115 文件 pickcode

        Returns:
            切片文件名模式，如 "/path/to/transcode/pickcode/segment%d.ts"
        """
        output_dir = self.get_output_dir(pickcode)
        return os.path.join(output_dir, "segment%d.ts")


def get_transcode_config(app_config: dict) -> TranscodeConfig:
    """获取转码配置的便捷函数

    Args:
        app_config: 全局配置字典

    Returns:
        TranscodeConfig 实例
    """
    return TranscodeConfig.from_app_config(app_config)
