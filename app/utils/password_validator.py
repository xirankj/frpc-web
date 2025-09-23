"""
密码验证工具
"""
import re
from typing import List, Tuple


class PasswordValidator:
    """密码强度验证器"""
    
    def __init__(self):
        self.min_length = 8
        self.max_length = 128
    
    def validate(self, password: str) -> Tuple[bool, List[str]]:
        """
        验证密码强度
        
        Args:
            password: 待验证的密码
            
        Returns:
            Tuple[bool, List[str]]: (是否有效, 错误信息列表)
        """
        errors = []
        
        # 检查长度
        if len(password) < self.min_length:
            errors.append(f"密码长度不能少于{self.min_length}位")
        
        if len(password) > self.max_length:
            errors.append(f"密码长度不能超过{self.max_length}位")
        
        # 检查是否包含数字
        if not re.search(r'\d', password):
            errors.append("密码必须包含至少一个数字")
        
        # 检查是否包含小写字母
        if not re.search(r'[a-z]', password):
            errors.append("密码必须包含至少一个小写字母")
        
        # 检查是否包含大写字母
        if not re.search(r'[A-Z]', password):
            errors.append("密码必须包含至少一个大写字母")
        
        # 检查是否包含特殊字符
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            errors.append("密码必须包含至少一个特殊字符 (!@#$%^&*(),.?\":{}|<>)")
        
        # 检查常见弱密码
        common_passwords = [
            '123456', 'password', 'admin', '12345678', 'qwerty',
            '123456789', '12345', '1234', '111111', '1234567',
            'dragon', '123123', 'baseball', 'abc123', 'football'
        ]
        
        if password.lower() in common_passwords:
            errors.append("不能使用常见弱密码")
        
        # 检查是否包含用户名（如果提供）
        # 这里可以扩展为检查用户名
        
        return len(errors) == 0, errors
    
    def generate_password_requirements_text(self) -> str:
        """生成密码要求说明"""
        return (
            f"密码要求：\n"
            f"- 长度：{self.min_length}-{self.max_length}位\n"
            f"- 必须包含大写字母、小写字母、数字和特殊字符\n"
            f"- 不能使用常见弱密码"
        )