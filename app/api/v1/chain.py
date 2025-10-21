from typing import Any, Dict, Optional, AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import CurrentAPIKey, RequestID, check_rate_limit
from app.utils.responses import APIResponse, ResponseFormatter

# Access chain runner via runtime registry
from app.core.runtime import get_chain_runner

router = APIRouter()


class ChainRequest(BaseModel):
    model: Optional[str] = None
    messages: Optional[list[Dict[str, Any]]] = None
    inputs: Optional[Dict[str, Any]] = None
    stream: Optional[bool] = False
    # 允许附带任意扩展字段
    class Config:
        extra = "allow"


async def _sse_from_context(context) -> AsyncGenerator[bytes, None]:
    import json

    try:
        # 优先使用插件提供的原生流
        stream_obj = context.response_data.get("stream")
        if stream_obj is not None:
            async for chunk in stream_obj:
                if chunk is None:
                    continue
                data = chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)
                yield f"event: chunk\ndata: {data}\n\n".encode("utf-8")
            yield b"event: done\ndata: [DONE]\n\n"
            return

        # 其次兼容 chunks
        chunks = context.response_data.get("chunks", [])
        if chunks:
            for chunk in chunks:
                if chunk is None:
                    continue
                data = chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)
                yield f"event: chunk\ndata: {data}\n\n".encode("utf-8")
            yield b"event: done\ndata: [DONE]\n\n"
            return

        # 兜底：一次性内容
        content = context.response_data.get("content", "")
        if content:
            data = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            yield f"event: content\ndata: {data}\n\n".encode("utf-8")
        yield b"event: done\ndata: [DONE]\n\n"
    except Exception as e:
        import json
        err = {"error": str(e), "type": "internal_error"}
        yield f"event: error\ndata: {json.dumps(err, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"event: done\ndata: [DONE]\n\n"


@router.post("/{route}")
async def run_chain_endpoint(
    route: str,
    payload: ChainRequest,
    request: Request,
    api_key: CurrentAPIKey,  # 兼容 JWT 或 API Key
    request_id: RequestID,
    _: None = Depends(check_rate_limit),
    stream: bool = Query(False, description="是否以SSE流式返回"),
    forward_headers: Optional[str] = Query(
        None,
        description="逗号分隔的需透传到插件的请求头名（大小写不敏感），默认不透传"
    ),
    x_prism_trace: Optional[str] = Header(None, description="设置为 'true' 以启用执行链路追踪"),
):
    """按配置执行插件链。"""
    # 确保 ChainRunner 已初始化
    runner = get_chain_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="Chain runner not initialized")

    # 组装请求数据，附带用户与请求ID
    request_data: Dict[str, Any] = payload.dict(by_alias=True, exclude_none=True)
    request_data["request_id"] = request_id

    # APIKey 兼容虚拟键，可能无 user_id
    user_id = getattr(api_key, "user_id", None)
    if user_id:
        request_data["user_id"] = str(user_id)

    # （可选）按需透传请求头（默认不透传，需调用方显式指定）
    if forward_headers:
        allow = {h.strip().lower() for h in forward_headers.split(',') if h and h.strip()}
        if allow:
            forwarded: Dict[str, str] = {}
            for name, value in request.headers.items():
                lname = name.lower()
                if lname in allow and lname not in {"authorization", "cookie", "set-cookie"}:
                    forwarded[lname] = value
            if forwarded:
                request_data["headers"] = forwarded

    # 对齐查询参数的 stream
    if stream:
        request_data["stream"] = True

    # 检查是否启用追踪
    if x_prism_trace and x_prism_trace.lower() == 'true':
        request_data["_trace"] = True

    # 执行链
    context = await runner.run(route=f"/{route}" if not route.startswith("/") else route, request_data=request_data)

    # 流式返回
    want_stream = stream or bool(context.response_data.get("stream")) or bool(context.response_data.get("chunks"))
    if want_stream:
        return StreamingResponse(
            _sse_from_context(context),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Request-ID": request_id,
            },
        )

    # 标准JSON返回
    std = ResponseFormatter.context_to_standard_format(context)

    # 如果追踪日志存在，确保它在最终的响应中
    if "_trace" in context.response_data:
        if isinstance(std, dict): # 假设 std 是一个字典
            std["_trace"] = context.response_data["_trace"]
        elif hasattr(std, 'body'): # 假设 std 是一个 Response 对象
            try:
                import json
                body = json.loads(std.body)
                body["_trace"] = context.response_data["_trace"]
                std.body = json.dumps(body).encode('utf-8')
            except Exception:
                pass # 无法解析或修改 body，忽略

    # 直接返回标准格式（限流头由全局中间件/异常器负责）
    return std