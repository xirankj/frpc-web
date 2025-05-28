from flask import render_template, jsonify, request, Response
from flask_login import login_required, current_user
from app.main import bp
import json
import os
import requests
import tarfile
import shutil
from pathlib import Path
import threading
import time
import logging
from flask_sock import Sock
from app.utils.frpc_manager import FrpcManager
import queue

# 配置日志
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# 创建 WebSocket 实例
sock = Sock()

# 全局变量存储下载状态
download_status = {
    'is_downloading': False,
    'progress': '',
    'completed': False,
    'error': False,  # 添加错误状态标识
    'error_message': ''  # 添加错误消息
}

# WebSocket 连接列表
ws_clients = set()

# 创建日志队列
log_queue = queue.Queue()

frpc_manager = FrpcManager()

download_thread = None

def broadcast_logs():
    """广播日志到所有 WebSocket 客户端"""
    while True:
        try:
            log_message = log_queue.get()
            message = json.dumps({
                'type': 'log',
                'content': log_message
            })
            for client in ws_clients.copy():
                try:
                    client.send(message)
                except Exception as e:
                    logger.error(f'发送日志消息失败: {str(e)}')
                    ws_clients.remove(client)
        except Exception as e:
            logger.error(f'广播日志时出错: {str(e)}')
        time.sleep(0.1)  # 避免过度消耗 CPU

# 启动日志广播线程
log_broadcast_thread = threading.Thread(target=broadcast_logs, daemon=True)
log_broadcast_thread.start()

@sock.route('/ws')
def ws_handler(ws):
    """处理 WebSocket 连接"""
    logger.info('新的 WebSocket 连接')
    ws_clients.add(ws)
    status_thread = None
    status_thread_stop = threading.Event()
    def push_status():
        try:
            while not status_thread_stop.is_set():
                status = frpc_manager.get_status()
                ws.send(json.dumps({
                    'type': 'service_status',
                    'status': status['status'],
                    'pid': status.get('pid'),
                    'error_message': status.get('error_message', '')
                }))
                time.sleep(2)
        except Exception as e:
            logger.error(f'WebSocket 状态推送中断: {str(e)}')
    try:
        while True:
            try:
                message = ws.receive()
                logger.debug(f'收到 WebSocket 消息: {message}')
                if message:
                    data = json.loads(message)
                    if data.get('type') == 'start_download_progress':
                        logger.info('开始订阅下载进度')
                        ws.send(json.dumps({
                            'type': 'download_progress',
                            'message': download_status['progress'],
                            'completed': download_status['completed'],
                            'error': download_status['error'],
                            'error_message': download_status['error_message']
                        }))
                    elif data.get('type') == 'get_log':
                        # 直接读取最新日志并推送
                        logs = frpc_manager.get_logs()
                        ws.send(json.dumps({
                            'type': 'log',
                            'content': '\n'.join(logs)
                        }))
                    elif data.get('type') == 'clear_log':
                        frpc_manager.clear_logs()
                        ws.send(json.dumps({
                            'type': 'log',
                            'content': ''
                        }))
                    elif data.get('type') == 'get_status':
                        # 启动独立线程持续推送状态
                        if status_thread is None or not status_thread.is_alive():
                            status_thread_stop.clear()
                            status_thread = threading.Thread(target=push_status, daemon=True)
                            status_thread.start()
            except Exception as e:
                logger.error(f'处理 WebSocket 消息时出错: {str(e)}')
                break
    except Exception as e:
        logger.error(f'WebSocket 连接出错: {str(e)}')
    finally:
        logger.info('WebSocket 连接关闭')
        ws_clients.remove(ws)
        status_thread_stop.set()

def broadcast_download_status():
    """广播下载状态到所有 WebSocket 客户端"""
    message = json.dumps({
        'type': 'download_progress',
        'message': download_status['progress'],
        'completed': download_status['completed'],
        'error': download_status['error'],
        'error_message': download_status['error_message']
    })
    logger.debug(f'广播下载状态: {message}')
    for client in ws_clients.copy():
        try:
            client.send(message)
        except Exception as e:
            logger.error(f'发送 WebSocket 消息失败: {str(e)}')
            ws_clients.remove(client)

@bp.route('/')
@bp.route('/index')
@login_required
def index():
    return render_template('main/index.html')

@bp.route('/save', methods=['POST'])
@login_required
def save_frpc_config():
    logger.info('收到保存 frpc.json 请求')
    try:
        config = request.get_json()
        logger.debug(f'保存的配置内容: {config}')
        
        # 过滤掉 enabled 字段
        if 'proxies' in config:
            config['proxies'] = [{
                key: value for key, value in proxy.items() if key != 'enabled'
            } for proxy in config['proxies']]
        
        with open('frpc.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info('frpc.json 保存成功')
        return jsonify({
            'status': 'success',
            'message': 'frpc.json 已保存'
        })
    except Exception as e:
        logger.error(f'保存 frpc.json 失败: {str(e)}')
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@bp.route('/frpc.json')
@login_required
def get_config():
    try:
        with open('frpc.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        return jsonify(config)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/config.json')
@login_required
def get_config_file():
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
            return jsonify(config)
        else:
            # 如果 config.json 不存在，尝试从 frpc.json 读取并添加 enabled 字段
            if os.path.exists('frpc.json'):
                with open('frpc.json', 'r', encoding='utf-8') as f:
                    config = json.load(f)
                # 为所有客户端配置添加 enabled 字段
                if 'proxies' in config:
                    config['proxies'] = [{
                        **proxy,
                        'enabled': True  # 从 frpc.json 读取的配置默认都是启用的
                    } for proxy in config['proxies']]
                # 保存为 config.json
                with open('config.json', 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                return jsonify(config)
            return jsonify({
                'status': 'error',
                'message': '配置文件不存在'
            }), 404
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@bp.route('/save-config', methods=['POST'])
@login_required
def save_config_file():
    try:
        config = request.get_json()
        # 确保所有客户端配置都有 enabled 字段
        if 'proxies' in config:
            config['proxies'] = [{
                **proxy,
                'enabled': proxy.get('enabled', True)  # 如果没有 enabled 字段，默认为 True
            } for proxy in config['proxies']]
        
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return jsonify({
            'status': 'success',
            'message': '配置已保存'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@bp.route('/check-frpc')
@login_required
def check_frpc():
    try:
        frpc_path = Path('frpc')
        exists = frpc_path.exists() and frpc_path.is_file()
        return jsonify({
            'status': 'success',
            'exists': exists
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def download_and_extract():
    import requests
    import tarfile
    import os
    import shutil
    url = "https://github.com/fatedier/frp/releases/download/v0.62.1/frp_0.62.1_linux_amd64.tar.gz"
    filename = "frp_0.62.1_linux_amd64.tar.gz"
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(filename, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)
        # 解压
        with tarfile.open(filename, 'r:gz') as tar:
            tar.extractall()
        # 查找解压目录
        extracted_dir = None
        for item in os.listdir('.'):
            if item.startswith('frp_') and os.path.isdir(item):
                extracted_dir = item
                break
        if not extracted_dir:
            return False, '未找到解压目录'
        frpc_path = os.path.join(extracted_dir, 'frpc')
        if not os.path.exists(frpc_path):
            return False, '未找到 frpc 文件'
        # 移动 frpc 到根目录
        if os.path.exists('frpc'):
            os.remove('frpc')
        shutil.move(frpc_path, 'frpc')
        # 清理解压目录和压缩包
        shutil.rmtree(extracted_dir)
        os.remove(filename)
        # 设置可执行权限（非Windows）
        if os.name != 'nt':
            os.chmod('frpc', 0o755)
        return True, '下载并解压完成，frpc 已放到根目录。'
    except Exception as e:
        return False, f'下载或解压失败: {e}'

@bp.route('/download-frpc', methods=['POST'])
@login_required
def download_frpc():
    global download_thread
    try:
        # 如果已经在下载，返回错误
        if getattr(download_frpc, '_is_downloading', False):
            return jsonify({
                'status': 'error',
                'message': '已有下载任务正在进行'
            }), 400
        setattr(download_frpc, '_is_downloading', True)
        def run_download():
            success, msg = download_and_extract()
            setattr(download_frpc, '_last_result', (success, msg))
            setattr(download_frpc, '_is_downloading', False)
        download_thread = threading.Thread(target=run_download, daemon=True)
        download_thread.start()
        download_thread.join()  # 阻塞直到下载完成
        success, msg = getattr(download_frpc, '_last_result', (False, '未知错误'))
        if success:
            return jsonify({'status': 'success', 'message': msg})
        else:
            return jsonify({'status': 'error', 'message': msg}), 500
    except Exception as e:
        setattr(download_frpc, '_is_downloading', False)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@bp.route('/stop-download', methods=['POST'])
@login_required
def stop_download():
    global download_thread
    if getattr(download_frpc, '_is_downloading', False):
        # Python线程无法强制终止，只能通过状态标志配合下载函数实现中断。
        # 这里简单地将状态标志设为False，实际下载线程会继续运行直到结束。
        setattr(download_frpc, '_is_downloading', False)
        return jsonify({'status': 'success', 'message': '已请求停止下载任务（注意：实际线程会继续运行到结束）'})
    else:
        return jsonify({'status': 'error', 'message': '没有正在进行的下载任务'}), 400

@bp.route('/cancel-download', methods=['POST'])
@login_required
def cancel_download():
    global download_status
    logger.info('收到取消下载请求')
    if download_status['is_downloading']:
        # 设置取消状态
        download_status['is_downloading'] = False
        download_status['progress'] = '下载已取消'
        download_status['completed'] = True
        download_status['error'] = True
        download_status['error_message'] = '用户取消了下载'
        
        # 广播取消状态
        broadcast_download_status()
        
        # 清理可能存在的临时文件
        try:
            if os.path.exists('frp.tar.gz'):
                os.remove('frp.tar.gz')
                logger.info('已删除临时下载文件')
        except Exception as e:
            logger.error(f'清理临时文件失败: {str(e)}')
        
        return jsonify({
            'status': 'success',
            'message': '下载已取消'
        })
    return jsonify({
        'status': 'error',
        'message': '没有正在进行的下载任务'
    }), 400

@bp.route('/download-progress')
@login_required
def download_progress():
    try:
        return jsonify({
            'status': 'success',
            'message': download_status['progress'],
            'completed': download_status['completed'],
            'error': download_status['error'],
            'error_message': download_status['error_message']
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@bp.route('/frpc/status')
@login_required
def frpc_status():
    """获取 frpc 服务状态"""
    try:
        status = frpc_manager.get_status()
        return jsonify(status)
    except Exception as e:
        logger.error(f"获取状态失败: {str(e)}")
        return jsonify({'error': '获取状态失败', 'message': str(e)}), 500

@bp.route('/frpc/start', methods=['POST'])
@login_required
def frpc_start():
    """启动 frpc 服务"""
    try:
        success, message = frpc_manager.start()
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        logger.error(f"启动服务失败: {str(e)}")
        return jsonify({'error': '启动服务失败', 'message': str(e)}), 500

@bp.route('/frpc/stop', methods=['POST'])
@login_required
def frpc_stop():
    """停止 frpc 服务"""
    try:
        success, message = frpc_manager.stop()
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        logger.error(f"停止服务失败: {str(e)}")
        return jsonify({'error': '停止服务失败', 'message': str(e)}), 500

@bp.route('/frpc/restart', methods=['POST'])
@login_required
def frpc_restart():
    """重启 frpc 服务"""
    try:
        success, message = frpc_manager.restart()
        return jsonify({
            'success': success,
            'message': message
        })
    except Exception as e:
        logger.error(f"重启服务失败: {str(e)}")
        return jsonify({'error': '重启服务失败', 'message': str(e)}), 500

@bp.route('/frpc/logs')
@login_required
def frpc_logs():
    """获取 frpc 日志"""
    try:
        logs = frpc_manager.get_logs()
        # 将日志放入队列以广播给所有客户端
        for log in logs:
            log_queue.put(log)
        return jsonify({'logs': logs})
    except Exception as e:
        logger.error(f'获取日志失败: {str(e)}')
        return jsonify({'error': '获取日志失败', 'message': str(e)}), 500

@bp.route('/delete-frpc-config', methods=['POST'])
@login_required
def delete_frpc_config():
    try:
        if os.path.exists('frpc.json'):
            os.remove('frpc.json')
        return jsonify({
            'status': 'success',
            'message': 'frpc.json 已删除'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500 