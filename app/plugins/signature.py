"""
Plugin signature verification system.
插件签名验证系统，确保插件来源可信。
"""

import os
import json
import hashlib
import base64
from typing import Optional, Dict, Any, Tuple
from pathlib import Path
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidSignature

from app.core.structured_logging import get_logger

logger = get_logger("plugin.signature")


class PluginSignatureVerifier:
    """
    插件签名验证器
    
    使用RSA公钥验证插件签名，确保插件代码未被篡改。
    """
    
    def __init__(self, trusted_keys_dir: str = "trusted_keys"):
        self.trusted_keys_dir = Path(trusted_keys_dir)
        self.trusted_keys: Dict[str, Any] = {}
        self._load_trusted_keys()
    
    def _load_trusted_keys(self):
        """加载受信任的公钥"""
        if not self.trusted_keys_dir.exists():
            logger.warning(f"Trusted keys directory not found: {self.trusted_keys_dir}")
            return
        
        for key_file in self.trusted_keys_dir.glob("*.pem"):
            try:
                with open(key_file, "rb") as f:
                    public_key = serialization.load_pem_public_key(
                        f.read(),
                        backend=default_backend()
                    )
                key_name = key_file.stem
                self.trusted_keys[key_name] = public_key
                logger.info(f"Loaded trusted key: {key_name}")
            except Exception as e:
                logger.error(f"Failed to load key {key_file}: {e}")
    
    def verify_plugin(self, plugin_path: Path) -> Tuple[bool, Optional[str]]:
        """
        验证插件签名
        
        Args:
            plugin_path: 插件目录路径
            
        Returns:
            (验证是否成功, 错误信息)
        """
        # 查找签名文件
        signature_file = plugin_path / "plugin.sig"
        manifest_file = plugin_path / "plugin.manifest"
        
        if not signature_file.exists():
            return False, "Signature file not found"
        
        if not manifest_file.exists():
            return False, "Manifest file not found"
        
        try:
            # 读取清单文件
            with open(manifest_file, "r") as f:
                manifest = json.load(f)
            
            # 读取签名
            with open(signature_file, "rb") as f:
                signature_data = json.load(f)
            
            signer_id = signature_data.get("signer_id")
            signature_b64 = signature_data.get("signature")
            
            if not signer_id or not signature_b64:
                return False, "Invalid signature file format"
            
            # 获取对应的公钥
            public_key = self.trusted_keys.get(signer_id)
            if not public_key:
                return False, f"Unknown signer: {signer_id}"
            
            # 计算清单的哈希
            manifest_hash = self._calculate_manifest_hash(manifest, plugin_path)
            
            # 验证签名
            signature = base64.b64decode(signature_b64)
            try:
                public_key.verify(
                    signature,
                    manifest_hash,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH
                    ),
                    hashes.SHA256()
                )
                logger.info(f"Plugin signature verified: {plugin_path.name} (signed by {signer_id})")
                return True, None
            except InvalidSignature:
                return False, "Invalid signature"
            
        except Exception as e:
            logger.error(f"Error verifying plugin: {e}")
            return False, str(e)
    
    def _calculate_manifest_hash(self, manifest: Dict[str, Any], plugin_path: Path) -> bytes:
        """
        计算插件清单的哈希值
        
        清单包含：
        - 插件元数据
        - 文件列表及其哈希
        """
        hasher = hashlib.sha256()
        
        # 哈希元数据
        metadata = manifest.get("metadata", {})
        metadata_str = json.dumps(metadata, sort_keys=True)
        hasher.update(metadata_str.encode())
        
        # 验证并哈希文件列表
        files = manifest.get("files", {})
        for file_path, expected_hash in sorted(files.items()):
            full_path = plugin_path / file_path
            
            if not full_path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            
            # 计算文件哈希
            file_hash = self._calculate_file_hash(full_path)
            if file_hash != expected_hash:
                raise ValueError(f"File hash mismatch: {file_path}")
            
            # 添加到总哈希
            hasher.update(f"{file_path}:{file_hash}".encode())
        
        return hasher.digest()
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件的SHA256哈希"""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def is_plugin_trusted(self, plugin_name: str) -> bool:
        """
        检查插件是否受信任（已验证签名）
        
        这个方法可以缓存验证结果以提高性能
        """
        # TODO: 实现验证结果缓存
        plugin_path = Path("plugins") / plugin_name
        if not plugin_path.exists():
            return False
        
        verified, _ = self.verify_plugin(plugin_path)
        return verified


class PluginSigner:
    """
    插件签名器（供插件开发者使用）
    """
    
    def __init__(self, private_key_path: str, signer_id: str):
        self.signer_id = signer_id
        with open(private_key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,
                backend=default_backend()
            )
    
    def sign_plugin(self, plugin_path: Path) -> None:
        """
        为插件生成签名
        
        Args:
            plugin_path: 插件目录路径
        """
        # 生成清单
        manifest = self._generate_manifest(plugin_path)
        
        # 保存清单
        manifest_file = plugin_path / "plugin.manifest"
        with open(manifest_file, "w") as f:
            json.dump(manifest, f, indent=2)
        
        # 计算清单哈希
        manifest_hash = self._calculate_manifest_hash(manifest, plugin_path)
        
        # 生成签名
        signature = self.private_key.sign(
            manifest_hash,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        
        # 保存签名
        signature_data = {
            "signer_id": self.signer_id,
            "signature": base64.b64encode(signature).decode(),
            "algorithm": "RSA-PSS-SHA256"
        }
        
        signature_file = plugin_path / "plugin.sig"
        with open(signature_file, "w") as f:
            json.dump(signature_data, f, indent=2)
        
        logger.info(f"Plugin signed: {plugin_path.name}")
    
    def _generate_manifest(self, plugin_path: Path) -> Dict[str, Any]:
        """生成插件清单"""
        manifest = {
            "metadata": {},
            "files": {}
        }
        
        # 读取插件元数据
        plugin_py = plugin_path / "plugin.py"
        if plugin_py.exists():
            # TODO: 动态提取元数据
            manifest["metadata"] = {
                "name": plugin_path.name,
                "version": "1.0.0"
            }
        
        # 计算所有Python文件的哈希
        for py_file in plugin_path.glob("**/*.py"):
            relative_path = py_file.relative_to(plugin_path)
            file_hash = self._calculate_file_hash(py_file)
            manifest["files"][str(relative_path)] = file_hash
        
        return manifest
    
    def _calculate_manifest_hash(self, manifest: Dict[str, Any], plugin_path: Path) -> bytes:
        """计算插件清单的哈希值"""
        hasher = hashlib.sha256()
        
        # 哈希元数据
        metadata = manifest.get("metadata", {})
        metadata_str = json.dumps(metadata, sort_keys=True)
        hasher.update(metadata_str.encode())
        
        # 哈希文件列表
        files = manifest.get("files", {})
        for file_path, file_hash in sorted(files.items()):
            hasher.update(f"{file_path}:{file_hash}".encode())
        
        return hasher.digest()
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件的SHA256哈希"""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()


def generate_key_pair(key_name: str, key_dir: str = "keys") -> None:
    """
    生成RSA密钥对
    
    Args:
        key_name: 密钥名称
        key_dir: 密钥保存目录
    """
    key_dir_path = Path(key_dir)
    key_dir_path.mkdir(exist_ok=True)
    
    # 生成私钥
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    
    # 保存私钥
    private_key_path = key_dir_path / f"{key_name}_private.pem"
    with open(private_key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            )
        )
    
    # 保存公钥
    public_key = private_key.public_key()
    public_key_path = key_dir_path / f"{key_name}_public.pem"
    with open(public_key_path, "wb") as f:
        f.write(
            public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
    
    logger.info(f"Generated key pair: {key_name}")
    logger.info(f"Private key: {private_key_path}")
    logger.info(f"Public key: {public_key_path}")


# 集成到插件加载器
def integrate_with_loader():
    """
    将签名验证集成到插件加载器
    
    在 loader.py 中调用：
    1. 初始化时创建 PluginSignatureVerifier
    2. 加载插件前调用 verify_plugin
    3. 根据配置决定是否强制验证签名
    """
    pass