from flask import render_template, jsonify, request
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
from app.runtime_settings import load_runtime_settings
from app.services.runtime_state import runtime_state, DownloadCancelledError
from app.utils.input_validator import InputValidator

logger = logging.getLogger(__name__)

# Web 管理专用配置字段，只写入 config.json，不写入 frpc.json
WEB_ONLY_CONFIG_FIELDS = ('autoRetry',)

# 创建 WebSocket 实例
sock = Sock()

frpc_manager = FrpcManager(enable_auto_retry_watchdog=True)
runtime_state.ensure_log_broadcaster_started()


def get_runtime_settings():
    """统一读取运行时路径配置。"""
    return load_runtime_settings()


def json_error(message: str, status_code: int = 500):
    """统一返回前端可读的错误响应。"""
    return jsonify({'status': 'error', 'message': message}), status_code


def normalize_proxies_list(config: dict, with_enabled: bool | None = None):
    """统一校验并规范化 proxies 字段。"""
    proxies = config.get('proxies')
    if proxies is None:
        return None, []
    if not isinstance(proxies, list):
        return None, ['proxies 必须是数组']

    normalized = []
    for proxy in proxies:
        if not isinstance(proxy, dict):
            return None, ['proxies 中的每一项都必须是对象']
        if with_enabled is True:
            normalized.append({
                **proxy,
                'enabled': proxy.get('enabled', True)
            })
        elif with_enabled is False:
            normalized.append({
                key: value for key, value in proxy.items() if key != 'enabled'
            })
        else:
            normalized.append(dict(proxy))
    return normalized, []


def normalize_web_config_payload(config: dict):
    """统一规范化 Web 配置结构，避免前端因缺省字段崩溃。"""
    normalized = dict(config)
    normalized['autoRetry'] = InputValidator.normalize_auto_retry_config(
        normalized.get('autoRetry')
    )

    normalized_proxies, proxy_errors = normalize_proxies_list(normalized, with_enabled=True)
    if proxy_errors:
        return None, proxy_errors

    normalized['proxies'] = normalized_proxies or []
    return normalized, []


def log_internal_error(log_message: str, exc: Exception, user_message: str, status_code: int = 500):
    """记录详细错误，但对前端只返回通用消息。"""
    logger.exception(f'{log_message}: {str(exc)}')
    return json_error(user_message, status_code)


def get_download_snapshot():
    """读取当前下载状态快照。"""
    return runtime_state.download_manager.snapshot()


def get_restart_snapshot():
    """读取当前重启任务状态快照。"""
    return runtime_state.restart_manager.snapshot()


def strip_web_only_fields(config: dict):
    """剥离仅供 Web 管理使用的配置字段。"""
    return {
        key: value
        for key, value in config.items()
        if key not in WEB_ONLY_CONFIG_FIELDS
    }


def run_restart_in_background():
    """在后台执行 frpc 重启并持续更新任务状态。"""
    restart_manager = runtime_state.restart_manager
    try:
        restart_manager.update('stopping', '正在停止 frpc 服务...', 30)
        stop_success, stop_message = frpc_manager.stop()
        if not stop_success and stop_message != 'frpc 服务未运行':
            restart_manager.complete_error(f'停止服务失败：{stop_message}')
            return

        restart_manager.update('starting', '正在启动 frpc 服务...', 72)
        start_success, start_message = frpc_manager.start()
        if not start_success:
            restart_manager.complete_error(f'重启失败：{start_message}')
            return

        status = frpc_manager.get_status()
        pid = status.get('pid')
        if pid:
            restart_manager.complete_success(f'frpc 服务已重启，PID: {pid}')
        else:
            restart_manager.complete_success(start_message)
    except Exception as e:
        logger.exception(f'后台重启 frpc 服务失败: {str(e)}')
        restart_manager.complete_error('重启失败，请查看应用日志')


def cleanup_download_artifacts(*paths):
    """清理下载过程中产生的临时文件和目录。"""
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.warning(f'清理下载临时文件失败: {path}, 错误: {str(e)}')


def request_download_cancel():
    """向当前下载任务发送取消信号。"""
    success, message = runtime_state.download_manager.request_cancel()
    if success:
        broadcast_download_status()
    return success, message


def reject_unauthorized_websocket(ws, user) -> bool:
    """拒绝未登录的 WebSocket 连接。"""
    if user.is_authenticated:
        return False

    logger.warning('未登录用户尝试建立 WebSocket 连接，已拒绝')
    try:
        ws.send(json.dumps({
            'type': 'error',
            'message': '请先登录'
        }))
        ws.close()
    except Exception:
        pass
    return True

@sock.route('/ws')
def ws_handler(ws):
    """处理 WebSocket 连接"""
    if reject_unauthorized_websocket(ws, current_user):
        return

    logger.info('新的 WebSocket 连接')
    runtime_state.websocket_hub.add(ws)
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
                    'error_message': status.get('error_message', ''),
                    'frpc_version': status.get('frpc_version', '待检测'),
                    'frpc_version_hint': status.get('frpc_version_hint', ''),
                    'frps_version': status.get('frps_version', '待检测'),
                    'frps_version_hint': status.get('frps_version_hint', ''),
                    'auto_retry': status.get('auto_retry', {})
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
                        download_snapshot = get_download_snapshot()
                        ws.send(json.dumps({
                            'type': 'download_progress',
                            'message': download_snapshot['progress'],
                            'completed': download_snapshot['completed'],
                            'error': download_snapshot['error'],
                            'cancelled': download_snapshot['cancelled'],
                            'error_message': download_snapshot['error_message']
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
        runtime_state.websocket_hub.discard(ws)
        status_thread_stop.set()

def broadcast_download_status():
    """广播下载状态到所有 WebSocket 客户端"""
    download_snapshot = get_download_snapshot()
    runtime_state.websocket_hub.broadcast({
        'type': 'download_progress',
        'message': download_snapshot['progress'],
        'completed': download_snapshot['completed'],
        'error': download_snapshot['error'],
        'cancelled': download_snapshot['cancelled'],
        'error_message': download_snapshot['error_message']
    })

@bp.route('/')
@bp.route('/index')
@login_required
def index():
    return render_template('main/index.html')

# 健康检查端点（无需登录）
@bp.route('/health')
def health():
    return jsonify({'status': 'ok'}), 200

@bp.route('/save', methods=['POST'])
@login_required
def save_frpc_config():
    logger.info('收到保存 frpc.json 请求')
    try:
        runtime_settings = get_runtime_settings()
        config = request.get_json(silent=True) or {}
        logger.debug(f'保存的配置内容: {config}')

        if not isinstance(config, dict):
            return json_error('配置内容必须是 JSON 对象', 400)
        config = strip_web_only_fields(config)
        
        normalized_proxies, proxy_errors = normalize_proxies_list(config, with_enabled=False)
        if proxy_errors:
            return jsonify({
                'status': 'error',
                'message': '；'.join(proxy_errors),
                'errors': proxy_errors
            }), 400
        if normalized_proxies is not None:
            config['proxies'] = normalized_proxies

        validation_errors = InputValidator.validate_frpc_config(config)
        if validation_errors:
            return jsonify({
                'status': 'error',
                'message': '；'.join(validation_errors),
                'errors': validation_errors
            }), 400
        
        os.makedirs(runtime_settings.frpc_work_dir, exist_ok=True)
        with open(runtime_settings.frpc_config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info('frpc.json 保存成功')
        return jsonify({
            'status': 'success',
            'message': 'frpc.json 已保存'
        })
    except Exception as e:
        return log_internal_error('保存 frpc.json 失败', e, '保存 frpc.json 失败，请稍后重试')

@bp.route('/frpc.json')
@login_required
def get_config():
    try:
        runtime_settings = get_runtime_settings()
        with open(runtime_settings.frpc_config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return jsonify(config)
    except Exception as e:
        return log_internal_error('读取 frpc.json 失败', e, '读取 frpc.json 失败，请稍后重试')

@bp.route('/config.json')
@login_required
def get_config_file():
    try:
        runtime_settings = get_runtime_settings()
        if os.path.exists(runtime_settings.web_config_path):
            with open(runtime_settings.web_config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            normalized_config, proxy_errors = normalize_web_config_payload(config)
            if proxy_errors:
                return jsonify({
                    'status': 'error',
                    'message': '；'.join(proxy_errors),
                    'errors': proxy_errors
                }), 400
            return jsonify(normalized_config)
        else:
            # 如果 config.json 不存在，尝试从 frpc.json 读取并添加 enabled 字段
            if os.path.exists(runtime_settings.frpc_config_path):
                with open(runtime_settings.frpc_config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                normalized_config, proxy_errors = normalize_web_config_payload(config)
                if proxy_errors:
                    return jsonify({
                        'status': 'error',
                        'message': '；'.join(proxy_errors),
                        'errors': proxy_errors
                    }), 400
                # 保存为 config.json
                os.makedirs(runtime_settings.frpc_work_dir, exist_ok=True)
                with open(runtime_settings.web_config_path, 'w', encoding='utf-8') as f:
                    json.dump(normalized_config, f, indent=2, ensure_ascii=False)
                return jsonify(normalized_config)
            return jsonify({
                'status': 'error',
                'message': '配置文件不存在'
            }), 404
    except Exception as e:
        return log_internal_error('读取 config.json 失败', e, '读取配置文件失败，请稍后重试')

@bp.route('/save-config', methods=['POST'])
@login_required
def save_config_file():
    try:
        runtime_settings = get_runtime_settings()
        config = request.get_json(silent=True) or {}
        if not isinstance(config, dict):
            return json_error('配置内容必须是 JSON 对象', 400)

        normalized_config, proxy_errors = normalize_web_config_payload(config)
        if proxy_errors:
            return jsonify({
                'status': 'error',
                'message': '；'.join(proxy_errors),
                'errors': proxy_errors
            }), 400

        validation_errors = InputValidator.validate_frpc_config(normalized_config)
        if validation_errors:
            return jsonify({
                'status': 'error',
                'message': '；'.join(validation_errors),
                'errors': validation_errors
            }), 400
        
        os.makedirs(runtime_settings.frpc_work_dir, exist_ok=True)
        with open(runtime_settings.web_config_path, 'w', encoding='utf-8') as f:
            json.dump(normalized_config, f, indent=2, ensure_ascii=False)
        return jsonify({
            'status': 'success',
            'message': '配置已保存'
        })
    except Exception as e:
        return log_internal_error('保存 config.json 失败', e, '保存配置失败，请稍后重试')

@bp.route('/check-frpc')
@login_required
def check_frpc():
    try:
        runtime_settings = get_runtime_settings()
        frpc_path = Path(runtime_settings.frpc_binary_path)
        exists = frpc_path.exists() and frpc_path.is_file()
        return jsonify({
            'status': 'success',
            'exists': exists
        })
    except Exception as e:
        return log_internal_error('检查 frpc 文件失败', e, '检查 frpc 文件失败，请稍后重试')

def download_and_extract():
    """
    下载 frp 最新版本的 linux_amd64 发行包，并解压出 frpc 可执行文件到持久化目录。
    解压与存放路径、权限处理保持与旧逻辑一致。
    优先使用 GitHub Releases API 获取最新版本；若失败，回退到解析 releases/latest 页面。
    同时通过 WebSocket 广播简单下载进度信息。
    """
    import requests
    import tarfile
    import os
    import shutil
    import re
    from urllib.parse import urlparse

    runtime_settings = get_runtime_settings()
    work_dir = runtime_settings.frpc_work_dir
    binary_path = runtime_settings.frpc_binary_path
    download_manager = runtime_state.download_manager
    extracted_dir_path = None

    os.makedirs(work_dir, exist_ok=True)

    def set_progress(message: str, completed: bool = False, error: bool = False, cancelled: bool = False):
        """更新全局下载状态并广播"""
        try:
            download_manager.update_progress(
                message=message,
                completed=completed,
                error=error,
                cancelled=cancelled
            )
            broadcast_download_status()
        except Exception:
            # 广播异常不应影响主流程
            pass

    def ensure_not_cancelled():
        """在关键步骤检查是否已收到取消信号。"""
        download_manager.ensure_not_cancelled()

    def get_latest_linux_amd64_from_api():
        api = 'https://api.github.com/repos/fatedier/frp/releases/latest'
        headers = {
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'frpc-manager'
        }
        resp = requests.get(api, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        tag_name = data.get('tag_name')  # e.g. v0.62.1
        assets = data.get('assets', [])
        for a in assets:
            name = a.get('name', '')
            if name.endswith('linux_amd64.tar.gz'):
                return a.get('browser_download_url'), name, tag_name
        # 没有找到匹配资产
        raise RuntimeError('最新版本中未找到 linux_amd64 资源')

    def get_latest_linux_amd64_from_html():
        # 访问 releases/latest，将被重定向到 /tag/vX.Y.Z
        latest_url = 'https://github.com/fatedier/frp/releases/latest'
        resp = requests.get(latest_url, allow_redirects=True, timeout=15, headers={'User-Agent': 'frpc-manager'})
        resp.raise_for_status()
        # 取最终 URL 的 tag
        final = resp.url  # .../releases/tag/v0.62.1
        tag = final.rstrip('/').split('/')[-1]
        if not tag.startswith('v'):
            # 尝试从页面内容解析
            m = re.search(r'/releases/tag/(v\d+\.\d+\.\d+)', resp.text)
            if not m:
                raise RuntimeError('无法解析最新版本号')
            tag = m.group(1)
        # 构造资源名和下载链接
        name = f'frp_{tag.lstrip("v")}_linux_amd64.tar.gz'
        url = f'https://github.com/fatedier/frp/releases/download/{tag}/{name}'
        return url, name, tag

    try:
        try:
            url, filename, tag = get_latest_linux_amd64_from_api()
        except Exception:
            # API 失败时使用 HTML 解析回退
            url, filename, tag = get_latest_linux_amd64_from_html()

        set_progress(f'开始下载最新版本 {tag} ...')
        archive_path = os.path.join(work_dir, filename)
        extracted_dir_name = filename[:-7] if filename.endswith('.tar.gz') else filename
        extracted_dir_path = os.path.join(work_dir, extracted_dir_name)
        if os.path.isdir(extracted_dir_path):
            shutil.rmtree(extracted_dir_path)
        download_manager.set_archive_path(archive_path)
        ensure_not_cancelled()
        # 2) 下载文件
        with requests.get(url, stream=True, timeout=60, headers={'User-Agent': 'frpc-manager'}) as response:
            response.raise_for_status()
            total = response.headers.get('Content-Length')
            total = int(total) if total else None
            downloaded = 0
            with open(archive_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    ensure_not_cancelled()
                    if chunk:
                        file.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            percent = downloaded * 100.0 / total
                            set_progress(f'下载进度: {percent:.1f}%')
        ensure_not_cancelled()
        set_progress('下载完成，开始解压...')
        # 3) 解压（安全校验，防路径穿越）
        with tarfile.open(archive_path, 'r:gz') as tar:
            def is_within_directory(directory: str, target: str) -> bool:
                abs_directory = os.path.abspath(directory)
                abs_target = os.path.abspath(target)
                return (abs_target + os.sep).startswith(abs_directory + os.sep)
            for member in tar.getmembers():
                ensure_not_cancelled()
                member_path = os.path.join(work_dir, member.name)
                if not is_within_directory(work_dir, member_path):
                    set_progress('检测到压缩包包含非法路径，已终止', completed=True, error=True)
                    return False, '压缩包包含非法路径'
                tar.extract(member, path=work_dir)
        # 4) 查找解压目录
        ensure_not_cancelled()
        extracted_dir = extracted_dir_path if os.path.isdir(extracted_dir_path) else None
        if not extracted_dir:
            set_progress('未找到解压目录', completed=True, error=True)
            return False, '未找到解压目录'
        frpc_path = os.path.join(extracted_dir, 'frpc')
        if not os.path.exists(frpc_path):
            set_progress('未找到 frpc 文件', completed=True, error=True)
            return False, '未找到 frpc 文件'
        # 5) 移动 frpc 到持久化目录（覆盖旧文件）
        ensure_not_cancelled()
        if os.path.exists(binary_path):
            try:
                os.remove(binary_path)
            except Exception:
                # 可能被占用，尝试重命名备份
                try:
                    os.rename(binary_path, f'{binary_path}.bak')
                except Exception:
                    pass
        shutil.move(frpc_path, binary_path)
        # 6) 清理解压目录和压缩包
        try:
            shutil.rmtree(extracted_dir)
        finally:
            if os.path.exists(archive_path):
                os.remove(archive_path)
            download_manager.clear_archive_path()
        # 7) 设置可执行权限（非Windows）
        if os.name != 'nt':
            os.chmod(binary_path, 0o755)
        set_progress(f'已下载最新版本 {tag} 并解压完成。', completed=True)
        return True, f'已下载最新版本 {tag}，并解压完成。frpc 已保存到持久化目录。'
    except DownloadCancelledError as e:
        cleanup_download_artifacts(download_manager.get_archive_path(), extracted_dir_path)
        download_manager.clear_archive_path()
        set_progress(str(e), completed=True, cancelled=True)
        return False, str(e)
    except Exception as e:
        cleanup_download_artifacts(download_manager.get_archive_path(), extracted_dir_path)
        logger.exception(f'下载或解压失败: {str(e)}')
        set_progress('下载或解压失败，请检查网络连接或稍后重试', completed=True, error=True)
        download_manager.clear_archive_path()
        return False, '下载或解压失败，请检查网络连接或稍后重试'

@bp.route('/download-frpc', methods=['POST'])
@login_required
def download_frpc():
    try:
        download_manager = runtime_state.download_manager
        if not download_manager.can_start():
            return json_error('已有下载任务正在进行', 400)

        def run_download():
            try:
                download_and_extract()
            finally:
                download_manager.finish_thread()

        download_manager.start(run_download)
        return jsonify({'status': 'accepted', 'message': '下载任务已开始'}), 202
    except Exception as e:
        return log_internal_error('启动下载任务失败', e, '启动下载任务失败，请稍后重试')

@bp.route('/stop-download', methods=['POST'])
@login_required
def stop_download():
    success, message = request_download_cancel()
    if success:
        return jsonify({'status': 'success', 'message': message})
    return json_error(message, 400)

@bp.route('/cancel-download', methods=['POST'])
@login_required
def cancel_download():
    logger.info('收到取消下载请求')
    success, message = request_download_cancel()
    if success:
        return jsonify({
            'status': 'success',
            'message': message
        })
    return json_error(message, 400)

@bp.route('/download-progress')
@login_required
def download_progress():
    try:
        download_snapshot = get_download_snapshot()
        return jsonify({
            'status': 'success',
            'message': download_snapshot['progress'],
            'completed': download_snapshot['completed'],
            'error': download_snapshot['error'],
            'cancelled': download_snapshot['cancelled'],
            'error_message': download_snapshot['error_message']
        })
    except Exception as e:
        return log_internal_error('读取下载进度失败', e, '读取下载进度失败，请稍后重试')

@bp.route('/frpc/status')
@login_required
def frpc_status():
    """获取 frpc 服务状态"""
    try:
        status = frpc_manager.get_status()
        return jsonify(status)
    except Exception as e:
        return log_internal_error('获取 frpc 状态失败', e, '获取 frpc 状态失败，请稍后重试')

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
        return log_internal_error('启动 frpc 服务失败', e, '启动服务失败，请稍后重试')

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
        return log_internal_error('停止 frpc 服务失败', e, '停止服务失败，请稍后重试')

@bp.route('/frpc/restart', methods=['POST'])
@login_required
def frpc_restart():
    """重启 frpc 服务"""
    try:
        restart_manager = runtime_state.restart_manager
        if not restart_manager.can_start():
            snapshot = get_restart_snapshot()
            return jsonify({
                'success': False,
                'message': snapshot.get('message') or '已有重启任务正在进行',
                'restart': snapshot
            }), 409

        snapshot = restart_manager.start(run_restart_in_background, initial_delay=1.0)
        return jsonify({
            'success': True,
            'accepted': True,
            'message': '已提交重启请求，正在后台执行',
            'restart': snapshot
        }), 202
    except Exception as e:
        return log_internal_error('重启 frpc 服务失败', e, '重启服务失败，请稍后重试')


@bp.route('/frpc/restart-status')
@login_required
def frpc_restart_status():
    """获取 frpc 重启任务状态。"""
    try:
        snapshot = get_restart_snapshot()
        service_status = frpc_manager.get_status()
        return jsonify({
            **snapshot,
            'service_status': service_status
        })
    except Exception as e:
        return log_internal_error('获取 frpc 重启状态失败', e, '获取重启状态失败，请稍后重试')

@bp.route('/frpc/logs')
@login_required
def frpc_logs():
    """获取 frpc 日志"""
    try:
        logs = frpc_manager.get_logs()
        # 将日志放入队列以广播给所有客户端
        runtime_state.enqueue_logs(logs)
        return jsonify({'logs': logs})
    except Exception as e:
        return log_internal_error('获取 frpc 日志失败', e, '获取日志失败，请稍后重试')

@bp.route('/delete-frpc-config', methods=['POST'])
@login_required
def delete_frpc_config():
    try:
        runtime_settings = get_runtime_settings()
        if os.path.exists(runtime_settings.frpc_config_path):
            os.remove(runtime_settings.frpc_config_path)
        return jsonify({
            'status': 'success',
            'message': 'frpc.json 已删除'
        })
    except Exception as e:
        return log_internal_error('删除 frpc.json 失败', e, '删除 frpc.json 失败，请稍后重试')
