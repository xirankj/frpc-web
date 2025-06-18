import json
import asyncio
from flask import current_app
from flask_sock import Sock
from threading import Lock, Thread
import time
from app.main.routes import download_status  # 导入下载状态变量

sock = Sock()
clients = set()
clients_lock = Lock()
download_progress_clients = set()
download_progress_lock = Lock()

def start_download_progress_monitor():
    """启动下载进度监控线程"""
    def monitor():
        last_progress = None
        while True:
            with download_progress_lock:
                if not download_progress_clients:
                    break
                
                try:
                    # 使用全局变量获取进度
                    progress = {
                        'message': download_status['progress'],
                        'completed': download_status['completed']
                    }
                    
                    # 只在进度有变化时发送
                    if progress != last_progress:
                        message = json.dumps({
                            'type': 'download_progress',
                            'message': progress['message'],
                            'completed': progress['completed']
                        })
                        
                        # 发送进度到所有订阅的客户端
                        for client in list(download_progress_clients):
                            try:
                                client.send(message)
                            except Exception as e:
                                current_app.logger.error(f'发送下载进度失败: {str(e)}')
                                download_progress_clients.remove(client)
                        
                        last_progress = progress
                        
                        # 如果下载完成，等待一段时间后清理订阅
                        if progress['completed']:
                            time.sleep(2)  # 等待2秒确保客户端收到完成消息
                            download_progress_clients.clear()
                            break
                except Exception as e:
                    current_app.logger.error(f'获取下载进度失败: {str(e)}')
                    time.sleep(1)  # 发生错误时等待1秒再重试
                    continue
            
            time.sleep(0.2)  # 每0.2秒更新一次进度，提高实时性
    
    thread = Thread(target=monitor, daemon=True)
    thread.start()
    return thread

def start_service_status_monitor():
    """启动服务状态监控线程"""
    def monitor():
        last_status = None
        while True:
            with clients_lock:
                if not clients:
                    break
                
                current_status = current_app.service_manager.get_status()
                if current_status != last_status:
                    message = json.dumps({
                        'type': 'service_status',
                        'status': current_status
                    })
                    
                    # 广播状态到所有客户端
                    for client in list(clients):
                        try:
                            client.send(message)
                        except Exception:
                            clients.remove(client)
                    
                    last_status = current_status
            
            time.sleep(1)  # 每秒检查一次状态
    
    thread = Thread(target=monitor, daemon=True)
    thread.start()

@sock.route('/ws')
def websocket(ws):
    """WebSocket 连接处理"""
    with clients_lock:
        clients.add(ws)
    
    download_monitor_thread = None
    
    try:
        while True:
            message = ws.receive()
            if message is None:
                break
                
            try:
                data = json.loads(message)
                if data['type'] == 'get_log':
                    # 获取日志
                    log_content = current_app.logger.get_log()
                    ws.send(json.dumps({
                        'type': 'log',
                        'content': log_content
                    }))
                elif data['type'] == 'clear_log':
                    # 清空日志
                    current_app.logger.clear_log()
                    ws.send(json.dumps({
                        'type': 'log',
                        'content': ''
                    }))
                elif data['type'] == 'start_download_progress':
                    # 订阅下载进度
                    with download_progress_lock:
                        download_progress_clients.add(ws)
                        if len(download_progress_clients) == 1:
                            # 第一个订阅者，启动监控
                            download_monitor_thread = start_download_progress_monitor()
                elif data['type'] == 'get_service_status':
                    # 获取服务状态
                    status = current_app.service_manager.get_status()
                    ws.send(json.dumps({
                        'type': 'service_status',
                        'status': status
                    }))
            except json.JSONDecodeError:
                current_app.logger.error('无效的 WebSocket 消息格式')
            except Exception as e:
                current_app.logger.error(f'处理 WebSocket 消息时出错: {str(e)}')
    finally:
        with clients_lock:
            clients.remove(ws)
        with download_progress_lock:
            if ws in download_progress_clients:
                download_progress_clients.remove(ws)
                # 如果没有订阅者了，等待监控线程结束
                if not download_progress_clients and download_monitor_thread:
                    download_monitor_thread.join(timeout=1)

def broadcast_log(log_content):
    """广播日志到所有连接的客户端"""
    message = json.dumps({
        'type': 'log',
        'content': log_content
    })
    with clients_lock:
        for client in list(clients):
            try:
                client.send(message)
            except Exception:
                clients.remove(client)

def broadcast_service_status(status):
    """广播服务状态到所有连接的客户端"""
    message = json.dumps({
        'type': 'service_status',
        'status': status
    })
    with clients_lock:
        for client in list(clients):
            try:
                client.send(message)
            except Exception:
                clients.remove(client) 