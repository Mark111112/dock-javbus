"""
115 转码服务模块

借鉴 Jellyfin 的转码架构设计，提供流畅的 HLS 转码播放体验。

核心特性：
- 服务端动态生成 m3u8 播放列表（不依赖 FFmpeg 文件）
- 使用 ffprobe 预先获取视频时长
- 3 秒切片时长，减少跳转等待时间
- 24 秒 Seek 容忍窗口，减少转码重启次数
- 模块化设计，便于维护和扩展
"""

from .config import TranscodeConfig, get_transcode_config
from .task import TranscodeTask, TaskStatus
from .playlist import PlaylistGenerator
from .ffprobe import FFprobeRunner
from .ffmpeg import FFmpegRunner
from .manager import TranscodeManager, get_transcode_manager

__all__ = [
    'TranscodeConfig',
    'get_transcode_config',
    'TranscodeTask',
    'TaskStatus',
    'PlaylistGenerator',
    'FFprobeRunner',
    'FFmpegRunner',
    'TranscodeManager',
    'get_transcode_manager',
]
