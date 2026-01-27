"""
HLS 播放列表生成器

借鉴 Jellyfin 的设计，服务端动态生成完整的 m3u8 播放列表，
不依赖 FFmpeg 生成的文件。
"""

import math
from typing import Optional


class PlaylistGenerator:
    """HLS 播放列表生成器

    根据视频时长预先计算完整的 m3u8 播放列表。
    这是实现流畅播放体验的关键：客户端从第一刻就能看到完整的进度条。
    """

    def __init__(self, segment_duration: int = 3):
        """初始化播放列表生成器

        Args:
            segment_duration: 切片时长（秒），默认 3 秒
        """
        self.segment_duration = segment_duration

    def generate_vod_playlist(
        self,
        task_id: str,
        duration: float,
        start_time: float = 0,
        start_segment: int = 0,
        segment_url_template: str = "/api/cloud115/transcode/segment/{task_id}/{index}",
    ) -> str:
        """生成 VOD 类型的 m3u8 播放列表

        关键设计：基于视频时长预先计算所有切片，不依赖 FFmpeg 文件。
        这使得客户端从第一刻就能看到完整的进度条和时长信息。

        Args:
            task_id: 转码任务 ID
            duration: 视频总时长（秒）
            start_time: 起始时间（秒），用于 offset 计算
            start_segment: 起始 segment 编号（与 FFmpeg 的 start_number 对应）
            segment_url_template: 切片 URL 模板

        Returns:
            m3u8 播放列表内容
        """
        # 如果时长未知，生成开放式播放列表（没有 #EXT-X-ENDLIST）
        # 这样 HLS.js 会持续加载新切片，而不是报错
        if duration <= 0:
            return self._generate_open_playlist(task_id, start_segment, segment_url_template)

        # 计算切片数量
        segment_count = math.ceil(duration / self.segment_duration)

        # 构建 m3u8 内容
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            f"#EXT-X-TARGETDURATION:{self.segment_duration}",
            f"#EXT-X-MEDIA-SEQUENCE:{start_segment}",
        ]

        # 如果有起始时间偏移，添加 START 标签
        if start_time > 0:
            # 计算在起始 segment 中的偏移时间
            offset_in_segment = start_time - (start_segment * self.segment_duration)
            lines.append(f"#EXT-X-START:TIME-OFFSET={max(0, offset_in_segment):.3f}")

        # 生成所有切片条目（使用绝对 segment 编号）
        for i in range(segment_count):
            # 计算这个切片的实际时长
            seg_duration = min(
                self.segment_duration,
                duration - i * self.segment_duration
            )
            # 使用绝对 segment 编号（与 FFmpeg 的 start_number 对应）
            absolute_index = start_segment + i
            lines.append(f"#EXTINF:{seg_duration:.6f},nodesc")
            lines.append(segment_url_template.format(
                task_id=task_id,
                index=absolute_index
            ))

        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines)

    def _generate_open_playlist(
        self,
        task_id: str,
        start_segment: int = 0,
        segment_url_template: str = "/api/cloud115/transcode/segment/{task_id}/{index}",
    ) -> str:
        """生成开放式播放列表（用于时长未知时）

        开放式播放列表不包含 #EXT-X-ENDLIST，HLS.js 会持续加载新切片。

        Args:
            task_id: 转码任务 ID
            start_segment: 起始 segment 编号
            segment_url_template: 切片 URL 模板

        Returns:
            m3u8 播放列表内容
        """
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:EVENT",  # 使用 EVENT 类型（开放式）
            f"#EXT-X-TARGETDURATION:{self.segment_duration}",
            f"#EXT-X-MEDIA-SEQUENCE:{start_segment}",
        ]

        # 生成前 100 个切片条目（HLS.js 需要一些初始切片来启动）
        # 实际上更多的切片会随着 FFmpeg 转码而变得可用
        for i in range(100):
            absolute_index = start_segment + i
            lines.append(f"#EXTINF:{self.segment_duration:.6f},nodesc")
            lines.append(segment_url_template.format(
                task_id=task_id,
                index=absolute_index
            ))

        # 注意：不包含 #EXT-X-ENDLIST，这样 HLS.js 会持续检查新切片
        return "\n".join(lines)

    def generate_seek_playlist(
        self,
        task_id: str,
        duration: float,
        seek_time: float,
        segment_url_template: str = "/api/cloud115/transcode/segment/{task_id}/{index}",
    ) -> str:
        """生成 Seek 后的 m3u8 播放列表

        与完整播放列表不同，这里的切片编号从 seek 位置开始。

        Args:
            task_id: 转码任务 ID
            duration: 视频总时长（秒）
            seek_time: 跳转时间（秒）
            segment_url_template: 切片 URL 模板

        Returns:
            m3u8 播放列表内容
        """
        if duration <= 0 or seek_time >= duration:
            return self._generate_empty_playlist()

        # 计算起始切片编号
        start_segment = int(seek_time // self.segment_duration)
        remaining_duration = duration - seek_time
        segment_count = math.ceil(remaining_duration / self.segment_duration)

        # 构建 m3u8 内容
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            f"#EXT-X-TARGETDURATION:{self.segment_duration}",
            f"#EXT-X-MEDIA-SEQUENCE:{start_segment}",
            f"#EXT-X-START:TIME-OFFSET={seek_time:.3f}",
        ]

        # 生成切片条目
        for i in range(segment_count):
            segment_index = start_segment + i
            # 计算这个切片的实际时长
            elapsed_from_seek = i * self.segment_duration
            seg_duration = min(
                self.segment_duration,
                remaining_duration - elapsed_from_seek
            )
            lines.append(f"#EXTINF:{seg_duration:.6f},nodesc")
            lines.append(segment_url_template.format(
                task_id=task_id,
                index=segment_index
            ))

        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines)

    def _generate_empty_playlist(self) -> str:
        """生成空播放列表

        Returns:
            空 m3u8 内容
        """
        return "\n".join([
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-ENDLIST"
        ])

    def get_segment_count(self, duration: float) -> int:
        """获取视频的切片总数

        Args:
            duration: 视频时长（秒）

        Returns:
            切片总数
        """
        if duration <= 0:
            return 0
        return math.ceil(duration / self.segment_duration)

    def time_to_segment(self, time_seconds: float) -> int:
        """将时间转换为切片 ID

        Args:
            time_seconds: 时间（秒）

        Returns:
            切片 ID
        """
        if time_seconds < 0:
            return 0
        return int(time_seconds // self.segment_duration)

    def segment_to_time(self, segment_id: int) -> float:
        """将切片 ID 转换为起始时间

        Args:
            segment_id: 切片 ID

        Returns:
            起始时间（秒）
        """
        return segment_id * self.segment_duration

    def get_segment_range(self, seek_time: float, duration: float) -> tuple[int, int]:
        """获取指定时间后的切片范围

        Args:
            seek_time: 跳转时间（秒）
            duration: 视频总时长（秒）

        Returns:
            (起始切片 ID, 结束切片 ID)
        """
        start_segment = self.time_to_segment(seek_time)
        end_segment = self.get_segment_count(duration) - 1
        return (start_segment, end_segment)


class PlaylistGeneratorFactory:
    """播放列表生成器工厂

    根据配置创建播放列表生成器实例。
    """

    _default_instance: Optional[PlaylistGenerator] = None

    @classmethod
    def get_default(cls, segment_duration: int = 3) -> PlaylistGenerator:
        """获取默认的播放列表生成器

        Args:
            segment_duration: 切片时长（秒）

        Returns:
            PlaylistGenerator 实例
        """
        if cls._default_instance is None or cls._default_instance.segment_duration != segment_duration:
            cls._default_instance = PlaylistGenerator(segment_duration)
        return cls._default_instance

    @classmethod
    def create(cls, segment_duration: int = 3) -> PlaylistGenerator:
        """创建新的播放列表生成器

        Args:
            segment_duration: 切片时长（秒）

        Returns:
            PlaylistGenerator 实例
        """
        return PlaylistGenerator(segment_duration)
