"""
AI Gateway Meta Plugin

A unified AI gateway that provides OpenAI-compatible API for multiple AI providers.

Includes:
- Model Router: Intelligent routing based on model name
- Claude Provider: Anthropic Claude integration
- OpenAI Provider: OpenAI GPT integration
"""

from app.plugins.interface import MetaPlugin, PluginMetadata
from app.core.structured_logging import get_logger
from typing import Dict, Any, Optional

logger = get_logger("plugin.ai_gateway")


class AIGatewayMetaPlugin(MetaPlugin):
    """
    AI Gateway Meta Plugin
    
    Provides a unified OpenAI-compatible API for multiple AI providers
    through intelligent model routing.
    """
    
    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="ai_gateway",
            version="1.0.0",
            description="Unified AI Gateway with OpenAI-compatible API",
            author="Prism Framework",
            permissions=[],  # Sub-plugins define their own permissions
            dependencies=[]
        )
    
    async def initialize(self):
        """Initialize AI Gateway"""
        self.logger.info("AI Gateway Meta Plugin initializing...")
        self.logger.info("AI Gateway initialized successfully")
    
    async def shutdown(self):
        """Shutdown AI Gateway"""
        self.logger.info("AI Gateway shutting down...")
    
    def get_route_schema(self):
        """Define routes for AI Gateway"""
        return [
            {
                "path": "/v1/chat/completions",
                "method": "POST",
                "handler": "chat_completions",
                "description": "OpenAI-compatible chat completion endpoint",
                "request_model": "ChatCompletionRequest",
                "response_model": "ChatCompletionResponse"
            }
        ]
    
    async def invoke(self, method_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle direct API calls (route invocations)
        """
        if method_name == "chat_completions":
            return await self._handle_chat_completions(payload)
        else:
            raise ValueError(f"Unknown method: {method_name}")
    
    async def _handle_chat_completions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle chat completion requests
        
        This method creates a RequestContext and runs it through the ai_gateway:chat chain
        """
        from app.core.runtime import get_chain_runner
        
        # Run through the chain
        chain_runner = get_chain_runner()
        result_context = await chain_runner.run("ai_gateway:chat", payload)
        
        # Return the response
        if result_context.response_data:
            return result_context.response_data
        else:
            raise RuntimeError("Chain execution failed to produce a response")
