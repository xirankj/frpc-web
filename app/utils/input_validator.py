"""
输入验证工具
"""
import json
import re
from typing import Any, Dict, List, Optional
from flask import request


class InputValidator:
    """输入验证器"""
    
    @staticmethod
    def validate_json_request(required_fields: List[str], optional_fields: List[str] = None) -> Dict[str, Any]:
        """
        验证 JSON 请求数据
        
        Args:
            required_fields: 必需字段列表
            optional_fields: 可选字段列表
            
        Returns:
            Dict[str, Any]: 验证后的数据
            
        Raises:
            ValueError: 验证失败时抛出异常
        """
        if not request.is_json:
            raise ValueError("请求必须是 JSON 格式")
        
        try:
            data = request.get_json()
        except Exception as e:
            raise ValueError(f"JSON 格式错误: {str(e)}")
        
        if not isinstance(data, dict):
            raise ValueError("请求数据必须是 JSON 对象")
        
        # 检查必需字段
        missing_fields = []
        for field in required_fields:
            if field not in data or data[field] is None or data[field] == "":
                missing_fields.append(field)
        
        if missing_fields:
            raise ValueError(f"缺少必需字段: {', '.join(missing_fields)}")
        
        # 过滤有效字段
        valid_fields = set(required_fields)
        if optional_fields:
            valid_fields.update(optional_fields)
        
        validated_data = {k: v for k, v in data.items() if k in valid_fields}
        
        return validated_data
    
    @staticmethod
    def validate_username(username: str) -> bool:
        """
        验证用户名格式
        
        Args:
            username: 用户名
            
        Returns:
            bool: 是否有效
        """
        if not username or not isinstance(username, str):
            return False
        
        # 用户名长度 3-20 位，只能包含字母、数字、下划线
        pattern = r'^[a-zA-Z0-9_]{3,20}$'
        return bool(re.match(pattern, username))
    
    @staticmethod
    def validate_ip_address(ip: str) -> bool:
        """
        验证 IP 地址格式
        
        Args:
            ip: IP 地址
            
        Returns:
            bool: 是否有效
        """
        if not ip or not isinstance(ip, str):
            return False
        
        # IPv4 地址验证
        pattern = r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
        return bool(re.match(pattern, ip))
    
    @staticmethod
    def validate_port(port: Any) -> bool:
        """
        验证端口号
        
        Args:
            port: 端口号
            
        Returns:
            bool: 是否有效
        """
        try:
            port_int = int(port)
            return 1 <= port_int <= 65535
        except (ValueError, TypeError):
            return False
    
    @staticmethod
    def validate_frpc_config(config: Dict[str, Any]) -> List[str]:
        """
        验证 FRPC 配置
        
        Args:
            config: FRPC 配置字典
            
        Returns:
            List[str]: 错误信息列表
        """
        errors = []
        
        # 验证服务器地址
        if 'serverAddr' not in config:
            errors.append("缺少服务器地址 (serverAddr)")
        elif not InputValidator.validate_ip_address(config['serverAddr']):
            errors.append("服务器地址格式无效")
        
        # 验证服务器端口
        if 'serverPort' not in config:
            errors.append("缺少服务器端口 (serverPort)")
        elif not InputValidator.validate_port(config['serverPort']):
            errors.append("服务器端口无效 (1-65535)")
        
        # 验证认证配置
        if 'auth' in config:
            auth = config['auth']
            if not isinstance(auth, dict):
                errors.append("认证配置必须是对象")
            else:
                if 'method' not in auth:
                    errors.append("缺少认证方法")
                elif auth['method'] not in ['token', 'oidc']:
                    errors.append("认证方法无效，支持: token, oidc")
                
                if auth.get('method') == 'token' and not auth.get('token'):
                    errors.append("使用 token 认证时必须提供 token")
        
        # 验证代理配置
        if 'proxies' in config and isinstance(config['proxies'], list):
            for i, proxy in enumerate(config['proxies']):
                if not isinstance(proxy, dict):
                    errors.append(f"代理配置 {i+1} 必须是对象")
                    continue
                
                # 验证必需字段
                required_proxy_fields = ['name', 'type', 'localIP', 'localPort', 'remotePort']
                for field in required_proxy_fields:
                    if field not in proxy:
                        errors.append(f"代理配置 {i+1} 缺少字段: {field}")
                
                # 验证代理类型
                if 'type' in proxy and proxy['type'] not in ['tcp', 'udp', 'http', 'https']:
                    errors.append(f"代理配置 {i+1} 类型无效: {proxy['type']}")
                
                # 验证端口
                for port_field in ['localPort', 'remotePort']:
                    if port_field in proxy and not InputValidator.validate_port(proxy[port_field]):
                        errors.append(f"代理配置 {i+1} {port_field} 无效")
                
                # 验证本地 IP
                if 'localIP' in proxy and not InputValidator.validate_ip_address(proxy['localIP']):
                    errors.append(f"代理配置 {i+1} 本地IP地址无效")
        
        return errors
    
    @staticmethod
    def sanitize_string(value: str, max_length: int = 255) -> str:
        """
        清理字符串输入
        
        Args:
            value: 输入字符串
            max_length: 最大长度
            
        Returns:
            str: 清理后的字符串
        """
        if not isinstance(value, str):
            return ""
        
        # 移除首尾空白字符
        value = value.strip()
        
        # 限制长度
        if len(value) > max_length:
            value = value[:max_length]
        
        return value