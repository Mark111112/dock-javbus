"""
115 转码 V2 API 端点

借鉴 Jellyfin 的设计，提供流畅的 HLS 转码播放体验。
"""

import os
import time
from flask import jsonify, request, send_file, Response
import logging

logger = logging.getLogger(__name__)

# 全局转码管理器实例（在 webserver.py 中初始化）
TRANSCODE_MANAGER = None


def init_transcode_manager(manager):
    """初始化转码管理器

    Args:
        manager: TranscodeManager 实例
    """
    global TRANSCODE_MANAGER
    TRANSCODE_MANAGER = manager
    logger.info("Transcode manager initialized")


def register_routes(app):
    """注册转码 API 路由

    Args:
        app: Flask 应用实例
    """

    @app.route('/api/cloud115/transcode/playlist/<task_id>.m3u8', methods=['GET'])
    @app.route('/api/cloud115/transcode/playlist/<task_id>', methods=['GET'])
    def cloud115_transcode_playlist(task_id):
        """获取 m3u8 播放列表（V2）

        关键设计：服务端动态生成完整的 m3u8，不依赖 FFmpeg 文件。
        客户端从第一刻就能看到完整的进度条。

        Args:
            task_id: 转码任务 ID

        Returns:
            m3u8 播放列表内容，带 X-Video-Duration 响应头
        """
        if TRANSCODE_MANAGER is None:
            return "Transcode manager not initialized", 500

        success, playlist, error, duration = TRANSCODE_MANAGER.get_playlist(task_id)

        if not success:
            return error or "Playlist not found", 404

        response = Response(playlist, mimetype='application/vnd.apple.mpegurl')
        # 将时长添加到响应头，供前端使用
        if duration and duration > 0:
            response.headers['X-Video-Duration'] = str(int(duration))
        return response

    @app.route('/api/cloud115/transcode/segment/<task_id>/<int:segment_id>', methods=['GET'])
    def cloud115_transcode_segment(task_id, segment_id):
        """获取切片文件

        如果切片不存在，启动 FFmpeg（如果需要）并等待切片生成。

        Args:
            task_id: 转码任务 ID（基于 pickcode）
            segment_id: 切片 ID

        Returns:
            切片文件内容
        """
        if TRANSCODE_MANAGER is None:
            return "Transcode manager not initialized", 500

        # 获取任务以获取 pickcode
        task = TRANSCODE_MANAGER.get_task(task_id)
        if not task:
            return "Task not found", 404

        pickcode = task.pickcode
        if not pickcode:
            return "No pickcode in task", 500

        # 检查切片是否已存在
        segment_path = TRANSCODE_MANAGER.get_segment_path(pickcode, segment_id)

        if segment_path and os.path.exists(segment_path):
            # 切片已存在，直接返回
            return send_file(segment_path, mimetype='video/mp2t')

        # 切片不存在，确保 FFmpeg 正在转码这个位置
        # 这处理播放遇到未转码 segment 的情况
        if not TRANSCODE_MANAGER.ensure_transcoding_for_segment(task, segment_id):
            # 切片不会被转码（比如比当前转码位置早很多），返回 404 让客户端跳过
            return "Segment skipped", 404

        # 等待切片生成
        success, result_path = TRANSCODE_MANAGER.wait_for_segment(
            pickcode, segment_id, task=task, timeout=120.0
        )

        if success and result_path:
            return send_file(result_path, mimetype='video/mp2t')

        return "Segment not available", 404

    @app.route('/api/cloud115/transcode/status/<task_id>', methods=['GET'])
    def cloud115_transcode_status_v2(task_id):
        """获取转码任务状态（V2）

        兼容前端期望的响应格式。

        Args:
            task_id: 转码任务 ID

        Returns:
            任务状态 JSON
        """
        if TRANSCODE_MANAGER is None:
            return jsonify({"error": "Transcode manager not initialized"}), 500

        task = TRANSCODE_MANAGER.get_task(task_id)

        if not task:
            return jsonify({"error": "Task not found"}), 404

        task_dict = task.to_dict()

        # 兼容前端期望的响应格式
        response = {
            "success": True,
            "task": task_dict,
            "task_id": task_id,
            "status": task.status.value,
            "ready": task.status.value in ("ready", "running", "completed"),
            "stream_url": f"/api/cloud115/transcode/playlist/{task_id}",
            "playlist_url": f"/api/cloud115/transcode/playlist/{task_id}",
        }

        # 添加摘要信息
        response["status_summary"] = TRANSCODE_MANAGER.get_status_summary()

        return jsonify(response)

    @app.route('/api/cloud115/transcode/stop/<task_id>', methods=['POST'])
    def cloud115_transcode_stop_v2(task_id):
        """停止转码任务（V2）

        Args:
            task_id: 转码任务 ID

        Returns:
            操作结果 JSON
        """
        if TRANSCODE_MANAGER is None:
            return jsonify({"error": "Transcode manager not initialized"}), 500

        success = TRANSCODE_MANAGER.stop_task(task_id, reason="manual")

        if success:
            task = TRANSCODE_MANAGER.get_task(task_id)
            response = {"success": True, "message": "Task stopped"}
            if task:
                response["task"] = task.to_dict()
            response["status_summary"] = TRANSCODE_MANAGER.get_status_summary()
            return jsonify(response)

        return jsonify({"error": "Task not found"}), 404

    @app.route('/api/cloud115/transcode/seek/<task_id>', methods=['POST'])
    def cloud115_transcode_seek_v2(task_id):
        """跳转到指定时间（V2）

        借鉴 Jellyfin 的设计：在容忍窗口内的跳转不需要重启转码。

        请求体：
        {
            "time": 120.5  // 目标时间（秒）
        }

        Args:
            task_id: 原任务 ID

        Returns:
            操作结果 JSON
        """
        if TRANSCODE_MANAGER is None:
            return jsonify({"error": "Transcode manager not initialized"}), 500

        data = request.get_json() or {}
        target_time = float(data.get('time', 0))

        success, message, task, stream_start_time = TRANSCODE_MANAGER.seek_task(task_id, target_time)

        if success:
            response = {
                "success": True,
                "message": message,
                "target_time": target_time
            }

            if task:
                new_task_id = task.task_id
                response["task_id"] = new_task_id
                # stream_start_time 是 HLS 流的起始时间，用于前端设置 startOffset
                # 如果目标切片已存在从头播放，stream_start_time = 0
                # 如果从目标位置开始转码，stream_start_time = 目标切片的起始时间
                response["start_time"] = stream_start_time if stream_start_time is not None else 0
                response["task"] = task.to_dict()

            response["status_summary"] = TRANSCODE_MANAGER.get_status_summary()

            return jsonify(response)

        return jsonify({"error": message}), 400 if task else 404

    @app.route('/api/cloud115/transcode/tasks', methods=['GET'])
    def cloud115_transcode_tasks_v2():
        """获取所有转码任务列表（V2）

        兼容前端期望的响应格式。

        Returns:
            任务列表 JSON
        """
        if TRANSCODE_MANAGER is None:
            return jsonify({"error": "Transcode manager not initialized"}), 500

        tasks = TRANSCODE_MANAGER.get_all_tasks()
        summary = TRANSCODE_MANAGER.get_status_summary()

        return jsonify({
            "success": True,
            "tasks": tasks,
            "summary": summary
        })

    @app.route('/api/cloud115/transcode/delete/<task_id>', methods=['POST'])
    def cloud115_transcode_delete_v2(task_id):
        """删除转码任务（V2）

        Args:
            task_id: 任务 ID

        Returns:
            操作结果 JSON
        """
        if TRANSCODE_MANAGER is None:
            return jsonify({"error": "Transcode manager not initialized"}), 500

        success = TRANSCODE_MANAGER.delete_task(task_id, remove_files=True)

        if success:
            summary = TRANSCODE_MANAGER.get_status_summary()
            return jsonify({
                "success": True,
                "message": "Task deleted",
                "status_summary": summary
            })

        return jsonify({"error": "Task not found"}), 404


def get_transcode_manager():
    """获取转码管理器实例

    Returns:
        TranscodeManager 实例
    """
    return TRANSCODE_MANAGER
