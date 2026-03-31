import os
import subprocess
import signal
import psutil
import logging
import requests
from pathlib import Path
import threading
import time
import queue
import re
from app.runtime_settings import load_runtime_settings, resolve_runtime_path

logger = logging.getLogger(__name__)

class FrpcManager:
    VERSION_CACHE_TTL = 60
    VERSION_PATTERN = re.compile(r'v?\d+\.\d+\.\d+(?:[-+._][0-9A-Za-z]+)*')

    def __init__(self):
        runtime_settings = load_runtime_settings()
        self.frpc_work_dir = runtime_settings.frpc_work_dir
        self.frpc_path = runtime_settings.frpc_binary_path
        self.config_path = runtime_settings.frpc_config_path
        # 将 frpc 日志输出目录与应用日志目录保持一致，并兼容容器路径映射
        configured_log_dir = os.getenv('FRPC_LOG_DIR')
        if configured_log_dir:
            self.log_dir = resolve_runtime_path(
                configured_log_dir,
                runtime_settings.base_dir,
                data_dir=runtime_settings.data_dir,
                logs_dir=runtime_settings.logs_dir
            )
        else:
            self.log_dir = runtime_settings.logs_dir
        self.log_path = os.path.join(self.log_dir, 'frpc.log')
        self.process = None
        self.attached_pid = None
        self._ensure_log_dir()
        self.log_queue = queue.Queue()
        self.log_thread = None
        self.stop_log_thread = False
        self.error_state = False
        self.error_message = ""
        self._version_cache_lock = threading.Lock()
        self._version_cache = {
            'frpc': {
                'value': '待检测',
                'hint': '等待版本检测',
                'checked_at': 0.0,
                'fingerprint': None,
            },
            'frps': {
                'value': '待检测',
                'hint': '等待版本检测',
                'checked_at': 0.0,
                'fingerprint': None,
            }
        }
        self._start_log_thread()  # 在初始化时就启动日志线程
        self._recover_process()  # 尝试恢复进程信息

    def _append_log(self, message: str):
        """安全地追加一行日志，避免错误处理再次抛出异常。"""
        try:
            with open(self.log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(message)
        except Exception as e:
            logger.warning(f"写入 frpc 日志失败: {str(e)}")

    @classmethod
    def _normalize_version(cls, value: str) -> str:
        """统一格式化版本号展示。"""
        candidate = (value or '').strip()
        if not candidate:
            return ''
        if re.fullmatch(r'\d+\.\d+\.\d+(?:[-+._][0-9A-Za-z]+)*', candidate):
            return f'v{candidate}'
        return candidate

    @classmethod
    def _extract_version_from_text(cls, text: str) -> str:
        """从命令输出或 HTTP 响应中提取版本号。"""
        if not text:
            return ''
        match = cls.VERSION_PATTERN.search(text)
        if match:
            return cls._normalize_version(match.group(0))
        return ''

    def _get_cached_version(self, key: str, fingerprint: str | None, force_refresh: bool = False):
        """读取版本缓存，避免频繁执行命令或探测远端。"""
        with self._version_cache_lock:
            cache = self._version_cache[key]
            if (
                not force_refresh
                and cache['fingerprint'] == fingerprint
                and (time.time() - cache['checked_at']) < self.VERSION_CACHE_TTL
            ):
                return {
                    'value': cache['value'],
                    'hint': cache['hint']
                }
        return None

    def _set_cached_version(self, key: str, fingerprint: str | None, value: str, hint: str):
        """更新版本缓存。"""
        with self._version_cache_lock:
            self._version_cache[key] = {
                'value': value,
                'hint': hint,
                'checked_at': time.time(),
                'fingerprint': fingerprint,
            }
        return {
            'value': value,
            'hint': hint
        }

    def get_local_version_info(self, force_refresh: bool = False):
        """获取本地 frpc 版本。"""
        if not os.path.exists(self.frpc_path):
            return self._set_cached_version(
                'frpc',
                'missing',
                '未安装',
                '未找到本地 frpc 可执行文件'
            )

        try:
            fingerprint = f"{self.frpc_path}:{os.path.getmtime(self.frpc_path)}"
        except OSError:
            fingerprint = self.frpc_path

        cached = self._get_cached_version('frpc', fingerprint, force_refresh=force_refresh)
        if cached:
            return cached

        commands = (
            [self.frpc_path, 'version'],
            [self.frpc_path, '--version'],
            [self.frpc_path, '-v'],
        )
        last_error = ''

        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=self.frpc_work_dir
                )
                output = '\n'.join(
                    chunk for chunk in (result.stdout, result.stderr) if chunk
                ).strip()
                version = self._extract_version_from_text(output)
                if version:
                    return self._set_cached_version(
                        'frpc',
                        fingerprint,
                        version,
                        '来自本地 frpc 可执行文件'
                    )
                if result.returncode == 0 and output:
                    return self._set_cached_version(
                        'frpc',
                        fingerprint,
                        output.splitlines()[0].strip(),
                        '来自本地 frpc 可执行文件原始输出'
                    )
            except Exception as e:
                last_error = str(e)

        if last_error:
            hint = f'执行版本命令失败：{last_error}'
        else:
            hint = '本地 frpc 未输出可识别的版本号'
        return self._set_cached_version('frpc', fingerprint, '未知', hint)

    @classmethod
    def _extract_version_from_json(cls, payload):
        """从 JSON 响应中递归查找版本号字段。"""
        if isinstance(payload, dict):
            for key in ('frpsVersion', 'serverVersion', 'version'):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    normalized = cls._extract_version_from_text(value) or value.strip()
                    if normalized:
                        return normalized
            for value in payload.values():
                nested = cls._extract_version_from_json(value)
                if nested:
                    return nested
        elif isinstance(payload, list):
            for item in payload:
                nested = cls._extract_version_from_json(item)
                if nested:
                    return nested
        elif isinstance(payload, str):
            return cls._extract_version_from_text(payload)
        return ''

    def get_server_version_info(self, force_refresh: bool = False):
        """通过可选的版本地址探测远端 frps 版本。"""
        version_url = (os.getenv('FRPS_VERSION_URL') or '').strip()
        if not version_url:
            return self._set_cached_version(
                'frps',
                'not-configured',
                '未配置',
                '未配置 FRPS_VERSION_URL，无法直接探测服务端 frps 版本'
            )

        cached = self._get_cached_version('frps', version_url, force_refresh=force_refresh)
        if cached:
            return cached

        username = (os.getenv('FRPS_VERSION_USERNAME') or '').strip()
        password = os.getenv('FRPS_VERSION_PASSWORD') or ''
        auth = (username, password) if username else None

        try:
            response = requests.get(
                version_url,
                timeout=5,
                auth=auth,
                headers={'Accept': 'application/json, text/plain, text/html;q=0.9'}
            )
            response.raise_for_status()

            version = ''
            content_type = (response.headers.get('Content-Type') or '').lower()
            if 'json' in content_type:
                try:
                    version = self._extract_version_from_json(response.json())
                except ValueError:
                    version = ''

            if not version:
                version = self._extract_version_from_text(response.text)

            if version:
                return self._set_cached_version(
                    'frps',
                    version_url,
                    version,
                    '来自 FRPS_VERSION_URL 版本探测'
                )

            return self._set_cached_version(
                'frps',
                version_url,
                '未知',
                '版本地址可访问，但响应中未找到可识别的版本号'
            )
        except requests.RequestException as e:
            return self._set_cached_version(
                'frps',
                version_url,
                '不可达',
                f'访问 FRPS_VERSION_URL 失败：{str(e)}'
            )

    def get_version_summary(self):
        """汇总本地 frpc 与远端 frps 版本信息。"""
        local_version = self.get_local_version_info()
        server_version = self.get_server_version_info()
        return {
            'frpc_version': local_version['value'],
            'frpc_version_hint': local_version['hint'],
            'frps_version': server_version['value'],
            'frps_version_hint': server_version['hint'],
        }

    def _ensure_log_dir(self):
        """确保日志目录存在"""
        log_dir = self.log_dir if hasattr(self, 'log_dir') else os.path.dirname(self.log_path)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

    def _start_log_thread(self):
        """启动日志读取线程"""
        if self.log_thread is None or not self.log_thread.is_alive():
            self.stop_log_thread = False
            self.log_thread = threading.Thread(target=self._read_logs, daemon=True)
            self.log_thread.start()
            logger.info("日志读取线程已启动")

    def _stop_log_thread(self):
        """停止日志读取线程"""
        self.stop_log_thread = True
        if self.log_thread and self.log_thread.is_alive():
            self.log_thread.join(timeout=1)
            logger.info("日志读取线程已停止")

    def _read_logs(self):
        """实时读取日志文件"""
        try:
            # 确保日志文件存在
            if not os.path.exists(self.log_path):
                with open(self.log_path, 'w', encoding='utf-8') as f:
                    pass

            with open(self.log_path, 'r', encoding='utf-8') as f:
                # 移动到文件末尾
                f.seek(0, 2)
                while not self.stop_log_thread:
                    line = f.readline()
                    if line:
                        line = line.strip()
                        if line:  # 只处理非空行
                            self.log_queue.put(line)
                            # 检查错误状态
                            self._check_error_state(line)
                            logger.debug(f"读取到日志: {line}")
                    else:
                        time.sleep(0.1)  # 避免过度消耗 CPU
        except Exception as e:
            logger.error(f"读取日志失败: {str(e)}")
            self.error_state = True
            self.error_message = "日志读取失败，请检查日志目录"

    def _check_error_state(self, log_line):
        """检查日志中的错误状态"""
        # 检查常见的错误模式
        error_patterns = [
            r'bind: cannot assign requested address',
            r'connection refused',
            r'connection reset',
            r'connection timeout',
            r'no such file or directory',
            r'permission denied',
            r'address already in use',
            r'failed to start proxy',
            r'failed to connect to server',
            r'failed to login to server'
        ]
        
        for pattern in error_patterns:
            if re.search(pattern, log_line, re.IGNORECASE):
                self.error_state = True
                self.error_message = log_line.strip()
                logger.error(f"检测到错误状态: {log_line.strip()}")
                return

    def _recover_process(self):
        """尝试恢复进程信息（不创建新进程，仅附着到已存在的 frpc 进程）"""
        try:
            self.attached_pid = None
            # 遍历所有进程，查找 frpc 进程
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    # 检查进程名称和命令行
                    if proc.info['name'] == 'frpc' and self.config_path in proc.info['cmdline']:
                        # 找到匹配的进程，仅记录 PID，不创建新进程
                        self.attached_pid = proc.info['pid']
                        logger.info(f"检测到已运行 frpc 进程，PID: {self.attached_pid}")
                        # 添加恢复标记到日志
                        with open(self.log_path, 'a', encoding='utf-8') as log_file:
                            log_file.write(f"\n=== 附着到已运行 frpc 进程于 {time.strftime('%Y-%m-%d %H:%M:%S')}，PID: {self.attached_pid} ===\n")
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as e:
            logger.error(f"恢复进程信息失败: {str(e)}")
            self.process = None

    def start(self):
        """启动 frpc 服务"""
        try:
            # 重置错误状态
            self.error_state = False
            self.error_message = ""

            # 检查是否已经在运行
            if self.is_running():
                logger.warning("frpc 服务已经在运行")
                return False, "frpc 服务已经在运行"

            if not os.path.exists(self.frpc_path):
                self.error_state = True
                self.error_message = "未找到 frpc 可执行文件"
                logger.error(f"{self.error_message}: {self.frpc_path}")
                return False, "未找到 frpc 可执行文件，请先下载或上传"

            if not os.path.exists(self.config_path):
                self.error_state = True
                self.error_message = "未找到 frpc 配置文件"
                logger.error(f"{self.error_message}: {self.config_path}")
                return False, "未找到 frpc 配置文件，请先保存配置"

            # 确保 frpc 可执行
            if not os.access(self.frpc_path, os.X_OK):
                os.chmod(self.frpc_path, 0o755)

            # 启动前清空日志文件
            with open(self.log_path, 'w', encoding='utf-8') as log_file:
                log_file.write(f"\n=== frpc 服务启动于 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

            # 确保日志线程在运行
            self._start_log_thread()

            # 启动 frpc 进程
            with open(self.log_path, 'a', encoding='utf-8') as log_file:
                self.process = subprocess.Popen(
                    [self.frpc_path, '-c', self.config_path],
                    stdout=log_file,
                    stderr=log_file,
                    cwd=self.frpc_work_dir,
                    start_new_session=True  # 使用新的会话组
                )

            # 等待一段时间检查进程是否存活
            time.sleep(2)
            if not self.is_running():
                self.error_state = True
                self.error_message = "服务启动后立即退出，请检查日志"
                logger.error("服务启动后立即退出")
                with open(self.log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} [错误] {self.error_message}\n")
                return False, "服务启动失败，请检查日志"

            if self.error_state:
                return False, "服务启动失败，请检查日志"

            logger.info(f"frpc 服务已启动，PID: {self.process.pid}")
            with open(self.log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} [成功] frpc 服务已启动，PID: {self.process.pid}\n")
            return True, f"frpc 服务已启动，PID: {self.process.pid}"

        except Exception as e:
            logger.exception(f"启动 frpc 服务失败: {str(e)}")
            self.error_state = True
            self.error_message = "启动失败，请检查日志"
            self._append_log(f"\n[错误] 启动失败，请查看应用日志获取详情\n")
            return False, "启动失败，请检查日志或稍后重试"

    def stop(self):
        """停止 frpc 服务"""
        try:
            # 查找所有匹配的 frpc 进程
            found = False
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] == 'frpc' and self.config_path in proc.info['cmdline']:
                        found = True
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except psutil.TimeoutExpired:
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            if found:
                logger.info("frpc 服务已停止")
                with open(self.log_path, 'a', encoding='utf-8') as log_file:
                    log_file.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} [成功] frpc 服务已停止\n")
                return True, "frpc 服务已停止"
            else:
                logger.warning("frpc 服务未运行")
                return False, "frpc 服务未运行"
        except Exception as e:
            logger.exception(f"停止 frpc 服务失败: {str(e)}")
            self._append_log(f"\n{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} [错误] 停止失败，请查看应用日志获取详情\n")
            return False, "停止失败，请稍后重试"

    def restart(self):
        """重启 frpc 服务"""
        self.stop()
        # 重启时也清空日志
        with open(self.log_path, 'w', encoding='utf-8') as log_file:
            log_file.write(f"\n=== frpc 服务重启于 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        return self.start()

    def is_running(self):
        """直接用 psutil 检查系统中是否有目标 frpc 进程"""
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['name'] == 'frpc' and self.config_path in proc.info['cmdline']:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False

    def get_status(self):
        """获取 frpc 服务状态"""
        version_summary = self.get_version_summary()
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] == 'frpc' and self.config_path in proc.info['cmdline']:
                        return {
                            'status': 'running',
                            'pid': proc.info['pid'],
                            'error_message': self.error_message if self.error_state else '',
                            **version_summary,
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            return {
                'status': 'stopped',
                'pid': None,
                'error_message': self.error_message if self.error_state else '',
                **version_summary,
            }
        except Exception as e:
            logger.error(f"获取状态失败: {str(e)}")
            return {
                'status': 'error',
                'pid': None,
                'error_message': '状态读取失败，请稍后重试',
                **version_summary,
            }

    def get_logs(self, lines=100):
        """只读取frpc.log文件内容，返回最新日志（去除ANSI颜色码）"""
        try:
            if not os.path.exists(self.log_path):
                return []
            with open(self.log_path, 'r', encoding='utf-8') as f:
                file_logs = f.readlines()[-lines:]
                ansi_escape = re.compile(r'\x1B\[[0-9;]*[A-Za-z]')
                logs = [ansi_escape.sub('', line.strip()) for line in file_logs if line.strip()]
            return logs
        except Exception as e:
            logger.error(f"读取日志失败: {str(e)}")
            return []

    def clear_logs(self):
        """清空日志文件"""
        try:
            with open(self.log_path, 'w', encoding='utf-8') as f:
                f.write('')
            # 清空日志队列
            while not self.log_queue.empty():
                try:
                    self.log_queue.get_nowait()
                except queue.Empty:
                    break
            return True
        except Exception as e:
            logger.error(f"清空日志失败: {str(e)}")
            return False 
