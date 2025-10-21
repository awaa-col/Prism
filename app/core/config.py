"""
Configuration management module using Pydantic Settings.
Supports environment variables and YAML configuration files.
"""

import os
from typing import List, Dict, Any, Optional
from functools import lru_cache
import yaml

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    """Server configuration"""
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = 4
    log_level: str = "info"
    cors: Dict[str, Any] = {
        "allow_origins": ["*"],
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "allow_credentials": True
    }


class DatabaseConfig(BaseModel):
    """Database configuration"""
    url: str
    pool_size: int = 20
    max_overflow: int = 40
    echo: bool = False
    
    @field_validator('url', mode='before')
    def validate_url(cls, v):
        """Validate database URL format"""
        from app.core.validators import ConfigValidator
        return ConfigValidator.validate_database_url(v)


class RedisConfig(BaseModel):
    """Redis configuration"""
    url: str
    pool_size: int = 10
    decode_responses: bool = True
    enabled: bool = True


class CacheConfig(BaseModel):
    """Cache configuration"""
    backend: str = "memory"  # memory | redis
    memory_max_size: int = 10000
    memory_ttl_seconds: int = 300


class SecurityConfig(BaseModel):
    """Security configuration"""
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    trusted_plugins: List[str] = []
    
    @field_validator('secret_key', mode='before')
    def validate_secret_key(cls, v):
        """Validate secret key - enhanced for production safety"""
        from app.core.validators import ConfigValidator
        
        # Check for weak/development keys
        weak_keys = [
            "dev-secret-key-change-in-production",
            "change-me",
            "secret",
            "changeme",
            "your-secret-key-here"
        ]
        
        if v and v.lower() in [k.lower() for k in weak_keys]:
            raise ValueError(
                f"Weak SECRET_KEY detected: '{v}'\n"
                "Never use default/example values in production!\n"
                "Generate a strong key with: openssl rand -hex 32"
            )
        
        # Validate using existing validator
        return ConfigValidator.validate_secret_key(v)


class PluginConfig(BaseModel):
    """Plugin configuration"""
    enabled: List[str] = []
    auto_load: bool = True
    directory: str = "plugins"
    # 环境隔离：是否为每个插件创建独立 venv
    isolated_env: bool = False
    # 热重载相关
    hot_reload: bool = False
    hot_reload_interval: float = 2.0
    # 签名校验
    verify_signatures: bool = False
    trusted_keys_dir: str = "trusted_keys"


class SandboxConfig(BaseModel):
    """Sandbox configuration"""
    enabled: bool = True
    timeout: int = 30
    memory_limit: str = "512M"
    cpu_time_limit: int = 300  # CPU time limit in seconds (5 minutes)


class RateLimitConfig(BaseModel):
    """Rate limiting configuration"""
    default_limit: int = 100
    default_period: int = 60


class MonitoringConfig(BaseModel):
    """Monitoring configuration"""
    telemetry_enabled: bool = True
    otlp_endpoint: str = "http://localhost:4317"


class RoutesConfig(BaseModel):
    """Routes and plugin chain configuration"""
    routes: Dict[str, Dict[str, List[Dict[str, str]]]] = Field(default_factory=dict)
    
    def get_chain_for_route(self, route: str) -> List[str]:
        """Get plugin chain for a specific route"""
        route_config = self.routes.get(route, {})
        chain_config = route_config.get("chain", [])
        
        # Extract plugin names from chain configuration
        plugins = []
        for plugin_def in chain_config:
            if isinstance(plugin_def, dict) and "plugin" in plugin_def:
                plugins.append(plugin_def["plugin"])
            elif isinstance(plugin_def, str):
                plugins.append(plugin_def)
        
        return plugins


class Settings(BaseSettings):
    """Main application settings"""
    
    # Application info
    app_name: str = "Prism Framework"
    app_version: str = "2.0.0"
    debug: bool = False
    
    # Sub-configurations
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig
    redis: RedisConfig
    cache: CacheConfig = CacheConfig()
    security: SecurityConfig
    plugins: PluginConfig = PluginConfig()
    sandbox: SandboxConfig = SandboxConfig()
    rate_limiting: RateLimitConfig = RateLimitConfig()
    monitoring: MonitoringConfig = MonitoringConfig()
    routes: RoutesConfig = RoutesConfig()
    
    # Admin credentials (for initial setup)
    admin_username: str = "admin"
    admin_password: str = "ChangeMe"
    
    # 添加缓存后端属性（方便访问）
    @property
    def cache_backend(self) -> str:
        return self.cache.backend
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow",
        env_nested_delimiter="__",
    )
    
    @classmethod
    def from_yaml(cls, yaml_file: str = "config.yml") -> "Settings":
        """Load settings from YAML file with environment variable substitution"""
        if not os.path.exists(yaml_file):
            return cls()
        
        with open(yaml_file, "r", encoding="utf-8") as f:
            yaml_content = f.read()
            
        # Replace environment variables in YAML
        import re
        pattern = r'\$\{([^}:]+)(?::([^}]+))?\}'
        
        def replace_env_var(match):
            var_name = match.group(1)
            default_value = match.group(2)
            value = os.environ.get(var_name, default_value or "")
            # If still empty and it's a required field, provide sensible defaults
            if not value:
                if var_name == "DATABASE_URL":
                    value = "sqlite+aiosqlite:///./ai_gateway.db"
                elif var_name == "REDIS_URL":
                    value = "redis://localhost:6379/0"
                elif var_name == "SECRET_KEY":
                    value = "change-me"
            return value
        
        yaml_content = re.sub(pattern, replace_env_var, yaml_content)
        config_dict = yaml.safe_load(yaml_content)
        
        # Flatten the configuration
        flat_config = {}
        for key, value in config_dict.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    flat_config[f"{key}_{sub_key}"] = sub_value
            else:
                flat_config[key] = value
        
        # Create sub-configurations
        if "server" in config_dict:
            flat_config["server"] = ServerConfig(**config_dict["server"])
        if "database" in config_dict:
            flat_config["database"] = DatabaseConfig(**config_dict["database"])
        if "redis" in config_dict:
            flat_config["redis"] = RedisConfig(**config_dict["redis"])
        if "cache" in config_dict:
            flat_config["cache"] = CacheConfig(**config_dict["cache"])
        if "security" in config_dict:
            flat_config["security"] = SecurityConfig(**config_dict["security"])
        if "plugins" in config_dict:
            flat_config["plugins"] = PluginConfig(**config_dict["plugins"])
        if "sandbox" in config_dict:
            flat_config["sandbox"] = SandboxConfig(**config_dict["sandbox"])
        if "rate_limiting" in config_dict:
            flat_config["rate_limiting"] = RateLimitConfig(**config_dict["rate_limiting"])
        if "monitoring" in config_dict:
            flat_config["monitoring"] = MonitoringConfig(**config_dict["monitoring"])
        if "routes" in config_dict:
            flat_config["routes"] = RoutesConfig(routes=config_dict["routes"])
        
        return cls(**flat_config)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    # Try to load from YAML first, then fall back to environment variables
    try:
        return Settings.from_yaml()
    except Exception as e:
        print(f"Warning: Failed to load config.yml: {e}")
        print("Falling back to environment variables...")
        try:
            return Settings()
        except Exception as env_error:
            print(f"Error: Failed to load settings from environment: {env_error}")
            # Provide minimal working defaults for development
            return Settings(
                database=DatabaseConfig(url="sqlite+aiosqlite:///./ai_gateway.db"),
                redis=RedisConfig(url="redis://localhost:6379/0"),
                security=SecurityConfig(secret_key="change-me")
            ) 
