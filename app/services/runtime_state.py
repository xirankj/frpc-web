import json
import logging
import queue
import threading
import time
from dataclasses import asdict, dataclass


logger = logging.getLogger(__name__)


@dataclass
class DownloadState:
    """下载任务的统一状态。"""

    is_downloading: bool = False
    progress: str = ""
    completed: bool = False
    error: bool = False
    cancelled: bool = False
    error_message: str = ""
    archive_path: str = ""

    def snapshot(self) -> dict:
        """返回可序列化的状态快照。"""
        return asdict(self)


@dataclass
class RestartState:
    """重启任务的统一状态。"""

    is_restarting: bool = False
    completed: bool = False
    success: bool | None = None
    stage: str = "idle"
    progress: int = 0
    message: str = ""
    error_message: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0
    attempt_id: int = 0

    def snapshot(self) -> dict:
        """返回可序列化的状态快照。"""
        return asdict(self)


class DownloadManager:
    """统一管理下载状态、线程与取消信号。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread = None
        self._state = DownloadState()

    def snapshot(self) -> dict:
        """获取当前下载状态快照。"""
        with self._lock:
            return self._state.snapshot()

    def is_running(self) -> bool:
        """判断当前是否有下载任务运行中。"""
        with self._lock:
            return bool(self._thread and self._thread.is_alive() and self._state.is_downloading)

    def can_start(self) -> bool:
        """检查是否允许开始新下载任务。"""
        with self._lock:
            return not (self._thread and self._thread.is_alive())

    def start(self, target):
        """登记并启动下载线程。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("已有下载任务正在进行")
            self._cancel_event.clear()
            self._state = DownloadState(
                is_downloading=True,
                progress="开始下载任务..."
            )
            self._thread = threading.Thread(target=target, daemon=True)
            self._thread.start()
            return self._state.snapshot()

    def finish_thread(self):
        """下载线程结束后的统一收尾。"""
        with self._lock:
            self._thread = None
            self._cancel_event.clear()

    def request_cancel(self) -> tuple[bool, str]:
        """向当前下载任务发送取消信号。"""
        with self._lock:
            if not (self._thread and self._thread.is_alive() and self._state.is_downloading):
                return False, "没有正在进行的下载任务"
            self._cancel_event.set()
            self._state.progress = "正在取消下载任务..."
            self._state.completed = False
            self._state.error = False
            self._state.cancelled = False
            self._state.error_message = ""
            return True, "已收到取消请求，正在停止下载任务"

    def ensure_not_cancelled(self):
        """在下载关键步骤检查取消信号。"""
        if self._cancel_event.is_set():
            raise DownloadCancelledError("下载已取消")

    def update_progress(self, message: str, completed: bool = False, error: bool = False, cancelled: bool = False):
        """更新下载状态。"""
        with self._lock:
            self._state.is_downloading = not completed and not error and not cancelled
            self._state.progress = message
            self._state.completed = completed
            self._state.error = error
            self._state.cancelled = cancelled
            self._state.error_message = message if error else ""

    def set_archive_path(self, archive_path: str):
        """记录当前下载中的压缩包路径。"""
        with self._lock:
            self._state.archive_path = archive_path

    def get_archive_path(self) -> str:
        """读取当前压缩包路径。"""
        with self._lock:
            return self._state.archive_path

    def clear_archive_path(self):
        """清空当前压缩包路径。"""
        with self._lock:
            self._state.archive_path = ""


class RestartManager:
    """统一管理 frpc 重启任务状态。"""

    COMPLETED_STATE_TTL = 15

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._attempt_id = 0
        self._state = RestartState()

    def snapshot(self) -> dict:
        """获取当前重启状态快照。"""
        with self._lock:
            if (
                self._state.completed
                and self._state.completed_at
                and (time.time() - self._state.completed_at) > self.COMPLETED_STATE_TTL
            ):
                self._state = RestartState()
            return self._state.snapshot()

    def can_start(self) -> bool:
        """检查是否允许开始新的重启任务。"""
        with self._lock:
            return not (self._thread and self._thread.is_alive())

    def start(self, target, initial_delay: float = 0.8) -> dict:
        """登记并启动后台重启线程。"""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("已有重启任务正在进行")

            self._attempt_id += 1
            now = time.time()
            self._state = RestartState(
                is_restarting=True,
                completed=False,
                success=None,
                stage="pending",
                progress=8,
                message="已收到重启请求，准备开始执行",
                error_message="",
                started_at=now,
                updated_at=now,
                completed_at=0.0,
                attempt_id=self._attempt_id,
            )

            def runner():
                try:
                    if initial_delay > 0:
                        time.sleep(initial_delay)
                    target()
                finally:
                    with self._lock:
                        self._thread = None

            self._thread = threading.Thread(target=runner, daemon=True)
            self._thread.start()
            return self._state.snapshot()

    def update(
        self,
        stage: str,
        message: str,
        progress: int | None = None,
        *,
        success: bool | None = None,
        error_message: str = "",
    ) -> dict:
        """更新重启任务状态。"""
        with self._lock:
            now = time.time()
            self._state.stage = stage
            self._state.message = message
            self._state.updated_at = now
            if progress is not None:
                self._state.progress = max(0, min(100, int(progress)))

            if success is None:
                self._state.is_restarting = True
                self._state.completed = False
                self._state.success = None
                self._state.error_message = ""
                self._state.completed_at = 0.0
            else:
                self._state.is_restarting = False
                self._state.completed = True
                self._state.success = success
                self._state.error_message = error_message if not success else ""
                self._state.completed_at = now
                if progress is None:
                    self._state.progress = 100

            return self._state.snapshot()

    def complete_success(self, message: str) -> dict:
        """将重启任务标记为成功完成。"""
        return self.update("completed", message, 100, success=True)

    def complete_error(self, message: str) -> dict:
        """将重启任务标记为失败完成。"""
        return self.update("failed", message, 100, success=False, error_message=message)


class DownloadCancelledError(Exception):
    """用于中断下载线程的取消异常。"""


class WebSocketHub:
    """统一管理 WebSocket 客户端集合与广播。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients = set()

    def add(self, client):
        """添加客户端连接。"""
        with self._lock:
            self._clients.add(client)

    def discard(self, client):
        """安全移除客户端连接。"""
        with self._lock:
            self._clients.discard(client)

    def snapshot(self):
        """获取当前客户端快照，避免广播时长时间持锁。"""
        with self._lock:
            return list(self._clients)

    def broadcast(self, payload: dict):
        """向所有客户端广播 JSON 消息。"""
        message = json.dumps(payload)
        for client in self.snapshot():
            try:
                client.send(message)
            except Exception as e:
                logger.error(f"发送 WebSocket 消息失败: {str(e)}")
                self.discard(client)


class RuntimeStateService:
    """聚合下载管理与 WebSocket 广播状态。"""

    def __init__(self):
        self.download_manager = DownloadManager()
        self.restart_manager = RestartManager()
        self.websocket_hub = WebSocketHub()
        self.log_queue = queue.Queue()
        self._broadcast_thread = None
        self._broadcast_lock = threading.Lock()

    def ensure_log_broadcaster_started(self):
        """确保日志广播线程只启动一次。"""
        with self._broadcast_lock:
            if self._broadcast_thread and self._broadcast_thread.is_alive():
                return
            self._broadcast_thread = threading.Thread(target=self._broadcast_logs_loop, daemon=True)
            self._broadcast_thread.start()

    def _broadcast_logs_loop(self):
        """后台循环广播日志消息。"""
        while True:
            try:
                log_message = self.log_queue.get()
                self.websocket_hub.broadcast({
                    "type": "log",
                    "content": log_message
                })
            except Exception as e:
                logger.error(f"广播日志时出错: {str(e)}")

    def enqueue_logs(self, logs):
        """将日志送入广播队列。"""
        for log in logs:
            self.log_queue.put(log)


runtime_state = RuntimeStateService()
