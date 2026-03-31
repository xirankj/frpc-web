import os
import shutil
from dataclasses import dataclass


DEFAULT_WEB_PORT = 8001
CONTAINER_APP_ROOT = "/app"
CONTAINER_DATA_ROOT = "/app/data"
CONTAINER_LOG_ROOT = "/var/log/frpc-web"


@dataclass(frozen=True)
class RuntimeSettings:
    """统一管理运行时端口与文件路径。"""

    base_dir: str
    data_dir: str
    frpc_work_dir: str
    frpc_binary_path: str
    frpc_config_path: str
    web_config_path: str
    logs_dir: str
    app_port: int


def resolve_path(path_value: str, base_dir: str) -> str:
    """将相对路径统一转换为绝对路径。"""
    if os.path.isabs(path_value):
        return os.path.abspath(path_value)
    return os.path.abspath(os.path.join(base_dir, path_value))


def _is_container_runtime(base_dir: str) -> bool:
    """判断当前是否运行在容器内。"""
    return os.path.exists("/.dockerenv") or os.path.abspath(base_dir).replace("\\", "/") == CONTAINER_APP_ROOT


def resolve_runtime_path(path_value: str, base_dir: str, data_dir: str | None = None, logs_dir: str | None = None) -> str:
    """兼容容器路径与本地路径，统一解析运行时目录。"""
    if not path_value:
        return resolve_path(path_value, base_dir)

    normalized_path = path_value.replace("\\", "/")
    if not _is_container_runtime(base_dir):
        if normalized_path == CONTAINER_DATA_ROOT or normalized_path.startswith(f"{CONTAINER_DATA_ROOT}/"):
            local_data_dir = os.path.abspath(data_dir or resolve_path("data", base_dir))
            suffix = normalized_path[len(CONTAINER_DATA_ROOT):].lstrip("/")
            return os.path.abspath(os.path.join(local_data_dir, suffix))

        if normalized_path == CONTAINER_LOG_ROOT or normalized_path.startswith(f"{CONTAINER_LOG_ROOT}/"):
            local_logs_dir = os.path.abspath(logs_dir or resolve_path("logs", base_dir))
            suffix = normalized_path[len(CONTAINER_LOG_ROOT):].lstrip("/")
            return os.path.abspath(os.path.join(local_logs_dir, suffix))

        if normalized_path == CONTAINER_APP_ROOT or normalized_path.startswith(f"{CONTAINER_APP_ROOT}/"):
            suffix = normalized_path[len(CONTAINER_APP_ROOT):].lstrip("/")
            return os.path.abspath(os.path.join(base_dir, suffix))

    return resolve_path(path_value, base_dir)


def _get_logs_dir(base_dir: str, data_dir: str) -> str:
    """优先使用显式日志目录，否则从日志文件路径推导。"""
    default_logs_dir = resolve_runtime_path("logs", base_dir, data_dir=data_dir)
    explicit_log_dir = os.getenv("FRPC_LOG_DIR")
    if explicit_log_dir:
        return resolve_runtime_path(explicit_log_dir, base_dir, data_dir=data_dir, logs_dir=default_logs_dir)

    log_file = os.getenv("LOG_FILE", "/var/log/frpc-web/app.log")
    resolved_log_file = resolve_runtime_path(log_file, base_dir, data_dir=data_dir, logs_dir=default_logs_dir)
    return os.path.dirname(resolved_log_file)


def _get_app_port() -> int:
    """读取端口配置，并在异常时回退到默认端口。"""
    try:
        return int(os.getenv("WEB_PORT", str(DEFAULT_WEB_PORT)))
    except ValueError:
        return DEFAULT_WEB_PORT


def load_runtime_settings(base_dir: str | None = None) -> RuntimeSettings:
    """构建统一的运行时配置。"""
    resolved_base_dir = os.path.abspath(base_dir or os.getenv("APP_BASE_DIR", os.getcwd()))
    data_dir = resolve_runtime_path(os.getenv("APP_DATA_DIR", "data"), resolved_base_dir)
    frpc_work_dir = resolve_runtime_path(os.getenv("FRPC_WORK_DIR", os.path.join(data_dir, "frpc")), resolved_base_dir, data_dir=data_dir)
    logs_dir = _get_logs_dir(resolved_base_dir, data_dir)

    return RuntimeSettings(
        base_dir=resolved_base_dir,
        data_dir=data_dir,
        frpc_work_dir=frpc_work_dir,
        frpc_binary_path=resolve_runtime_path(os.getenv("FRPC_BINARY_PATH", os.path.join(frpc_work_dir, "frpc")), resolved_base_dir, data_dir=data_dir, logs_dir=logs_dir),
        frpc_config_path=resolve_runtime_path(os.getenv("FRPC_CONFIG_PATH", os.path.join(frpc_work_dir, "frpc.json")), resolved_base_dir, data_dir=data_dir, logs_dir=logs_dir),
        web_config_path=resolve_runtime_path(os.getenv("WEB_CONFIG_PATH", os.path.join(frpc_work_dir, "config.json")), resolved_base_dir, data_dir=data_dir, logs_dir=logs_dir),
        logs_dir=logs_dir,
        app_port=_get_app_port(),
    )


def sync_legacy_runtime_files(runtime_settings: RuntimeSettings) -> list[tuple[str, str]]:
    """将旧版根目录文件同步到新的持久化目录。"""
    legacy_file_map = {
        os.path.join(runtime_settings.base_dir, "frpc"): runtime_settings.frpc_binary_path,
        os.path.join(runtime_settings.base_dir, "frpc.json"): runtime_settings.frpc_config_path,
        os.path.join(runtime_settings.base_dir, "config.json"): runtime_settings.web_config_path,
    }

    synced_files = []
    os.makedirs(runtime_settings.frpc_work_dir, exist_ok=True)

    for source_path, target_path in legacy_file_map.items():
        if os.path.exists(source_path) and not os.path.exists(target_path):
            shutil.copy2(source_path, target_path)
            synced_files.append((source_path, target_path))

    return synced_files
