import os
import time
import subprocess
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

class NetworkChecker:
    def __init__(self):
        self.check_interval = int(os.getenv('NETWORK_CHECK_INTERVAL', 1800))  # 默认30分钟
        self.check_hosts = os.getenv('NETWORK_CHECK_HOSTS', '8.8.8.8,114.114.114.114').split(',')
        self.check_timeout = int(os.getenv('NETWORK_CHECK_TIMEOUT', 5))
        self.last_check_time = 0
        self.is_online = True

    def check_network(self) -> bool:
        """检查网络连接状态"""
        current_time = time.time()
        if current_time - self.last_check_time < self.check_interval:
            return self.is_online

        self.last_check_time = current_time
        self.is_online = self._ping_hosts()
        return self.is_online

    def _ping_hosts(self) -> bool:
        """ping 指定的主机列表"""
        for host in self.check_hosts:
            try:
                # 使用 ping 命令检查网络连接
                result = subprocess.run(
                    ['ping', '-c', '1', '-W', str(self.check_timeout), host],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.check_timeout + 1
                )
                if result.returncode == 0:
                    return True
            except subprocess.TimeoutExpired:
                logger.warning(f"Ping {host} timeout")
            except Exception as e:
                logger.error(f"Error pinging {host}: {str(e)}")
        return False

    def start_monitoring(self, callback):
        """开始监控网络状态"""
        while True:
            try:
                is_online = self.check_network()
                if not is_online:
                    logger.warning("Network connection lost, waiting for recovery...")
                    callback(False)
                elif not self.is_online:  # 网络恢复
                    logger.info("Network connection restored")
                    callback(True)
                self.is_online = is_online
            except Exception as e:
                logger.error(f"Error in network monitoring: {str(e)}")
            time.sleep(self.check_interval)  # 使用配置的检查间隔 