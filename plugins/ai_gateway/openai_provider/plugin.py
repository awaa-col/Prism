"""
OpenAI Provider Plugin
OpenAI API integration for Prism

Supports:
- GPT-4, GPT-4 Turbo
- GPT-3.5 Turbo
- Streaming and non-streaming responses
- Function calling
- Native OpenAI format (no conversion needed!)
"""

import os
from typing import Optional, Callable, Awaitable, AsyncGenerator, List, Dict, Any

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from app.plugins.interface import PluginInterface, PluginMetadata, RequestContext, SandboxPermission
from app.core.structured_logging import get_logger

logger = get_logger("plugin.openai_provider")


class OpenAIProvider(PluginInterface):
    """
    OpenAI Provider Plugin
    
    Integrates OpenAI's GPT models into Prism framework.
    Since our API is OpenAI-compatible, this plugin is very simple!
    
    Supported Models:
    - gpt-4-turbo-preview
    - gpt-4-0125-preview
    - gpt-4-1106-preview
    - gpt-4
    - gpt-3.5-turbo-0125
    - gpt-3.5-turbo
    """
    
    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="openai_provider",
            version="1.0.0",
            description="OpenAI GPT Provider with native API support",
            author="Prism Framework",
            permissions=[
                SandboxPermission(
                    type="network.https",
                    resource="api.openai.com",
                    description="Call OpenAI API"
                )
            ],
            dependencies=[]
        )
    
    async def initialize(self):
        """
        Initialize OpenAI Provider
        
        Loads API key from environment and initializes the OpenAI client
        """
        self.logger.info("Initializing OpenAI Provider...")
        
        # Check if openai SDK is available
        if not OPENAI_AVAILABLE:
            self.logger.error("OpenAI SDK not installed. Install with: pip install openai")
            raise RuntimeError("OpenAI SDK not available")
        
        # Load API key from environment
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            self.logger.warning(
                "OPENAI_API_KEY environment variable not set. "
                "OpenAI provider will not be functional."
            )
        
        # Initialize OpenAI client
        if self.api_key:
            self.client = openai.AsyncOpenAI(api_key=self.api_key)
            self.logger.info("OpenAI Provider initialized successfully")
        else:
            self.client = None
            self.logger.warning("OpenAI Provider initialized without API key")
    
    async def handle(self, context: RequestContext, next_plugin: Optional[Callable[[RequestContext], Awaitable[None]]] = None):
        """
        Handle chat completion request
        
        Since our API is OpenAI-compatible, we can pass the request
        directly to OpenAI with minimal processing!
        
        Args:
            context: Request context containing model, messages, etc.
            next_plugin: Next plugin in chain (usually None for providers)
        """
        # Check if client is initialized
        if not self.client:
            context.error(
                "OpenAI Provider not configured: OPENAI_API_KEY not set",
                code="configuration_error",
                status=500
            )
            return
        
        # Extract parameters (they're already in OpenAI format!)
        model = context.request_data.get("model")
        messages = context.request_data.get("messages", [])
        stream = context.request_data.get("stream", False)
        
        context.add_trace(f"Processing OpenAI request with model: {model}")
        
        # Validate required parameters
        if not model:
            context.error(
                "Missing required field: model",
                code="missing_model",
                status=400
            )
            return
        
        if not messages:
            context.error(
                "Missing required field: messages",
                code="missing_messages",
                status=400
            )
            return
        
        # Build API parameters
        # Remove internal fields that shouldn't be sent to OpenAI
        api_params = {
            k: v for k, v in context.request_data.items()
            if k not in ["user_id", "request_id", "_trace", "headers"]
        }
        
        # Call OpenAI API
        try:
            if stream:
                # Streaming response
                context.add_trace("Initiating streaming request to OpenAI API")
                context.response_data["stream"] = self._stream_chat(api_params)
            else:
                # Non-streaming response
                context.add_trace("Initiating non-streaming request to OpenAI API")
                response = await self.client.chat.completions.create(**api_params)
                
                # Convert Pydantic model to dict
                response_dict = response.model_dump()
                context.response_data.update(response_dict)
            
            context.add_trace("OpenAI API call completed successfully")
        
        except openai.APIError as e:
            # OpenAI-specific errors
            self.logger.error(
                "OpenAI API error",
                model=model,
                error=str(e),
                status_code=getattr(e, 'status_code', None),
                exc_info=True
            )
            
            # Map OpenAI errors to HTTP status codes
            if isinstance(e, openai.AuthenticationError):
                context.error(
                    f"Authentication failed: {str(e)}",
                    code="authentication_failed",
                    status=401
                )
            elif isinstance(e, openai.RateLimitError):
                context.error(
                    f"Rate limit exceeded: {str(e)}",
                    code="rate_limit_exceeded",
                    status=429
                )
            elif isinstance(e, openai.BadRequestError):
                context.error(
                    f"Invalid request: {str(e)}",
                    code="invalid_request",
                    status=400
                )
            else:
                context.error(
                    f"OpenAI API error: {str(e)}",
                    code="provider_api_error",
                    status=502
                )
        
        except Exception as e:
            # Unexpected errors
            self.logger.error(
                "Unexpected error in OpenAI Provider",
                model=model,
                error=str(e),
                exc_info=True
            )
            context.error(
                f"Internal error: {str(e)}",
                code="internal_error",
                status=500
            )
    
    async def _stream_chat(self, api_params: Dict) -> AsyncGenerator[Dict, None]:
        """
        Streaming chat completion
        
        Args:
            api_params: Parameters to pass to OpenAI API
        
        Yields:
            OpenAI-format chunk dictionaries
        """
        # Call OpenAI API with streaming
        stream = await self.client.chat.completions.create(**api_params, stream=True)
        
        async for chunk in stream:
            # Convert Pydantic model to dict
            yield chunk.model_dump()
    
    async def shutdown(self):
        """Clean up resources"""
        if hasattr(self, 'client') and self.client:
            await self.client.close()
        self.logger.info("OpenAI Provider shut down")
