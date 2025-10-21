import importlib
import inspect
import os
from typing import Dict, Optional, Type

from app.core.structured_logging import get_logger
from app.oauth_providers.base import BaseOAuthProvider

logger = get_logger("oauth.registry")

class OAuthProviderRegistry:
    def __init__(self, provider_dir: str = "app/oauth_providers"):
        self.provider_dir = provider_dir
        self._providers: Dict[str, BaseOAuthProvider] = {}

    def load_providers(self):
        """
        从指定目录动态发现并加载 OAuth 提供商。
        """
        logger.info("正在加载 OAuth 提供商...", directory=self.provider_dir)
        if not os.path.isdir(self.provider_dir):
            logger.warning("未找到 OAuth 提供商目录。", directory=self.provider_dir)
            return

        for filename in os.listdir(self.provider_dir):
            if filename.endswith(".py") and not filename.startswith("_"):
                module_name = filename[:-3]
                module_path = f"{self.provider_dir.replace('/', '.')}.{module_name}"
                try:
                    module = importlib.import_module(module_path)
                    for _, obj in inspect.getmembers(module, inspect.isclass):
                        if issubclass(obj, BaseOAuthProvider) and obj is not BaseOAuthProvider:
                            try:
                                # 实例化会触发配置加载
                                module_name_lower = module_name.lower()
                                provider_instance = obj(provider_name=module_name_lower)
                                provider_name = provider_instance.name
                                if provider_name in self._providers:
                                    logger.warning(
                                        "发现重复的 OAuth 提供商，将进行覆盖。",
                                        provider_name=provider_name,
                                    )
                                self._providers[provider_name] = provider_instance
                                logger.info("成功加载并配置 OAuth 提供商", provider_name=provider_name)
                            except (FileNotFoundError, ValueError) as config_error:
                                # 捕获配置错误，记录警告，然后跳过此提供商
                                # FileNotFoundError 意味着该插件未被配置，这是正常情况
                                if isinstance(config_error, FileNotFoundError):
                                    logger.debug(
                                        "OAuth 提供商未配置，将被跳过。",
                                        provider_class=obj.__name__,
                                        error=str(config_error)
                                    )
                                else: # ValueError 意味着配置文件有问题
                                    logger.warning(
                                        "加载 OAuth 提供商配置失败，该提供商将被禁用。",
                                        provider_class=obj.__name__,
                                        error=str(config_error)
                                    )
                except Exception as e:
                    logger.error(
                        "从模块加载 OAuth 提供商失败，捕获到意外异常。",
                        module=module_path,
                        error=str(e),
                        exc_info=True,  # 记录完整的异常堆栈信息
                    )
        logger.info(f"已加载 {len(self._providers)} 个 OAuth 提供商。", providers=list(self._providers.keys()))

    def get_provider(self, name: str) -> Optional[BaseOAuthProvider]:
        """
        按名称获取提供商实例。
        """
        return self._providers.get(name.lower())

    def list_providers(self) -> Dict[str, BaseOAuthProvider]:
        """
        列出所有已加载的提供商。
        """
        return self._providers

# 全局实例
oauth_provider_registry = OAuthProviderRegistry()

def get_oauth_provider_registry() -> OAuthProviderRegistry:
    return oauth_provider_registry


