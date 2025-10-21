"""
统一的API响应格式处理器
"""

from typing import Any, Optional, Dict, List
from datetime import datetime, timezone
from fastapi.responses import JSONResponse


class APIResponse:
    """统一的API响应格式"""
    
    @staticmethod
    def success(
        data: Any = None, 
        message: str = "Success",
        code: str = "success",
        total: Optional[int] = None
    ) -> Dict[str, Any]:
        """成功响应格式"""
        response = {
            "success": True,
            "code": code,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
        }
        
        if data is not None:
            response["data"] = data
            
        if total is not None:
            response["total"] = total
            
        return response
    
    @staticmethod
    def error(
        message: str,
        code: str = "error", 
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None
    ) -> JSONResponse:
        """错误响应格式"""
        response = {
            "success": False,
            "code": code,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
        }
        
        if details:
            response["details"] = details
            
        return JSONResponse(
            status_code=status_code,
            content=response
        )
    
    @staticmethod
    def paginated(
        data: List[Any],
        total: int,
        page: int = 1,
        size: int = 20,
        message: str = "Success"
    ) -> Dict[str, Any]:
        """分页响应格式"""
        return {
            "success": True,
            "code": "success",
            "message": message,
            "data": data,
            "pagination": {
                "total": total,
                "page": page,
                "size": size,
                "pages": (total + size - 1) // size
            },
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
        }


class APIException(Exception):
    """统一的API异常类"""
    
    def __init__(
        self, 
        message: str, 
        code: str = "error",
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.code = code  
        self.status_code = status_code
        self.details = details
        super().__init__(message)


"""
Response format converters for plugin chain execution.
"""

import json
from typing import Dict, Any, List, Optional, AsyncGenerator

from app.plugins.interface import RequestContext


class ResponseFormatter:
    """Convert RequestContext to various response formats"""
    
    @staticmethod
    def context_to_standard_format(context: RequestContext) -> Dict[str, Any]:
        """
        将 RequestContext 转换为标准 API 格式
        
        Args:
            context: 插件调用链执行完成的请求上下文
            
        Returns:
            标准格式的响应字典
        """
        # 检查是否有错误
        if context.is_short_circuited and "error" in context.response_data:
            # 将插件返回的完整错误信息透传出去，保留 code/status 等权限相关元数据
            failure_payload = dict(context.response_data)
            failure_payload.setdefault("code", "plugin_execution_failed")
            failure_payload["success"] = False
            return failure_payload
        
        # 获取响应内容
        content = context.response_data.get("content", "")
        if not content:
            # 如果没有content，尝试从chunks合并
            chunks = context.response_data.get("chunks", [])
            content = "".join(chunks) if chunks else ""
        
        # 返回标准格式的响应
        return {
            "success": True,
            "data": content,
            "metadata": {
                "shared_state": context._shared_state,
                "user_id": context.user_id
            }
        }
    
    @staticmethod
    async def stream_generator(context: RequestContext) -> AsyncGenerator[str, None]:
        """
        为流式响应创建生成器
        
        Args:
            context: 请求上下文（包含流式响应数据）
            
        Yields:
            流式响应块
        """
        try:
            # 检查是否有错误
            if context.is_short_circuited and "error" in context.response_data:
                error_chunk = {
                    "error": context.response_data["error"],
                    "type": "plugin_error"
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"
                return
            
            # 获取流式数据
            chunks = context.response_data.get("chunks", [])
            
            if chunks:
                # 如果有预生成的块，逐个发送
                for chunk in chunks:
                    if chunk:
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
            else:
                # 如果没有块，发送完整内容
                content = context.response_data.get("content", "")
                if content:
                    yield f"data: {json.dumps({'content': content})}\n\n"
            
            # 发送结束块
            yield "data: [DONE]\n\n"
            
        except Exception as e:
            # 发送错误块
            error_chunk = {
                "error": str(e),
                "type": "internal_error"
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
    
    @staticmethod
    def validate_context(context: RequestContext) -> List[str]:
        """
        验证 RequestContext 的有效性
        
        Args:
            context: 要验证的请求上下文
            
        Returns:
            验证错误列表（空列表表示无错误）
        """
        errors = []
        
        if not isinstance(context, RequestContext):
            errors.append("Invalid context type")
            return errors
        
        # 检查是否有响应数据
        if not context.response_data:
            errors.append("Missing response data")
        else:
            # 检查是否有内容或错误信息
            has_content = bool(context.response_data.get("content"))
            has_chunks = bool(context.response_data.get("chunks"))
            has_error = bool(context.response_data.get("error"))
            
            if not (has_content or has_chunks or has_error):
                errors.append("No content, chunks, or error in response data")
        
        return errors 
