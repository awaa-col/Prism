"""
Claude Provider Plugin
Anthropic Claude AI integration for Prism

Supports:
- Claude 3 Opus, Sonnet, Haiku
- Claude 2.1, 2.0
- Streaming and non-streaming responses
- OpenAI-compatible API format
"""

import os
import time
from typing import Optional, Callable, Awaitable, AsyncGenerator, List, Dict, Any

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from app.plugins.interface import PluginInterface, PluginMetadata, RequestContext, SandboxPermission
from app.core.structured_logging import get_logger

logger = get_logger("plugin.claude_provider")


class ClaudeProvider(PluginInterface):
    """
    Claude AI Provider Plugin
    
    Integrates Anthropic's Claude models into Prism framework
    with OpenAI-compatible API format.
    
    Supported Models:
    - claude-3-opus-20240229
    - claude-3-sonnet-20240229
    - claude-3-haiku-20240307
    - claude-2.1
    - claude-2.0
    """
    
    def get_metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="claude_provider",
            version="1.0.0",
            description="Anthropic Claude AI Provider with OpenAI-compatible API",
            author="Prism Framework",
            permissions=[
                SandboxPermission(
                    type="network.https",
                    resource="api.anthropic.com",
                    description="Call Anthropic Claude API"
                )
            ],
            dependencies=[]
        )
    
    async def initialize(self):
        """
        Initialize Claude Provider
        
        Loads API key from environment and initializes the Anthropic client
        """
        self.logger.info("Initializing Claude Provider...")
        
        # Check if anthropic SDK is available
        if not ANTHROPIC_AVAILABLE:
            self.logger.error("Anthropic SDK not installed. Install with: pip install anthropic")
            raise RuntimeError("Anthropic SDK not available")
        
        # Load API key from environment
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            self.logger.warning(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Claude provider will not be functional."
            )
        
        # Initialize Anthropic client
        if self.api_key:
            self.client = anthropic.AsyncAnthropic(api_key=self.api_key)
            self.logger.info("Claude Provider initialized successfully")
        else:
            self.client = None
            self.logger.warning("Claude Provider initialized without API key")
    
    async def handle(self, context: RequestContext, next_plugin: Optional[Callable[[RequestContext], Awaitable[None]]] = None):
        """
        Handle chat completion request
        
        Converts OpenAI-compatible request to Claude API format,
        calls Claude API, and converts response back to OpenAI format.
        
        Args:
            context: Request context containing model, messages, etc.
            next_plugin: Next plugin in chain (usually None for providers)
        """
        # Check if client is initialized
        if not self.client:
            context.error(
                "Claude Provider not configured: ANTHROPIC_API_KEY not set",
                code="configuration_error",
                status=500
            )
            return
        
        # Extract parameters from request
        model = context.request_data.get("model")
        messages = context.request_data.get("messages", [])
        stream = context.request_data.get("stream", False)
        temperature = context.request_data.get("temperature", 1.0)
        max_tokens = context.request_data.get("max_tokens", 1024)
        top_p = context.request_data.get("top_p", None)
        stop_sequences = context.request_data.get("stop", None)
        
        context.add_trace(f"Processing Claude request with model: {model}")
        
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
        
        # Process messages (extract system message if present)
        system_message, claude_messages = self._process_messages(messages)
        
        # Call Claude API
        try:
            if stream:
                # Streaming response
                context.add_trace("Initiating streaming request to Claude API")
                context.response_data["stream"] = self._stream_chat(
                    model=model,
                    messages=claude_messages,
                    system=system_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    stop_sequences=stop_sequences
                )
            else:
                # Non-streaming response
                context.add_trace("Initiating non-streaming request to Claude API")
                response = await self._complete_chat(
                    model=model,
                    messages=claude_messages,
                    system=system_message,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    top_p=top_p,
                    stop_sequences=stop_sequences
                )
                context.response_data.update(response)
            
            context.add_trace("Claude API call completed successfully")
        
        except anthropic.APIError as e:
            # Anthropic-specific errors
            self.logger.error(
                "Claude API error",
                model=model,
                error=str(e),
                status_code=getattr(e, 'status_code', None),
                exc_info=True
            )
            
            # Map Anthropic errors to HTTP status codes
            if isinstance(e, anthropic.AuthenticationError):
                context.error(
                    f"Authentication failed: {str(e)}",
                    code="authentication_failed",
                    status=401
                )
            elif isinstance(e, anthropic.RateLimitError):
                context.error(
                    f"Rate limit exceeded: {str(e)}",
                    code="rate_limit_exceeded",
                    status=429
                )
            elif isinstance(e, anthropic.BadRequestError):
                context.error(
                    f"Invalid request: {str(e)}",
                    code="invalid_request",
                    status=400
                )
            else:
                context.error(
                    f"Claude API error: {str(e)}",
                    code="provider_api_error",
                    status=502
                )
        
        except Exception as e:
            # Unexpected errors
            self.logger.error(
                "Unexpected error in Claude Provider",
                model=model,
                error=str(e),
                exc_info=True
            )
            context.error(
                f"Internal error: {str(e)}",
                code="internal_error",
                status=500
            )
    
    def _process_messages(self, messages: List[Dict]) -> tuple[Optional[str], List[Dict]]:
        """
        Process messages to extract system message
        
        Claude API requires system message to be separate from messages array.
        This method extracts the first system message (if any) and returns
        the remaining messages.
        
        Args:
            messages: OpenAI-format messages
        
        Returns:
            (system_message, claude_messages) tuple
        """
        system_message = None
        claude_messages = []
        
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            
            if role == "system":
                # Extract system message
                if system_message is None:
                    system_message = content
                else:
                    # If multiple system messages, concatenate them
                    system_message += "\n\n" + content
            else:
                # Regular message
                claude_messages.append({
                    "role": role,
                    "content": content
                })
        
        return system_message, claude_messages
    
    async def _complete_chat(
        self,
        model: str,
        messages: List[Dict],
        system: Optional[str],
        temperature: float,
        max_tokens: int,
        top_p: Optional[float],
        stop_sequences: Optional[List[str]]
    ) -> Dict:
        """
        Non-streaming chat completion
        
        Args:
            model: Claude model name
            messages: Messages array (without system message)
            system: System message (if any)
            temperature: Temperature parameter
            max_tokens: Maximum tokens to generate
            top_p: Top-p sampling parameter
            stop_sequences: Stop sequences
        
        Returns:
            OpenAI-compatible response dictionary
        """
        # Build API parameters
        params = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        if system:
            params["system"] = system
        
        if top_p is not None:
            params["top_p"] = top_p
        
        if stop_sequences:
            params["stop_sequences"] = stop_sequences if isinstance(stop_sequences, list) else [stop_sequences]
        
        # Call Claude API
        response = await self.client.messages.create(**params)
        
        # Convert to OpenAI format
        return self._to_openai_format(response)
    
    async def _stream_chat(
        self,
        model: str,
        messages: List[Dict],
        system: Optional[str],
        temperature: float,
        max_tokens: int,
        top_p: Optional[float],
        stop_sequences: Optional[List[str]]
    ) -> AsyncGenerator[Dict, None]:
        """
        Streaming chat completion
        
        Args:
            Same as _complete_chat
        
        Yields:
            OpenAI-compatible chunk dictionaries
        """
        # Build API parameters
        params = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        
        if system:
            params["system"] = system
        
        if top_p is not None:
            params["top_p"] = top_p
        
        if stop_sequences:
            params["stop_sequences"] = stop_sequences if isinstance(stop_sequences, list) else [stop_sequences]
        
        # Call Claude API with streaming
        async with self.client.messages.stream(**params) as stream:
            # First chunk with role
            chunk_id = f"chatcmpl-{int(time.time())}"
            yield {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None
                }]
            }
            
            # Content chunks
            async for text in stream.text_stream:
                yield {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": text},
                        "finish_reason": None
                    }]
                }
            
            # Final chunk with finish_reason
            final_message = await stream.get_final_message()
            yield {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": final_message.stop_reason
                }]
            }
    
    def _to_openai_format(self, claude_response) -> Dict:
        """
        Convert Claude API response to OpenAI-compatible format
        
        Args:
            claude_response: Anthropic Message object
        
        Returns:
            OpenAI-compatible response dictionary
        """
        # Extract text content from Claude response
        # Claude returns content as a list of content blocks
        content_text = ""
        for block in claude_response.content:
            if block.type == "text":
                content_text += block.text
        
        return {
            "id": claude_response.id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": claude_response.model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content_text
                },
                "finish_reason": claude_response.stop_reason
            }],
            "usage": {
                "prompt_tokens": claude_response.usage.input_tokens,
                "completion_tokens": claude_response.usage.output_tokens,
                "total_tokens": claude_response.usage.input_tokens + claude_response.usage.output_tokens
            }
        }
    
    async def shutdown(self):
        """Clean up resources"""
        if hasattr(self, 'client') and self.client:
            # Anthropic client doesn't need explicit cleanup
            pass
        self.logger.info("Claude Provider shut down")
