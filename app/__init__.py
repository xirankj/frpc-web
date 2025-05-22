from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from app.websocket import sock  # 导入 WebSocket
import os
from datetime import timedelta
from flask_sock import Sock
from app.main.routes import sock
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from app.utils.frpc_manager import FrpcManager
import threading

# 加载环境变量
load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()

def create_app():
    from app.models import User  # 移到这里，避免循环导入
    
    app = Flask(__name__)
    
    # 配置
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///frpc.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)  # 设置 session 有效期为1小时
    app.config['JSON_AS_ASCII'] = False  # 允许 JSON 响应包含非 ASCII 字符
    app.config['JSONIFY_MIMETYPE'] = 'application/json'  # 设置 JSON 响应的 MIME 类型
    
    # 添加错误处理
    @app.errorhandler(404)
    def not_found_error(error):
        return jsonify({'error': 'Not found', 'message': str(error)}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error', 'message': str(error)}), 500

    @app.errorhandler(401)
    def unauthorized_error(error):
        return jsonify({'error': 'Unauthorized', 'message': '请先登录'}), 401

    # 配置日志
    def setup_logging():
        # 获取日志配置
        log_level = os.getenv('LOG_LEVEL', 'INFO')
        log_format = os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_file = os.getenv('LOG_FILE', '/var/log/frpc-web/app.log')
        
        # 设置日志级别
        numeric_level = getattr(logging, log_level.upper(), None)
        if not isinstance(numeric_level, int):
            numeric_level = logging.INFO
        
        # 配置根日志记录器
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)
        
        # 创建格式化器
        formatter = logging.Formatter(log_format)
        
        # 添加控制台处理器（输出到 Docker 日志）
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        # 添加文件处理器（如果配置了日志文件）
        if log_file:
            # 确保日志目录存在
            log_dir = os.path.dirname(log_file)
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            # 创建轮转文件处理器
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10*1024*1024,  # 10MB
                backupCount=3
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        
        # 设置 Werkzeug 日志级别
        logging.getLogger('werkzeug').setLevel(logging.WARNING)

    # 初始化日志
    setup_logging()

    # 创建日志记录器
    logger = logging.getLogger(__name__)
    logger.info('应用初始化开始')
    
    # 初始化扩展
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    sock.init_app(app)  # 初始化 WebSocket
    
    # 设置登录视图
    login_manager.login_view = 'auth.login'
    login_manager.login_message = '请先登录'
    
    # 添加会话验证
    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.get(int(user_id))
        if user:
            # 如果 last_login 或 password_changed_at 为空，允许通过
            if user.password_changed_at and user.last_login:
                if user.password_changed_at > user.last_login:
                    return None
        return user
    
    # 注册蓝图
    from app.main import bp as main_bp
    app.register_blueprint(main_bp)
    
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp)
    
    logger.info('应用初始化完成')

    # 创建必要的目录
    def create_directories():
        directories = [
            'data',
            'data/frpc',
            'logs'
        ]
        for directory in directories:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logger.info(f'创建目录: {directory}')

    create_directories()

    # 检查并自动启动 frpc
    def auto_start_frpc():
        try:
            frpc_path = os.path.join(os.getcwd(), 'frpc')
            config_path = os.path.join(os.getcwd(), 'frpc.json')
            
            # 检查文件是否存在
            if os.path.exists(frpc_path) and os.path.exists(config_path):
                logger.info('检测到 frpc 和配置文件存在，尝试自动启动服务')
                frpc_manager = FrpcManager()
                
                # 检查服务是否已经在运行
                if not frpc_manager.is_running():
                    success, message = frpc_manager.start()
                    if success:
                        logger.info('frpc 服务自动启动成功')
                    else:
                        logger.error(f'frpc 服务自动启动失败: {message}')
                else:
                    logger.info('frpc 服务已经在运行')
            else:
                logger.info('未检测到 frpc 或配置文件，跳过自动启动')
        except Exception as e:
            logger.error(f'自动启动 frpc 服务时出错: {str(e)}')

    # 在后台线程中启动 frpc
    threading.Thread(target=auto_start_frpc, daemon=True).start()
    
    return app 