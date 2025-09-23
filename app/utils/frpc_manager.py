import os
import subprocess
import signal
import psutil
import logging
from pathlib import Path
import threading
import time
import queue
import re

logger = logging.getLogger(__name__)

class FrpcManager:
    def __init__(self):
        self.frpc_path = os.path.join(os.getcwd(), 'frpc')
        self.config_path = os.path.join(os.getcwd(), 'frpc.json')
        # 将 frpc 日志输出目录与应用日志目录保持一致，或使用 FRPC_LOG_DIR
        default_app_log_file = os.getenv('LOG_FILE', '/var/log/frpc-web/app.log')
        inferred_log_dir = os.path.dirname(default_app_log_file) if default_app_log_file else os.path.join(os.getcwd(), 'logs')
        self.log_dir = os.getenv('FRPC_LOG_DIR', inferred_log_dir)
        self.log_path = os.path.join(self.log_dir, 'frpc.log')
        self.process = None
        self.attached_pid = None
        self._ensure_log_dir()
        self.log_queue = queue.Queue()
        self.log_thread = None
        self.stop_log_thread = False
        self.error_state = False
        self.error_message = ""
        self._start_log_thread()  # 在初始化时就启动日志线程
        self._recover_process()  # 尝试恢复进程信息

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
            self.error_message = f"日志读取失败: {str(e)}"

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
                return False, f"服务启动失败: {self.error_message}"

            logger.info(f"frpc 服务已启动，PID: {self.process.pid}")
            with open(self.log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} [成功] frpc 服务已启动，PID: {self.process.pid}\n")
            return True, f"frpc 服务已启动，PID: {self.process.pid}"

        except Exception as e:
            logger.error(f"启动 frpc 服务失败: {str(e)}")
            self.error_state = True
            self.error_message = str(e)
            with open(self.log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n[错误] 启动失败: {str(e)}\n")
            return False, f"启动失败: {str(e)}"

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
            logger.error(f"停止 frpc 服务失败: {str(e)}")
            with open(self.log_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} [错误] 停止失败: {str(e)}\n")
            return False, f"停止失败: {str(e)}"

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
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] == 'frpc' and self.config_path in proc.info['cmdline']:
                        return {
                            'status': 'running',
                            'pid': proc.info['pid'],
                            'error_message': self.error_message if self.error_state else ''
                        }
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            return {
                'status': 'stopped',
                'pid': None,
                'error_message': self.error_message if self.error_state else ''
            }
        except Exception as e:
            logger.error(f"获取状态失败: {str(e)}")
            return {
                'status': 'error',
                'pid': None,
                'error_message': str(e)
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