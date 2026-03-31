from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
import os
from datetime import timedelta
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from app.utils.frpc_manager import FrpcManager
import threading
from app.runtime_settings import load_runtime_settings, resolve_runtime_path, sync_legacy_runtime_files

# 加载环境变量
load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()


def normalize_database_uri(db_uri: str, runtime_settings) -> str:
    """统一解析数据库连接，兼容容器路径和本地路径。"""
    if not db_uri.startswith('sqlite:///'):
        return db_uri

    raw_path = db_uri.replace('sqlite:///', '', 1)
    resolved_path = resolve_runtime_path(
        raw_path,
        runtime_settings.base_dir,
        data_dir=runtime_settings.data_dir,
        logs_dir=runtime_settings.logs_dir
    )
    return f'sqlite:///{resolved_path}'

def create_app():
    from app.models import User  # 移到这里，避免循环导入
    
    app = Flask(__name__)
    runtime_settings = load_runtime_settings()
    
    # 配置
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key or secret_key.strip() in ('', 'your-secret-key-here', 'replace-with-a-random-secret'):
        raise RuntimeError('SECRET_KEY 未配置或仍为默认值，请在 .env 中设置安全的随机密钥')
    app.config['SECRET_KEY'] = secret_key
    app.config['APP_BASE_DIR'] = runtime_settings.base_dir
    app.config['APP_DATA_DIR'] = runtime_settings.data_dir
    app.config['FRPC_WORK_DIR'] = runtime_settings.frpc_work_dir
    app.config['FRPC_BINARY_PATH'] = runtime_settings.frpc_binary_path
    app.config['FRPC_CONFIG_PATH'] = runtime_settings.frpc_config_path
    app.config['WEB_CONFIG_PATH'] = runtime_settings.web_config_path
    app.config['APP_PORT'] = runtime_settings.app_port
    app.config['LOGS_DIR'] = runtime_settings.logs_dir

    # 统一并规范化数据库路径：优先用环境变量，默认写入持久化 data 目录
    default_db_path = os.path.join(runtime_settings.data_dir, 'frpc.db')
    db_uri = os.getenv('DATABASE_URL', f'sqlite:///{default_db_path}')
    db_uri = normalize_database_uri(db_uri, runtime_settings)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)  # 设置 session 有效期为1小时
    app.config['JSON_AS_ASCII'] = False  # 允许 JSON 响应包含非 ASCII 字符
    app.config['JSONIFY_MIMETYPE'] = 'application/json'  # 设置 JSON 响应的 MIME 类型

    # 会话 Cookie 安全配置（从环境变量读取，给出安全默认）
    def _env_bool(name: str, default: str) -> bool:
        return str(os.getenv(name, default)).lower() in ('1', 'true', 'yes', 'on')
    app.config.update(
        SESSION_COOKIE_SECURE=_env_bool('SESSION_COOKIE_SECURE', 'False'),
        SESSION_COOKIE_HTTPONLY=_env_bool('SESSION_COOKIE_HTTPONLY', 'True'),
        SESSION_COOKIE_SAMESITE=os.getenv('SESSION_COOKIE_SAMESITE', 'Lax'),
        REMEMBER_COOKIE_SECURE=_env_bool('REMEMBER_COOKIE_SECURE', 'False'),
        REMEMBER_COOKIE_HTTPONLY=_env_bool('REMEMBER_COOKIE_HTTPONLY', 'True'),
    )
    
    # 添加错误处理
    @app.errorhandler(404)
    def not_found_error(error):
        return jsonify({'error': 'Not found', 'message': '请求的资源不存在'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.exception(f'应用内部错误: {str(error)}')
        return jsonify({'error': 'Internal server error', 'message': '服务器内部错误，请稍后重试'}), 500

    @app.errorhandler(401)
    def unauthorized_error(error):
        return jsonify({'error': 'Unauthorized', 'message': '请先登录'}), 401

    # 配置日志
    def setup_logging():
        # 获取日志配置
        log_level = os.getenv('LOG_LEVEL', 'INFO')
        log_format = os.getenv('LOG_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        log_file = resolve_runtime_path(
            os.getenv('LOG_FILE', os.path.join(runtime_settings.logs_dir, 'app.log')),
            runtime_settings.base_dir,
            data_dir=runtime_settings.data_dir,
            logs_dir=runtime_settings.logs_dir
        )
        
        # 设置日志级别
        numeric_level = getattr(logging, log_level.upper(), None)
        if not isinstance(numeric_level, int):
            numeric_level = logging.INFO
        
        # 配置根日志记录器
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)
        if root_logger.handlers:
            for handler in list(root_logger.handlers):
                root_logger.removeHandler(handler)
                handler.close()
        
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
    # 延迟导入，避免循环依赖，并初始化 WebSocket
    from app.main.routes import sock
    sock.init_app(app)
    
    # 设置登录视图
    login_manager.login_view = 'auth.login'
    login_manager.login_message = '请先登录'

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        from flask import request, redirect, url_for
        # 如果是 API 请求或 Ajax 请求，返回 401 JSON；否则重定向到登录页
        accepts_json = request.accept_mimetypes.best == 'application/json' or \
                        request.headers.get('Accept', '').startswith('application/json') or \
                        request.is_json or \
                        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if accepts_json:
            return jsonify({'error': 'Unauthorized', 'message': '请先登录'}), 401
        return redirect(url_for('auth.login'))
    
    # 添加会话验证
    @login_manager.user_loader
    def load_user(user_id):
        user = db.session.get(User, int(user_id))
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
            runtime_settings.data_dir,
            runtime_settings.frpc_work_dir,
            runtime_settings.logs_dir
        ]
        for directory in directories:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logger.info(f'创建目录: {directory}')

    create_directories()
    synced_files = sync_legacy_runtime_files(runtime_settings)
    for source_path, target_path in synced_files:
        logger.info(f'已将旧版运行文件同步到持久化目录: {source_path} -> {target_path}')

    # 检查并自动启动 frpc
    def auto_start_frpc():
        try:
            frpc_path = runtime_settings.frpc_binary_path
            config_path = runtime_settings.frpc_config_path
            
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
