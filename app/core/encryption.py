"""
加密工具模块 - 用于安全存储敏感数据如凭证信息
"""

import os
import json
import base64
from typing import Dict, Any, Optional
from cryptography.fernet import Fernet
from cryptography.fernet import MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import get_settings
from app.core.structured_logging import get_logger

logger = get_logger("encryption")


class CredentialEncryption:
    """凭证加密工具类"""
    
    def __init__(self):
        self._fernet = None
        self._initialize_encryption()
    
    def _initialize_encryption(self):
        """初始化加密器"""
        try:
            # 从配置或环境变量获取加密密钥
            settings = get_settings()
            
            # 尝试从环境变量获取主/旧加密密钥
            primary_key_env = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
            previous_key_env = os.getenv("CREDENTIAL_ENCRYPTION_KEY_PREV")

            fernets = []

            def _to_key_bytes(k: str) -> bytes:
                kb = k.encode() if isinstance(k, str) else k
                # 验证Fernet key 长度
                decoded = base64.urlsafe_b64decode(kb)
                if len(decoded) != 32:
                    raise ValueError("Invalid encryption key length")
                return kb

            if primary_key_env:
                try:
                    fernets.append(Fernet(_to_key_bytes(primary_key_env)))
                except Exception:
                    raise ValueError("Invalid CREDENTIAL_ENCRYPTION_KEY format. Must be a valid Fernet key.")
                if previous_key_env:
                    try:
                        fernets.append(Fernet(_to_key_bytes(previous_key_env)))
                    except Exception:
                        logger.warning("CREDENTIAL_ENCRYPTION_KEY_PREV invalid; rotation fallback disabled")
            else:
                # 无独立密钥时，回退到基于JWT密钥派生（不推荐，仅用于开发场景）
                secret_key = settings.security.secret_key
                salt = b"ai_gateway_credential_salt"
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                derived = base64.urlsafe_b64encode(kdf.derive(secret_key.encode()))
                fernets.append(Fernet(derived))
                logger.warning("Using derived credential key from SECRET_KEY. Set CREDENTIAL_ENCRYPTION_KEY for production and rotation support.")

            # MultiFernet：加密使用第一个，解密尝试全部（支持轮换）
            self._fernet = MultiFernet(fernets) if len(fernets) > 1 else fernets[0]
            logger.info("Credential encryption initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize credential encryption: {e}")
            raise RuntimeError(f"Encryption initialization failed: {e}")
    
    def encrypt_credential_data(self, data: Dict[str, Any]) -> str:
        """
        加密凭证数据
        
        Args:
            data: 要加密的凭证数据字典
            
        Returns:
            加密后的base64编码字符串
            
        Raises:
            RuntimeError: 如果加密失败
        """
        try:
            # 将字典转换为JSON字符串
            json_data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
            
            # 加密数据
            encrypted_data = self._fernet.encrypt(json_data.encode('utf-8'))
            
            # 返回base64编码的字符串
            return base64.urlsafe_b64encode(encrypted_data).decode('ascii')
            
        except Exception as e:
            logger.error(f"Failed to encrypt credential data: {e}")
            raise RuntimeError(f"Encryption failed: {e}")
    
    def decrypt_credential_data(self, encrypted_data: str) -> Dict[str, Any]:
        """
        解密凭证数据
        
        Args:
            encrypted_data: 加密的base64编码字符串
            
        Returns:
            解密后的凭证数据字典
            
        Raises:
            RuntimeError: 如果解密失败
        """
        try:
            # 解码base64
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode('ascii'))
            
            # 解密数据
            decrypted_data = self._fernet.decrypt(encrypted_bytes)
            
            # 将JSON字符串转换回字典
            return json.loads(decrypted_data.decode('utf-8'))
            
        except Exception as e:
            logger.error(f"Failed to decrypt credential data: {e}")
            raise RuntimeError(f"Decryption failed: {e}")
    
    def is_encrypted(self, data: str) -> bool:
        """
        检查数据是否已加密
        
        Args:
            data: 要检查的数据字符串
            
        Returns:
            True如果数据已加密，False如果是明文
        """
        try:
            # 尝试解密，如果成功则说明已加密
            self.decrypt_credential_data(data)
            return True
        except:
            # 如果解密失败，检查是否是有效的JSON（明文）
            try:
                json.loads(data)
                return False  # 是有效JSON，但未加密
            except:
                # 既不是加密数据也不是有效JSON，可能是其他格式的明文
                return False
    
    def migrate_plaintext_credential(self, plaintext_data: str) -> str:
        """
        迁移明文凭证到加密格式
        
        Args:
            plaintext_data: 明文凭证数据（JSON字符串）
            
        Returns:
            加密后的凭证数据
            
        Raises:
            RuntimeError: 如果迁移失败
        """
        try:
            # 解析明文JSON
            data_dict = json.loads(plaintext_data)
            
            # 加密数据
            return self.encrypt_credential_data(data_dict)
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in plaintext credential: {e}")
            raise RuntimeError(f"Invalid credential format: {e}")
        except Exception as e:
            logger.error(f"Failed to migrate plaintext credential: {e}")
            raise RuntimeError(f"Migration failed: {e}")


# 全局加密器实例
_encryption_instance: Optional[CredentialEncryption] = None


def get_credential_encryption() -> CredentialEncryption:
    """获取全局凭证加密器实例"""
    global _encryption_instance
    if _encryption_instance is None:
        _encryption_instance = CredentialEncryption()
    return _encryption_instance


def encrypt_credential(data: Dict[str, Any]) -> str:
    """便捷函数：加密凭证数据"""
    return get_credential_encryption().encrypt_credential_data(data)


def decrypt_credential(encrypted_data: str) -> Dict[str, Any]:
    """便捷函数：解密凭证数据"""
    return get_credential_encryption().decrypt_credential_data(encrypted_data)


def migrate_plaintext_credential(plaintext_data: str) -> str:
    """便捷函数：迁移明文凭证"""
    return get_credential_encryption().migrate_plaintext_credential(plaintext_data) 