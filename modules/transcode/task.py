"""
转码任务数据模型

定义转码任务的数据结构和状态管理。
"""

import time
import math
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from subprocess import Popen


class TaskStatus(Enum):
    """任务状态枚举"""
    STARTING = "starting"  # 启动中
    RUNNING = "running"    # 运行中
    READY = "ready"        # 已就绪（首个切片已生成）
    COMPLETED = "completed"  # 已完成
    ERROR = "error"        # 错误
    STOPPED = "stopped"    # 已停止


@dataclass
class TranscodeTask:
    """转码任务数据模型

    包含转码任务的所有状态信息。
    """

    # 基本信息
    task_id: str
    source_url: str
    file_name: str

    # 媒体信息
    duration: float = 0.0  # 视频总时长（秒），优先使用 ffprobe，失败时使用 known_duration
    known_duration: float = 0.0  # 来自 115 API 的备用时长（秒）
    media_info: Dict[str, Any] = field(default_factory=dict)

    # 输出信息
    output_dir: str = ""
    segment_duration: int = 3

    # 转码位置
    current_seek_time: float = 0.0  # 当前转码起始时间（秒）

    # 状态信息
    status: TaskStatus = TaskStatus.STARTING
    error: Optional[str] = None

    # 进程信息
    process: Optional[Popen] = None

    # 时间戳
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # 访问追踪
    last_access_at: float = field(default_factory=time.time)
    access_count: int = 0

    # 115 相关
    pickcode: Optional[str] = None
    header_string: Optional[str] = None

    def __post_init__(self):
        """初始化后处理"""
        if isinstance(self.status, str):
            self.status = TaskStatus(self.status)

    def update_access(self):
        """更新访问时间"""
        self.last_access_at = time.time()
        self.access_count += 1
        self.updated_at = time.time()

    def mark_starting(self):
        """标记为启动中"""
        self.status = TaskStatus.STARTING
        self.updated_at = time.time()

    def mark_running(self):
        """标记为运行中"""
        self.status = TaskStatus.RUNNING
        self.updated_at = time.time()
        if self.started_at is None:
            self.started_at = time.time()

    def mark_ready(self):
        """标记为已就绪（首个切片已生成）"""
        if self.status == TaskStatus.STARTING or self.status == TaskStatus.RUNNING:
            self.status = TaskStatus.READY
        self.updated_at = time.time()

    def mark_completed(self):
        """标记为已完成"""
        self.status = TaskStatus.COMPLETED
        self.updated_at = time.time()
        self.completed_at = time.time()

    def mark_error(self, error: str):
        """标记为错误

        Args:
            error: 错误信息
        """
        self.status = TaskStatus.ERROR
        self.error = error
        self.updated_at = time.time()
        self.completed_at = time.time()

    def mark_stopped(self, reason: str = "manual"):
        """标记为已停止

        Args:
            reason: 停止原因
        """
        self.status = TaskStatus.STOPPED
        self.error = reason
        self.updated_at = time.time()
        self.completed_at = time.time()

    def is_active(self) -> bool:
        """判断任务是否活跃（正在运行或准备中）

        Returns:
            是否活跃
        """
        return self.status in (TaskStatus.STARTING, TaskStatus.RUNNING, TaskStatus.READY)

    def is_finished(self) -> bool:
        """判断任务是否已结束（完成、错误或停止）

        Returns:
            是否已结束
        """
        return self.status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.STOPPED)

    def can_seek_directly(self, target_time: float, seek_tolerance: int = 24) -> bool:
        """判断是否可以直接 seek（无需重启转码）

        参考 Jellyfin 的设计：在容忍窗口内的跳转不需要重启转码。

        Args:
            target_time: 目标时间（秒）
            seek_tolerance: 容忍窗口大小（秒），默认 24 秒

        Returns:
            是否可以直接 seek
        """
        if self.is_finished():
            return False

        # 向后跳转：需要重启
        if target_time < self.current_seek_time:
            return False

        # 向前跳太远：需要重启
        if target_time - self.current_seek_time > seek_tolerance:
            return False

        # 在容忍窗口内：可以直接 seek
        return True

    def get_estimated_segment_count(self) -> int:
        """估算切片总数

        Returns:
            切片总数
        """
        if self.duration <= 0:
            return 0
        return math.ceil(self.duration / self.segment_duration)

    def get_segment_id_for_time(self, time_seconds: float) -> int:
        """获取指定时间对应的切片 ID

        Args:
            time_seconds: 时间（秒）

        Returns:
            切片 ID
        """
        if time_seconds < 0:
            return 0
        # 如果时长未知，默认从 0 开始
        if self.duration <= 0:
            return 0
        if time_seconds >= self.duration:
            return max(0, self.get_estimated_segment_count() - 1)
        return int(time_seconds // self.segment_duration)

    def get_time_for_segment(self, segment_id: int) -> float:
        """获取切片对应的起始时间

        Args:
            segment_id: 切片 ID

        Returns:
            起始时间（秒）
        """
        return segment_id * self.segment_duration

    def get_elapsed_time(self) -> float:
        """获取任务已运行时间

        Returns:
            已运行时间（秒）
        """
        if self.started_at is None:
            return 0
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    def is_timeout(self, timeout_seconds: int = 3600) -> bool:
        """判断任务是否超时

        Args:
            timeout_seconds: 超时时间（秒）

        Returns:
            是否超时
        """
        if not self.is_active():
            return False
        if self.started_at is None:
            # 启动超过 5 分钟视为超时
            return (time.time() - self.created_at) > 300
        return (time.time() - self.last_access_at) > timeout_seconds

    def is_idle(self, idle_seconds: int = 600) -> bool:
        """判断任务是否空闲（长时间未访问）

        Args:
            idle_seconds: 空闲时间阈值（秒）

        Returns:
            是否空闲
        """
        if self.is_active():
            return False
        return (time.time() - self.last_access_at) > idle_seconds

    def to_dict(self, include_internal: bool = False) -> Dict[str, Any]:
        """转换为字典（用于 API 响应）

        Args:
            include_internal: 是否包含内部信息（如 header_string）

        Returns:
            字典表示
        """
        result = {
            "id": self.task_id,
            "file_name": self.file_name,
            "pickcode": self.pickcode,
            "status": self.status.value,
            "duration": self.duration,
            "known_duration": self.known_duration,
            "current_seek_time": self.current_seek_time,
            "segment_duration": self.segment_duration,
            "estimated_segment_count": self.get_estimated_segment_count(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_access_at": self.last_access_at,
            "access_count": self.access_count,
        }

        if self.started_at:
            result["started_at"] = self.started_at
        if self.completed_at:
            result["completed_at"] = self.completed_at
        if self.error:
            result["error"] = self.error

        if include_internal:
            result["source_url"] = self.source_url
            result["output_dir"] = self.output_dir
            result["header_string"] = self.header_string
            result["media_info"] = self.media_info

        return result
