from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash
from app import db
from app.auth import bp
from app.models import User
from app.auth.forms import LoginForm, ChangePasswordForm

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')
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
        return jsonify({
            'status': 'error',
            'message': '用户名或密码错误'
        }), 401
    
    return render_template('auth/login.html')

@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@bp.route('/change-password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json()
    current_password = data.get('currentPassword')
    new_password = data.get('newPassword')
    
    if not current_password or not new_password:
        return jsonify({
            'status': 'error',
            'message': '请提供当前密码和新密码'
        }), 400
    
    # 验证当前密码
    if not current_user.check_password(current_password):
        return jsonify({
            'status': 'error',
            'message': '当前密码错误'
        }), 401
    
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
        return jsonify({
            'status': 'error',
            'message': f'密码修改失败：{str(e)}'
        }), 500 