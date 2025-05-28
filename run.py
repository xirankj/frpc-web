import os
os.environ['EVENTLET_NO_GREENDNS'] = 'yes'
import eventlet
eventlet.monkey_patch()

from flask import Flask
from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import threading
from app.utils.network_check import NetworkChecker
from app.utils.frpc_manager import FrpcManager
from eventlet import wsgi  # 这里要加上

# 加载环境变量
load_dotenv()


app = create_app()

def init_db():
    """初始化数据库并创建默认管理员账户"""
    with app.app_context():
        db.create_all()
        # 从环境变量获取默认用户名和密码
        default_username = os.getenv('DEFAULT_USERNAME', 'admin')
        default_password = os.getenv('DEFAULT_PASSWORD', 'admin123')
        
        # 检查是否存在默认管理员账户
        admin = User.query.filter_by(username=default_username).first()
        if not admin:
            admin = User(username=default_username)
            admin.password = default_password  # 使用环境变量中的密码
            admin.is_first_login = True  # 标记为首次登录
            db.session.add(admin)
            db.session.commit()
            print(f'已创建默认管理员账户: {default_username}')

def network_status_callback(is_online: bool):
    """网络状态变化回调函数"""
    if is_online:
        # 网络恢复，尝试重启 frpc 服务
        frpc_manager = FrpcManager()
        if not frpc_manager.is_running():
            app.logger.info("Attempting to restart FRPC service after network recovery...")
            frpc_manager.start()
            app.logger.info("FRPC service successfully restarted after network recovery")
        else:
            app.logger.info("FRPC service is already running, no restart needed")
    else:
        app.logger.warning("Network connection lost, FRPC service may be affected")

def start_network_monitor():
    """启动网络监控"""
    checker = NetworkChecker()
    monitor_thread = threading.Thread(
        target=checker.start_monitoring,
        args=(network_status_callback,),
        daemon=True
    )
    monitor_thread.start()
    app.logger.info("Network monitoring started")

if __name__ == '__main__':
    # 初始化数据库
    init_db()
    # 启动网络监控
    start_network_monitor()
    # 启动 Web 服务
    port = int(os.getenv('WEB_PORT', 8001))
    host = '0.0.0.0'  # 明确指定监听所有网络接口
    print(f'服务器已启动，监听 {host}:{port}...')
    wsgi.server(eventlet.listen((host, port)), app) 