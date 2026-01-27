"""
转码任务管理器

负责转码任务的生命周期管理：
- 创建和获取转码任务
- 启动和停止转码进程
- Seek 跳转处理
- 任务清理
"""

import os
import time
import uuid
import threading
import logging
import hashlib
import fnmatch
from typing import Dict, Optional, List, Tuple, Any
from subprocess import Popen

from .config import TranscodeConfig
from .task import TranscodeTask, TaskStatus
from .playlist import PlaylistGenerator, PlaylistGeneratorFactory
from .ffprobe import FFprobeRunner
from .ffmpeg import FFmpegRunner

logger = logging.getLogger(__name__)


class TranscodeManager:
    """转码任务管理器

    管理所有转码任务的生命周期，借鉴 Jellyfin 的设计理念。
    """

    def __init__(self, config: TranscodeConfig, url_refresh_callback=None):
        """初始化转码管理器

        Args:
            config: 转码配置
            url_refresh_callback: URL 刷新回调函数，签名为 (pickcode: str) -> Tuple[str, str] | None
                                 返回 (source_url, header_string) 或 None
        """
        self.config = config
        self.tasks: Dict[str, TranscodeTask] = {}
        self.lock = threading.RLock()
        self.playlist_generator = PlaylistGeneratorFactory.create(config.segment_duration)
        self.ffprobe_runner = FFprobeRunner()
        self.ffmpeg_runner = FFmpegRunner(config)
        self.url_refresh_callback = url_refresh_callback

        # 启动清理线程
        self._cleanup_thread = None
        self._stop_cleanup = threading.Event()
        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """启动清理线程"""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._stop_cleanup.clear()
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="TranscodeCleanup"
            )
            self._cleanup_thread.start()

    def _cleanup_loop(self):
        """清理循环"""
        while not self._stop_cleanup.is_set():
            try:
                self.cleanup()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
            self._stop_cleanup.wait(self.config.cleanup_interval)

    def stop(self):
        """停止管理器"""
        self._stop_cleanup.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)

        # 停止所有活跃任务
        with self.lock:
            for task in list(self.tasks.values()):
                if task.is_active():
                    self._stop_task_process(task)

    def get_task(self, task_id: str) -> Optional[TranscodeTask]:
        """获取转码任务

        Args:
            task_id: 任务 ID

        Returns:
            TranscodeTask 对象，不存在返回 None
        """
        with self.lock:
            task = self.tasks.get(task_id)
            if task:
                task.update_access()
            return task

    def create_task(
        self,
        pickcode: str,
        file_name: str,
        source_url: str,
        header_string: str,
        start_time: float = 0.0,
        known_duration: Optional[float] = None
    ) -> Tuple[bool, str, Optional[TranscodeTask]]:
        """创建新的转码任务

        基于单一任务模式：同一视频（pickcode）始终使用相同的 task_id 和输出目录。
        这样可以复用已转码的切片。

        Args:
            pickcode: 115 文件 pickcode
            file_name: 文件名
            source_url: 视频源 URL
            header_string: HTTP 请求头
            start_time: 起始时间（秒）
            known_duration: 已知的视频时长（秒），来自 115 API 或其他来源，ffprobe 失败时使用

        Returns:
            (成功标志, 消息, TranscodeTask 对象)
        """
        # 检查并发限制
        if self._get_active_count() >= self.config.max_concurrent_tasks:
            return False, "Maximum concurrent tasks reached", None

        # 生成任务 ID（基于 pickcode，同一视频始终相同）
        task_id = self._generate_task_id(pickcode)

        # 创建输出目录（基于 pickcode，同一视频共享目录）
        output_dir = self.config.get_output_dir(pickcode)
        os.makedirs(output_dir, exist_ok=True)

        # 获取媒体信息（使用 ffprobe）
        media_info = {}
        duration = 0.0
        try:
            probe_timeout = getattr(self.config, 'probe_timeout', 30)
            success, info, error = self.ffprobe_runner.get_media_info(
                source_url,
                headers=header_string,
                timeout=probe_timeout
            )
            if success:
                media_info = info
                duration = info.get("duration", 0.0)
                logger.info(f"Got media info for {file_name}: duration={duration}s")
            else:
                logger.warning(f"Failed to get media info: {error}")
                # ffprobe 失败，尝试使用已知时长
                if known_duration and known_duration > 0:
                    duration = float(known_duration)
                    logger.info(f"Using known duration from 115 API: {duration}s")
                else:
                    logger.warning("No known duration available, will use open-ended playlist")
        except Exception as e:
            logger.error(f"Error getting media info: {e}")
            # 异常情况下，尝试使用已知时长
            if known_duration and known_duration > 0:
                duration = float(known_duration)
                logger.info(f"Using known duration from 115 API: {duration}s")

        # 创建任务对象
        task = TranscodeTask(
            task_id=task_id,
            source_url=source_url,
            file_name=file_name,
            duration=duration,
            known_duration=known_duration if known_duration and known_duration > 0 else 0.0,
            media_info=media_info,
            output_dir=output_dir,
            segment_duration=self.config.segment_duration,
            current_seek_time=start_time,
            pickcode=pickcode,
            header_string=header_string,
        )

        # 保存任务
        with self.lock:
            self.tasks[task_id] = task

        return True, "Task created", task

    def start_task(self, task: TranscodeTask) -> bool:
        """启动转码任务

        Args:
            task: 转码任务

        Returns:
            是否成功启动
        """
        if not task.is_active():
            logger.warning(f"Task {task.task_id} is not active, cannot start")
            return False

        try:
            # 构建 FFmpeg 命令
            start_number = task.get_segment_id_for_time(task.current_seek_time)
            command = self.ffmpeg_runner.build_command(task, start_number=start_number)

            logger.info(f"Starting FFmpeg for task {task.task_id}: {self.ffmpeg_runner.get_command_line_string(command)}")

            # 启动进程
            process = self.ffmpeg_runner.start_process(command, task.output_dir)
            if process is None:
                task.mark_error("Failed to start FFmpeg process")
                return False

            # 更新任务状态
            task.process = process
            task.mark_running()

            # 启动监控线程
            monitor_thread = threading.Thread(
                target=self._monitor_task,
                args=(task.task_id,),
                daemon=True,
                name=f"TranscodeMonitor-{task.task_id[:8]}"
            )
            monitor_thread.start()

            return True

        except Exception as e:
            logger.error(f"Error starting task {task.task_id}: {e}")
            task.mark_error(str(e))
            return False

    def get_or_create_task(
        self,
        pickcode: str,
        file_name: str,
        source_url: str,
        header_string: str,
        start_time: float = 0.0,
        known_duration: Optional[float] = None
    ) -> Tuple[bool, str, Optional[TranscodeTask]]:
        """获取或创建转码任务

        首先查找是否有匹配的现有任务，如果没有则创建新任务。

        Args:
            pickcode: 115 文件 pickcode
            file_name: 文件名
            source_url: 视频源 URL
            header_string: HTTP 请求头
            start_time: 起始时间（秒）
            known_duration: 已知的视频时长（秒），来自 115 API

        Returns:
            (成功标志, 消息, TranscodeTask 对象)
        """
        # 查找匹配的现有任务
        with self.lock:
            # 查找同一文件的活跃任务
            for task in self.tasks.values():
                if (task.pickcode == pickcode and
                    task.file_name == file_name and
                    task.is_active()):
                    # 检查起始时间是否合适
                    if task.can_seek_directly(start_time, self.config.seek_tolerance):
                        task.update_access()
                        return True, "Using existing task", task

        # 没有合适的现有任务，创建新任务
        success, message, task = self.create_task(
            pickcode, file_name, source_url, header_string, start_time, known_duration
        )
        if not success:
            return success, message, None

        # 启动转码
        if not self.start_task(task):
            return False, "Failed to start transcode", None

        return True, "Task created and started", task

    def seek_task(
        self,
        task_id: str,
        target_time: float
    ) -> Tuple[bool, str, Optional[TranscodeTask], Optional[float]]:
        """跳转到指定时间（单一任务模式，智能转码策略）

        智能转码策略：
        1. 在容忍窗口内的跳转不需要重启转码
        2. 如果目标切片已存在，直接播放，不重启 FFmpeg（避免重复转码）
        3. 如果目标切片不存在，找到最后存在的切片，从空缺处开始转码

        Args:
            task_id: 任务 ID（基于 pickcode，同一视频只有一个）
            target_time: 目标时间（秒）

        Returns:
            (成功标志, 消息, 任务对象, HLS流的起始时间）
        """
        task = self.get_task(task_id)
        if not task:
            return False, "Task not found", None, None

        pickcode = task.pickcode
        if not pickcode:
            return False, "No pickcode in task", None, None

        # 获取视频总时长
        duration = task.duration or 0

        # 检查目标时间是否有效
        if target_time < 0:
            target_time = 0
        if duration > 0 and target_time >= duration:
            target_time = duration - 1

        # 计算目标切片编号
        target_segment = int(target_time / self.config.segment_duration)

        # 检查是否可以直接 seek（在容忍窗口内，且 FFmpeg 正在转码该位置附近）
        if task.can_seek_directly(target_time, self.config.seek_tolerance):
            # 在容忍窗口内，客户端可以本地 seek
            # HLS流的起始时间 = 当前转码起始时间
            return True, "Seek within tolerance, no restart needed", task, task.current_seek_time

        # 检查目标切片是否已存在
        if self.segment_exists(pickcode, target_segment):
            # 目标切片已存在，可以直接播放
            # 不重启 FFmpeg，让它继续从当前位置转码（避免重复转码）
            logger.info(f"Seek to {target_time}s (segment {target_segment}) - using cached segment, FFmpeg continues")

            # 确保任务状态正确
            if not task.is_active() and task.status != TaskStatus.COMPLETED:
                # FFmpeg 没有运行但任务没完成，需要启动
                # 刷新 115 直链（如果配置了回调）
                self._refresh_task_url_if_needed(task)

                # 找到最后一个已存在的 segment，从下一个开始
                last_segment = self.find_last_existing_segment(pickcode)
                start_segment = last_segment + 1 if last_segment >= 0 else 0
                start_time = start_segment * self.config.segment_duration

                task.current_seek_time = start_time
                task.mark_starting()
                self.start_task(task)
            # 如果 FFmpeg 正在运行，什么都不做，让它继续

            # HLS流的起始时间 = 0（从头开始的完整流）
            return True, "Seek to cached segment", task, 0.0

        # 目标切片不存在，从目标位置开始转码
        start_segment = target_segment
        start_segment_time = start_segment * self.config.segment_duration

        logger.info(f"Seek to {target_time}s (segment {target_segment}), "
                   f"starting FFmpeg from target segment {start_segment} ({start_segment_time}s)")

        # 停止当前 FFmpeg 进程（如果正在运行）
        if task.is_active():
            self._stop_task_process(task, reason="seek_to_target")

        # 刷新 115 直链（如果配置了回调）
        # 115 直链有时效性，重启 FFmpeg 前需要获取新的直链
        self._refresh_task_url_if_needed(task)

        # 更新任务的 seek 时间为目标位置
        task.current_seek_time = start_segment_time
        task.mark_starting()

        # 启动 FFmpeg 从目标位置开始
        if not self.start_task(task):
            return False, "Failed to start FFmpeg", None, None

        # HLS流的起始时间 = 0（从头开始的完整流）
        return True, "FFmpeg started from target segment", task, 0.0

    def _refresh_task_url_if_needed(self, task: TranscodeTask) -> None:
        """刷新任务的 115 直链（如果配置了回调）

        Args:
            task: 转码任务
        """
        if self.url_refresh_callback and task.pickcode:
            try:
                refresh_result = self.url_refresh_callback(task.pickcode)
                if refresh_result:
                    new_source_url, new_header_string = refresh_result
                    task.source_url = new_source_url
                    task.header_string = new_header_string
                    logger.info(f"Refreshed 115 URL for {task.pickcode} before FFmpeg restart")
            except Exception as e:
                logger.warning(f"Failed to refresh URL for {task.pickcode}: {e}, using existing URL")

    def stop_task(self, task_id: str, reason: str = "manual") -> bool:
        """停止转码任务

        Args:
            task_id: 任务 ID
            reason: 停止原因

        Returns:
            是否成功停止
        """
        task = self.get_task(task_id)
        if not task:
            return False

        return self._stop_task_process(task, reason)

    def _stop_task_process(self, task: TranscodeTask, reason: str = "manual") -> bool:
        """停止任务的 FFmpeg 进程

        Args:
            task: 转码任务
            reason: 停止原因

        Returns:
            是否成功停止
        """
        if task.process:
            try:
                task.process.terminate()
                try:
                    task.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    task.process.kill()
                    task.process.wait()
                logger.info(f"Stopped FFmpeg process for task {task.task_id} ({reason})")
            except Exception as e:
                logger.error(f"Error stopping FFmpeg process: {e}")

        task.mark_stopped(reason)
        return True

    def delete_task(self, task_id: str, remove_files: bool = True) -> bool:
        """删除转码任务

        Args:
            task_id: 任务 ID
            remove_files: 是否删除文件

        Returns:
            是否成功删除
        """
        task = self.get_task(task_id)
        if not task:
            return False

        # 停止任务
        if task.is_active():
            self._stop_task_process(task, "delete")

        # 删除文件
        if remove_files:
            self._remove_task_files(task)

        # 从任务列表中移除
        with self.lock:
            self.tasks.pop(task_id, None)

        return True

    def _remove_task_files(self, task: TranscodeTask):
        """删除任务相关文件

        Args:
            task: 转码任务
        """
        try:
            output_dir = task.output_dir
            if os.path.exists(output_dir):
                # 删除输出目录下的所有文件
                for filename in os.listdir(output_dir):
                    file_path = os.path.join(output_dir, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                    except Exception as e:
                        logger.warning(f"Failed to delete file {file_path}: {e}")
                # 删除目录
                try:
                    os.rmdir(output_dir)
                except:
                    pass
        except Exception as e:
            logger.error(f"Error removing task files: {e}")

    def cleanup(self) -> int:
        """清理过期和空闲任务

        Returns:
            清理的任务数量
        """
        cleaned = 0
        now = time.time()

        with self.lock:
            tasks_to_remove = []

            for task_id, task in self.tasks.items():
                # 检查超时的活跃任务
                if task.is_active():
                    if task.is_timeout(self.config.task_timeout):
                        logger.info(f"Task {task_id} timed out, stopping")
                        self._stop_task_process(task, "timeout")
                        tasks_to_remove.append(task_id)

                # 检查空闲的已完成任务
                elif task.is_idle(self.config.task_timeout):
                    logger.info(f"Task {task_id} is idle, removing")
                    tasks_to_remove.append(task_id)

            # 移除任务
            for task_id in tasks_to_remove:
                task = self.tasks.get(task_id)
                if task:
                    self._remove_task_files(task)
                self.tasks.pop(task_id, None)
                cleaned += 1

        return cleaned

    def get_playlist(self, task_id: str) -> Tuple[bool, str, Optional[str], Optional[float]]:
        """获取 m3u8 播放列表

        关键设计：
        1. 生成从 segment 0 开始的完整 m3u8，让用户看到完整进度条
        2. 添加 #EXT-X-START 标签指向当前转码位置，引导播放器从可播放位置开始

        Args:
            task_id: 任务 ID

        Returns:
            (成功标志, 播放列表内容, 错误信息, 视频时长)
        """
        task = self.get_task(task_id)
        if not task:
            return False, "", "Task not found"

        try:
            # 获取视频时长：优先使用 ffprobe 探测的，其次使用 115 API 提供的备用时长
            duration = task.duration

            # 如果 ffprobe 失败但 115 API 提供了时长，使用备用时长
            if duration <= 0 and task.known_duration > 0:
                duration = task.known_duration
                logger.info(f"Using known_duration from 115 API for playlist: {duration}s")

            # 如果时长仍然未知，尝试从已有的切片文件推断（最后的备用方案）
            if duration <= 0 and task.pickcode:
                last_segment = self.find_last_existing_segment(task.pickcode, max_segment=10000)
                if last_segment >= 0:
                    # 使用切片数量估算时长（加一些余量）
                    duration = (last_segment + 1) * self.config.segment_duration * 1.1
                    logger.info(f"Estimated duration {duration:.1f}s from {last_segment + 1} segments for {task.pickcode}")

            # 找到第一个已存在的切片（作为推荐起始位置）
            first_available_segment = 0
            start_time_offset = 0.0

            if task.pickcode and task.current_seek_time > 0:
                # FFmpeg 从中间开始转码，找到第一个已存在的切片
                first_available_segment = int(task.current_seek_time // self.config.segment_duration)

                # 检查是否真的存在，如果不存在尝试找到第一个存在的
                if not self.segment_exists(task.pickcode, first_available_segment):
                    # 二分查找第一个存在的切片
                    low, high = 0, first_available_segment
                    first_available_segment = 0
                    while low <= high:
                        mid = (low + high) // 2
                        if self.segment_exists(task.pickcode, mid):
                            first_available_segment = mid
                            low = mid + 1
                        else:
                            high = mid - 1

                start_time_offset = first_available_segment * self.config.segment_duration

                logger.info(f"Playlist start offset: segment {first_available_segment} ({start_time_offset:.1f}s)")

            # 动态生成完整的 m3u8
            # 使用绝对路径（从域名根开始），确保 ExoPlayer 正确解析
            # 注意：切片 URL 使用 task_id（不是 pickcode）
            playlist = self.playlist_generator.generate_vod_playlist(
                task_id=task_id,
                duration=duration,
                start_time=start_time_offset,
                start_segment=0,
                segment_url_template=f"/api/cloud115/transcode/segment/{task_id}/{{index}}"
            )
            return True, playlist, None, duration

        except Exception as e:
            logger.error(f"Error generating playlist: {e}")
            return False, "", str(e), None

    def get_segment_path(self, pickcode: str, segment_id: int) -> Optional[str]:
        """获取切片文件路径

        Args:
            pickcode: 115 文件 pickcode
            segment_id: 切片 ID

        Returns:
            切片文件路径，不存在返回 None
        """
        segment_path = self.config.get_segment_path(pickcode, segment_id)
        if os.path.exists(segment_path):
            return segment_path
        return None

    def segment_exists(self, pickcode: str, segment_id: int) -> bool:
        """检查切片是否存在

        Args:
            pickcode: 115 文件 pickcode
            segment_id: 切片 ID

        Returns:
            切片是否存在
        """
        segment_path = self.config.get_segment_path(pickcode, segment_id)
        return os.path.exists(segment_path) and os.path.getsize(segment_path) > 0

    def find_last_existing_segment(self, pickcode: str, max_segment: int = 10000) -> int:
        """查找最后一个已存在的切片

        Args:
            pickcode: 115 文件 pickcode
            max_segment: 最大切片编号（搜索上限）

        Returns:
            最后一个存在的切片 ID，不存在返回 -1
        """
        output_dir = self.config.get_output_dir(pickcode)
        if not os.path.exists(output_dir):
            return -1

        # 二分查找最后一个存在的切片
        left, right = 0, max_segment
        last_existing = -1

        while left <= right:
            mid = (left + right) // 2
            if self.segment_exists(pickcode, mid):
                last_existing = mid
                left = mid + 1
            else:
                right = mid - 1

        return last_existing

    def ensure_transcoding_for_segment(
        self,
        task: TranscodeTask,
        segment_id: int
    ) -> bool:
        """确保 FFmpeg 正在转码指定的 segment

        当请求的 segment 不存在时，检查 FFmpeg 是否能生成它。
        如果不能（FFmpeg 没运行或正在转码其他位置），重启 FFmpeg。

        注意：如果请求的切片比当前转码位置早很多（超过阈值），不重启 FFmpeg，
        返回 False 让调用者知道这个切片不会被转码。

        Args:
            task: 转码任务
            segment_id: 需要的 segment ID

        Returns:
            是否成功启动/确认转码
        """
        pickcode = task.pickcode
        if not pickcode:
            return False

        # 如果 segment 已存在，不需要转码
        if self.segment_exists(pickcode, segment_id):
            return True

        # 检查 FFmpeg 是否正在运行且能生成这个 segment
        if task.is_active():
            # FFmpeg 正在运行，检查它是否会生成这个 segment
            current_segment = task.get_segment_id_for_time(task.current_seek_time)

            # 如果 FFmpeg 正在转码这个 segment 之前的内容，它最终会到达
            if current_segment <= segment_id:
                # FFmpeg 会转码到这个 segment，不需要重启
                logger.debug(f"FFmpeg running from segment {current_segment}, will reach {segment_id}")
                return True

            # FFmpeg 正在转码更后面的 segment
            # 检查差距是否太大（超过 10 个切片 = 30 秒）
            gap_threshold = 10
            if segment_id < current_segment - gap_threshold:
                # 差距太大，不重启 FFmpeg（避免打断当前转码）
                logger.warning(f"Segment {segment_id} is far behind FFmpeg position {current_segment}, skipping")
                return False

            logger.info(f"FFmpeg at segment {current_segment}, need segment {segment_id}, restarting")

        # FFmpeg 没运行或需要重启
        segment_time = segment_id * self.config.segment_duration

        # 停止当前 FFmpeg（如果运行）
        if task.is_active():
            self._stop_task_process(task, reason="segment_gap")

        # 刷新 115 直链（如果配置了回调）
        # 115 直链有时效性，重启 FFmpeg 前需要获取新的直链
        self._refresh_task_url_if_needed(task)

        # 从目标 segment 开始转码
        task.current_seek_time = segment_time
        task.mark_starting()

        if not self.start_task(task):
            logger.error(f"Failed to start FFmpeg for segment {segment_id}")
            return False

        logger.info(f"Started FFmpeg from segment {segment_id} ({segment_time}s) to fill gap")
        return True

    def wait_for_segment(
        self,
        pickcode: str,
        segment_id: int,
        task: TranscodeTask,
        timeout: float = 120.0
    ) -> Tuple[bool, Optional[str]]:
        """等待切片生成

        Args:
            pickcode: 115 文件 pickcode
            segment_id: 切片 ID
            task: 转码任务（用于检查状态）
            timeout: 超时时间（秒）

        Returns:
            (成功标志, 切片文件路径)
        """
        start_time = time.time()
        check_interval = 0.1  # 100ms

        while time.time() - start_time < timeout:
            # 检查任务状态
            if task.is_finished() and task.status != TaskStatus.COMPLETED:
                return False, None

            # 检查切片是否存在
            segment_path = self.config.get_segment_path(pickcode, segment_id)
            if os.path.exists(segment_path):
                # 检查文件大小（确保不是空文件）
                if os.path.getsize(segment_path) > 0:
                    # 如果任务还在运行，检查下一个切片是否开始生成
                    # 这确保当前切片是完整的
                    if task.is_active():
                        next_segment_path = self.config.get_segment_path(pickcode, segment_id + 1)
                        if os.path.exists(next_segment_path):
                            return True, segment_path
                    else:
                        return True, segment_path

            time.sleep(check_interval)

        return False, None

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """获取所有任务信息

        Returns:
            任务信息列表
        """
        with self.lock:
            return [task.to_dict() for task in self.tasks.values()]

    def _get_active_count(self) -> int:
        """获取活跃任务数量

        Returns:
            活跃任务数量
        """
        count = 0
        for task in self.tasks.values():
            if task.is_active():
                count += 1
        return count

    def _monitor_task(self, task_id: str):
        """监控转码任务

        定期检查进程状态和切片生成情况。

        Args:
            task_id: 任务 ID
        """
        try:
            while True:
                task = self.get_task(task_id)
                if not task:
                    break

                if not task.is_active():
                    break

                # 检查进程状态
                if task.process:
                    return_code = task.process.poll()
                    if return_code is not None:
                        # 进程已结束
                        if return_code == 0:
                            task.mark_completed()
                            logger.info(f"Task {task_id} completed successfully")
                        else:
                            task.mark_error(f"FFmpeg exited with code {return_code}")
                            logger.error(f"Task {task_id} failed with code {return_code}")
                        break

                # 检查是否有切片生成
                if task.status == TaskStatus.RUNNING:
                    segment_0_path = self.config.get_segment_path(task_id, 0)
                    if os.path.exists(segment_0_path):
                        task.mark_ready()

                time.sleep(1)

        except Exception as e:
            logger.error(f"Error monitoring task {task_id}: {e}")

    def _generate_task_id(self, pickcode: str) -> str:
        """生成任务 ID（基于 pickcode，同一视频始终相同）

        Args:
            pickcode: 115 pickcode

        Returns:
            任务 ID
        """
        # 使用 pickcode 的 MD5 哈希生成唯一 ID
        hash_str = hashlib.md5(pickcode.encode()).hexdigest()[:16]
        return f"task_{hash_str}"

    def get_status_summary(self) -> Dict[str, Any]:
        """获取状态摘要

        Returns:
            状态摘要字典
        """
        with self.lock:
            active_count = 0
            total_count = len(self.tasks)

            for task in self.tasks.values():
                if task.is_active():
                    active_count += 1

            return {
                "total_tasks": total_count,
                "active_tasks": active_count,
                "max_concurrent": self.config.max_concurrent_tasks,
            }


def get_transcode_manager(config: TranscodeConfig, url_refresh_callback=None) -> TranscodeManager:
    """获取转码管理器实例

    Args:
        config: 转码配置
        url_refresh_callback: URL 刷新回调函数

    Returns:
        TranscodeManager 实例
    """
    return TranscodeManager(config, url_refresh_callback=url_refresh_callback)
