"""
Model Router Plugin
根据请求中的 model 字段动态选择正确的 AI 提供商插件
"""

from typing import Optional, Callable, Awaitable, Dict
from pathlib import Path
import yaml

from app.plugins.interface import PluginInterface, PluginMetadata, RequestContext, SandboxPermission
from app.core.structured_logging import get_logger

logger = get_logger("plugin.model_router")


class ModelRouterPlugin(PluginInterface):
    """
    模型路由器插件
    
    职责:
    1. 从请求中提取 model 字段
    2. 根据前缀或配置选择对应的 provider 插件
    3. 动态调用目标 provider 的 handle 方法
    
    特点:
    - 不需要数据库查询,使用配置文件
    - 支持前缀匹配 (claude- → claude_provider)
    - 支持精确匹配 (自定义模型)
    - 支持默认 provider
    """
    
    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="model_router",
            version="1.0.0",
            description="Routes requests to the appropriate AI provider based on model field",
            author="Prism Framework",
            permissions=[],  # 不需要特殊权限
            dependencies=[]
        )
    
    async def initialize(self):
        """初始化插件,加载配置"""
        self.logger.info("Model Router Plugin initializing...")
        
        # 缓存插件加载器引用
        from app.core.runtime import get_plugin_loader
        self.plugin_loader = get_plugin_loader()
        
        # 加载配置
        self._load_config()
        
        self.logger.info(
            "Model Router initialized",
            prefix_map=self.prefix_map,
            default_provider=self.default_provider
        )
    
    def _load_config(self):
        """
        从配置文件加载模型到 provider 的映射
        
        配置文件: plugins/model_router/config.yml
        格式:
            prefix_map:
              claude: claude_provider
              gpt: openai_provider
            exact_map:
              my-custom-model: custom_provider
            default_provider: openai_provider
        """
        # 默认配置
        self.prefix_map = {
            "claude": "claude_provider",
            "gpt": "openai_provider",
            "gemini": "gemini_provider",
        }
        self.exact_map = {}
        self.default_provider = None
        
        # 尝试加载配置文件
        try:
            if self.plugin_dir:
                config_file = Path(self.plugin_dir) / "config.yml"
                if config_file.exists():
                    with open(config_file, 'r', encoding='utf-8') as f:
                        config = yaml.safe_load(f)
                    
                    if config:
                        self.prefix_map = config.get('prefix_map', self.prefix_map)
                        self.exact_map = config.get('exact_map', {})
                        self.default_provider = config.get('default_provider')
                    
                    self.logger.info("Loaded config from file", config_file=str(config_file))
                else:
                    self.logger.info("No config file found, using defaults")
        except Exception as e:
            self.logger.warning("Failed to load config file, using defaults", error=str(e))
    
    async def handle(self, context: RequestContext, next_plugin: Optional[Callable[[RequestContext], Awaitable[None]]]):
        """
        路由逻辑:
        1. 检查请求中是否有 model 字段
        2. 如果有,根据配置选择对应的 provider 并调用
        3. 如果没有,返回错误或使用默认 provider
        
        Args:
            context: 请求上下文
            next_plugin: 下一个插件 (通常不会用到,因为 router 是终点)
        """
        # 1. 提取 model 字段
        model = context.request_data.get("model")
        
        if not model:
            # 没有 model 字段
            if self.default_provider:
                context.add_trace(f"No model specified, using default provider: {self.default_provider}")
                model = "default"  # 标记使用默认
            else:
                context.add_trace("No model specified and no default provider configured")
                context.error(
                    "Missing 'model' field in request",
                    code="missing_model",
                    status=400
                )
                return
        else:
            context.add_trace(f"Model requested: {model}")
        
        # 2. 解析 provider
        provider_name = self._resolve_provider(model)
        
        if not provider_name:
            context.add_trace(f"No provider configured for model '{model}'")
            context.error(
                f"No provider available for model '{model}'",
                code="provider_not_found",
                status=404
            )
            return
        
        context.add_trace(f"Resolved to provider: {provider_name}")
        
        # 3. 获取 provider 插件实例
        provider = self.plugin_loader.get_plugin(provider_name)
        if not provider:
            context.add_trace(f"Provider plugin '{provider_name}' not loaded")
            context.error(
                f"Provider '{provider_name}' is not available",
                code="provider_not_loaded",
                status=503
            )
            return
        
        # 4. 调用 provider 插件
        context.add_trace(f"Calling provider plugin '{provider_name}'")
        try:
            # 不传 next_plugin,因为 provider 是处理链的终点
            await provider.handle(context, None)
            context.add_trace(f"Provider '{provider_name}' execution completed")
        except Exception as e:
            self.logger.error(
                "Provider execution failed",
                provider=provider_name,
                model=model,
                error=str(e),
                exc_info=True
            )
            context.add_trace(f"Provider '{provider_name}' execution FAILED: {e}")
            context.error(
                f"Provider error: {str(e)}",
                code="provider_execution_failed",
                status=500
            )
        
        # 注意: 不调用 next_plugin,路由器已经处理完请求
    
    def _resolve_provider(self, model: str) -> Optional[str]:
        """
        根据模型名解析对应的 provider 插件名
        
        解析策略 (按优先级):
        1. 精确匹配 (exact_map)
        2. 前缀匹配 (prefix_map)
        3. 默认 provider (default_provider)
        
        Args:
            model: 模型名 (如 "claude-3-opus", "gpt-4", "gemini-pro")
        
        Returns:
            provider 插件名,如果找不到则返回 None
        
        Examples:
            "claude-3-opus-20240229" → "claude_provider"
            "gpt-4-turbo" → "openai_provider"
            "gemini-pro" → "gemini_provider"
            "my-custom-model" → "custom_provider" (如果在 exact_map 中配置)
        """
        # 策略1: 精确匹配
        if model in self.exact_map:
            return self.exact_map[model]
        
        # 策略2: 前缀匹配
        for prefix, provider_name in self.prefix_map.items():
            if model.startswith(prefix):
                return provider_name
        
        # 策略3: 默认 provider
        if self.default_provider:
            return self.default_provider
        
        # 未找到
        return None
    
    async def shutdown(self):
        """清理资源"""
        self.logger.info("Model Router Plugin shutting down")
