"""
统一的配置验证器
"""

import re
from typing import List
from urllib.parse import urlparse


class ConfigValidator:
    """统一的配置验证器"""
    
    @staticmethod
    def validate_database_url(url: str) -> str:
        """验证数据库URL格式"""
        if not url or url.strip() == "":
            raise ValueError("Database URL cannot be empty")
        
        # 支持的数据库协议
        valid_schemes = [
            'postgresql+asyncpg://',
            'sqlite+aiosqlite://', 
            'mysql+aiomysql://',
        ]
        
        if not any(url.startswith(scheme) for scheme in valid_schemes):
            raise ValueError(
                f"Database URL must start with one of: {valid_schemes}"
            )
        
        # 基本URL格式验证
        try:
            parsed = urlparse(url)
            if not parsed.scheme:
                raise ValueError("Invalid database URL format")
        except Exception as e:
            raise ValueError(f"Invalid database URL: {e}")
        
        return url
    
    @staticmethod
    def validate_redis_url(url: str) -> str:
        """验证Redis URL格式"""
        if not url or url.strip() == "":
            raise ValueError("Redis URL cannot be empty")
        
        if not url.startswith('redis://'):
            raise ValueError("Redis URL must start with 'redis://'")
        
        try:
            parsed = urlparse(url)
            if not parsed.hostname:
                raise ValueError("Redis URL must contain hostname")
        except Exception as e:
            raise ValueError(f"Invalid Redis URL: {e}")
        
        return url
    
    @staticmethod
    def validate_secret_key(key: str) -> str:
        """验证密钥强度"""
        if not key or len(key.strip()) < 8:
            raise ValueError("Secret key must be at least 8 characters long")
        
        # 生产环境建议更强的密钥
        if len(key) < 32:
            import warnings
            warnings.warn(
                "Secret key should be at least 32 characters for production use",
                UserWarning
            )
        
        return key
    
    @staticmethod
    def validate_cors_origins(origins: List[str]) -> List[str]:
        """验证CORS源列表"""
        if not isinstance(origins, list):
            raise ValueError("CORS origins must be a list")
        
        validated_origins = []
        for origin in origins:
            if origin == "*":
                validated_origins.append(origin)
            elif origin.startswith(("http://", "https://")):
                validated_origins.append(origin)
            else:
                raise ValueError(f"Invalid CORS origin format: {origin}")
        
        return validated_origins
    
    @staticmethod
    def validate_host_port(host: str, port: int) -> tuple[str, int]:
        """验证主机和端口"""
        # 验证主机
        if not host:
            raise ValueError("Host cannot be empty")
        
        # 简单的主机名验证
        if not re.match(r'^[a-zA-Z0-9.-]+$', host) and host != "0.0.0.0":
            raise ValueError("Invalid host format")
        
        # 验证端口
        if not isinstance(port, int) or port < 1 or port > 65535:
            raise ValueError("Port must be an integer between 1 and 65535")
        
        return host, port
    
    @staticmethod
    def validate_log_level(level: str) -> str:
        """验证日志级别"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        level_upper = level.upper()
        
        if level_upper not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        
        return level_upper.lower()
    
    @staticmethod
    def validate_pool_size(pool_size: int, max_overflow: int) -> tuple[int, int]:
        """验证连接池配置"""
        if not isinstance(pool_size, int) or pool_size < 1:
            raise ValueError("Pool size must be a positive integer")
        
        if not isinstance(max_overflow, int) or max_overflow < 0:
            raise ValueError("Max overflow must be a non-negative integer")
        
        if max_overflow > pool_size * 3:
            import warnings
            warnings.warn(
                "Max overflow is very large compared to pool size",
                UserWarning
            )
        
        return pool_size, max_overflow
    
    @staticmethod
    def validate_plugin_list(plugins: List[str]) -> List[str]:
        """验证插件列表"""
        if not isinstance(plugins, list):
            raise ValueError("Plugins must be a list")
        
        validated_plugins = []
        for plugin in plugins:
            if not isinstance(plugin, str):
                raise ValueError("Plugin names must be strings")
            
            # 简单的插件名验证
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', plugin):
                raise ValueError(f"Invalid plugin name format: {plugin}")
            
            validated_plugins.append(plugin)
        
        return validated_plugins 