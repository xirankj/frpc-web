"""
Flask 扩展模块
用于避免循环导入问题
"""
from flask_sock import Sock

# WebSocket 实例
sock = Sock()