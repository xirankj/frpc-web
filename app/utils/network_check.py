import os
import time
import subprocess
import logging
import platform

logger = logging.getLogger(__name__)

class NetworkChecker:
    def __init__(self):
        self.check_interval = int(os.getenv('NETWORK_CHECK_INTERVAL', 1800))  # 默认30分钟
        self.check_hosts = [
            host.strip()
            for host in os.getenv('NETWORK_CHECK_HOSTS', '8.8.8.8,114.114.114.114').split(',')
            if host.strip()
        ]
        self.check_timeout = int(os.getenv('NETWORK_CHECK_TIMEOUT', 5))
        self.last_check_time = 0
        self.is_online = True

    def _build_ping_command(self, host: str) -> list[str]:
        """根据当前平台构造 ping 命令。"""
        if platform.system().lower() == 'windows':
            return ['ping', '-n', '1', '-w', str(self.check_timeout * 1000), host]
        return ['ping', '-c', '1', '-W', str(self.check_timeout), host]

    def check_network(self, force: bool = False) -> bool:
        """检查网络连接状态"""
        current_time = time.time()
        if not force and current_time - self.last_check_time < self.check_interval:
            return self.is_online

        self.last_check_time = current_time
        current_status = self._ping_hosts()
        self.is_online = current_status
        return current_status

    def _ping_hosts(self) -> bool:
        """ping 指定的主机列表"""
        for host in self.check_hosts:
            try:
                # 使用 ping 命令检查网络连接
                result = subprocess.run(
                    self._build_ping_command(host),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.check_timeout + 1
                )
                if result.returncode == 0:
                    return True
            except subprocess.TimeoutExpired:
                logger.warning(f"Ping 检查 {host} 超时")
            except Exception as e:
                logger.error(f"Ping 检查 {host} 时出错: {str(e)}")
        return False

    def start_monitoring(self, callback):
        """开始监控网络状态"""
        previous_status = self.is_online
        while True:
            try:
                current_status = self.check_network(force=True)
                if current_status != previous_status:
                    if current_status:
                        logger.info("网络连接已恢复")
                        callback(True)
                    else:
                        logger.warning("网络连接中断，等待恢复中...")
                        callback(False)
                previous_status = current_status
            except Exception as e:
                logger.error(f"网络监控过程中出错: {str(e)}")
            time.sleep(self.check_interval)  # 使用配置的检查间隔 
