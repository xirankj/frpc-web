from flask import render_template, redirect, url_for, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from app import db
from app.auth import bp
from app.models import User
from app.utils.password_validator import PasswordValidator
import logging


password_validator = PasswordValidator()
logger = logging.getLogger(__name__)


def get_json_data():
    """安全读取 JSON 请求体，避免空请求导致异常。"""
    return request.get_json(silent=True) or {}


def auth_error(message: str, status_code: int = 400):
    """统一返回认证相关错误。"""
    return jsonify({
        'status': 'error',
        'message': message
    }), status_code


def build_login_page_config():
    """构建登录页前端运行所需的配置。"""
    return {
        'loginUrl': url_for('auth.login'),
        'changePasswordUrl': url_for('auth.change_password'),
        'redirectUrl': url_for('main.index'),
        'repoUrl': 'https://github.com/xirankj/frpc-web',
        'repoLabel': '查看项目仓库',
        'projectName': 'frpc-web',
        'projectIntroTitle': '轻量级 FRPC 可视化管理面板',
        'projectIntroDescription': '统一完成配置编辑、服务启停、日志查看与状态监控，让 FRPC 管理更直接。'
    }

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        data = get_json_data()
        username = (data.get('username') or '').strip()
        password = data.get('password') or ''

        if not username or not password:
            return auth_error('请提供用户名和密码')

        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            user.update_last_login()  # 更新最后登录时间
            if user.is_first_login:
                return jsonify({
                    'status': 'success',
                    'requireNewPassword': True
                })
            return jsonify({
                'status': 'success',
                'message': '登录成功'
            })
        return auth_error('用户名或密码错误', 401)
    
    return render_template('auth/login.html', login_page_config=build_login_page_config())

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    data = get_json_data()
    current_password = data.get('currentPassword') or ''
    new_password = data.get('newPassword') or ''
    
    if not current_password or not new_password:
        return auth_error('请提供当前密码和新密码')
    
    # 验证当前密码
    if not current_user.check_password(current_password):
        return auth_error('当前密码错误', 401)

    if current_password == new_password:
        return auth_error('新密码不能与当前密码相同')

    is_valid, password_errors = password_validator.validate(new_password)
    if not is_valid:
        return jsonify({
            'status': 'error',
            'message': '；'.join(password_errors),
            'errors': password_errors
        }), 400
    
    try:
        # 更新密码
        current_user.password = new_password
        current_user.is_first_login = False
        db.session.commit()
        
        # 登出用户
        logout_user()
        
        return jsonify({
            'status': 'success',
            'message': '密码修改成功，请重新登录',
            'redirect': url_for('auth.login')
        })
    except Exception as e:
        db.session.rollback()
        logger.exception(f'密码修改失败: {str(e)}')
        return auth_error('密码修改失败，请稍后重试', 500)
