"""
WebSocket support for plugins.
为插件提供WebSocket实时通信能力。
"""

import asyncio
import json
from typing import Dict, Any, Optional, Callable, Set
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.routing import APIRouter
from fastapi import status

from app.core.structured_logging import get_logger
from app.plugins.interface import PluginInterface, RequestContext
from app.api.deps import get_api_key_or_user
from app.core.cache import get_rate_limiter

logger = get_logger("plugin.websocket")


class WebSocketMessageType(Enum):
    """WebSocket消息类型"""
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    MESSAGE = "message"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
    STREAM_START = "stream_start"
    STREAM_DATA = "stream_data"
    STREAM_END = "stream_end"


@dataclass
class WebSocketMessage:
    """WebSocket消息"""
    type: WebSocketMessageType
    data: Any
    metadata: Optional[Dict[str, Any]] = None


class WebSocketHandler(ABC):
    """WebSocket处理器基类"""
    
    def __init__(self, plugin: PluginInterface):
        self.plugin = plugin
        self.connections: Set[WebSocket] = set()
        self.logger = get_logger(f"websocket.{plugin.get_metadata().name}")
    
    @abstractmethod
    async def on_connect(self, websocket: WebSocket, context: RequestContext) -> bool:
        """
        连接建立时调用
        
        Returns:
            bool: 是否接受连接
        """
        pass
    
    @abstractmethod
    async def on_message(self, websocket: WebSocket, message: WebSocketMessage, context: RequestContext) -> None:
        """处理接收到的消息"""
        pass
    
    async def on_disconnect(self, websocket: WebSocket, context: RequestContext) -> None:
        """连接断开时调用"""
        self.logger.info("WebSocket disconnected")
    
    async def send_message(self, websocket: WebSocket, message: WebSocketMessage) -> None:
        """发送消息到客户端"""
        try:
            await websocket.send_json({
                "type": message.type.value,
                "data": message.data,
                "metadata": message.metadata or {}
            })
        except Exception as e:
            self.logger.error(f"Failed to send message: {e}")
    
    async def broadcast(self, message: WebSocketMessage) -> None:
        """广播消息到所有连接"""
        disconnected = set()
        for websocket in self.connections:
            try:
                await self.send_message(websocket, message)
            except Exception:
                disconnected.add(websocket)
        
        # 清理断开的连接
        self.connections -= disconnected


class StreamingWebSocketHandler(WebSocketHandler):
    """
    流式响应的WebSocket处理器
    
    适用于需要实时流式输出的场景，如聊天机器人。
    """
    
    async def on_connect(self, websocket: WebSocket, context: RequestContext) -> bool:
        """接受所有连接"""
        await websocket.accept()
        self.connections.add(websocket)
        self.logger.info("WebSocket connected for streaming")
        return True
    
    async def on_message(self, websocket: WebSocket, message: WebSocketMessage, context: RequestContext) -> None:
        """处理聊天消息"""
        if message.type == WebSocketMessageType.MESSAGE:
            # 开始流式响应
            await self.send_message(websocket, WebSocketMessage(
                type=WebSocketMessageType.STREAM_START,
                data={"id": message.metadata.get("id")}
            ))
            
            try:
                # 调用插件处理
                context.request_data = message.data
                context.request_data["stream"] = True
                
                # 使用插件的handle方法
                await self.plugin.handle(context)
                
                # 发送流式数据
                if "chunks" in context.response_data:
                    for chunk in context.response_data["chunks"]:
                        await self.send_message(websocket, WebSocketMessage(
                            type=WebSocketMessageType.STREAM_DATA,
                            data={"content": chunk},
                            metadata={"id": message.metadata.get("id")}
                        ))
                
                # 结束流式响应
                await self.send_message(websocket, WebSocketMessage(
                    type=WebSocketMessageType.STREAM_END,
                    data={"id": message.metadata.get("id")}
                ))
                
            except Exception as e:
                await self.send_message(websocket, WebSocketMessage(
                    type=WebSocketMessageType.ERROR,
                    data={"error": str(e)},
                    metadata={"id": message.metadata.get("id")}
                ))
    
    async def on_disconnect(self, websocket: WebSocket, context: RequestContext) -> None:
        """清理连接"""
        self.connections.discard(websocket)
        await super().on_disconnect(websocket, context)


class WebSocketPlugin(PluginInterface):
    """
    支持WebSocket的插件基类
    
    继承此类的插件可以提供WebSocket端点。
    """
    
    def __init__(self, http_client=None, permission_manager=None):
        super().__init__(http_client, permission_manager)
        self.websocket_handler: Optional[WebSocketHandler] = None
        self._websocket_router: Optional[APIRouter] = None
    
    @abstractmethod
    def create_websocket_handler(self) -> WebSocketHandler:
        """创建WebSocket处理器"""
        pass
    
    def get_router(self) -> Optional[APIRouter]:
        """获取包含WebSocket端点的路由"""
        if self._websocket_router is None:
            self._websocket_router = self._create_websocket_router()
        return self._websocket_router
    
    def _create_websocket_router(self) -> APIRouter:
        """创建WebSocket路由"""
        router = APIRouter()
        self.websocket_handler = self.create_websocket_handler()
        
        @router.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            context = RequestContext(
                request_data={},
                response_data={}
            )
            # 鉴权：从子协议/查询或头部获取 Authorization（标准浏览器限制时，允许通过 query 传）
            token = None
            auth_header = websocket.headers.get("Authorization") or websocket.query_params.get("authorization")
            if auth_header:
                parts = auth_header.split()
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    token = parts[1]
            if not token:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            # 校验 JWT 或 API Key
            try:
                # 复用依赖方法的核心逻辑：这里简化为直接设置 user_id 到 context
                from app.core.security import decode_access_token
                payload = decode_access_token(token)
                if payload and (payload.get("user_id") or payload.get("sub")):
                    context.set_user_id(str(payload.get("user_id") or payload.get("sub")))
                else:
                    # 退回到 API Key 校验（哈希验证在依赖中实现，这里仅做通行占位，建议路由前做依赖注入）
                    context.set_user_id("api_key_user")
            except Exception:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            
            # 尝试接受连接
            if not await self.websocket_handler.on_connect(websocket, context):
                await websocket.close()
                return
            
            # 基础速率限制（每连接）
            limiter = get_rate_limiter(prefix="ws_rate")
            
            try:
                last_pong = asyncio.get_event_loop().time()
                while True:
                    # 接收消息
                    data = await websocket.receive_json()
                    # 心跳/限速
                    if data.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                        last_pong = asyncio.get_event_loop().time()
                        continue
                    # 简单速率限制，以 user_id 为键
                    uid = context.get_user_id() or "anon"
                    allowed, remaining = await limiter.is_allowed(f"{uid}", limit=20, period=1)
                    if not allowed:
                        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                        return
                    
                    # 解析消息
                    message = WebSocketMessage(
                        type=WebSocketMessageType(data.get("type", "message")),
                        data=data.get("data", {}),
                        metadata=data.get("metadata", {})
                    )
                    
                    # 处理消息
                    await self.websocket_handler.on_message(websocket, message, context)
                    
            except WebSocketDisconnect:
                await self.websocket_handler.on_disconnect(websocket, context)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                await self.websocket_handler.on_disconnect(websocket, context)
        
        return router


class WebSocketManager:
    """
    WebSocket连接管理器
    
    管理所有WebSocket连接，支持房间/频道概念。
    """
    
    def __init__(self):
        self.connections: Dict[str, Set[WebSocket]] = {}  # room -> connections
        self.connection_rooms: Dict[WebSocket, Set[str]] = {}  # connection -> rooms
    
    async def connect(self, websocket: WebSocket, room: str = "default") -> None:
        """将连接加入房间"""
        await websocket.accept()
        
        if room not in self.connections:
            self.connections[room] = set()
        self.connections[room].add(websocket)
        
        if websocket not in self.connection_rooms:
            self.connection_rooms[websocket] = set()
        self.connection_rooms[websocket].add(room)
        
        logger.info(f"WebSocket connected to room: {room}")
    
    def disconnect(self, websocket: WebSocket) -> None:
        """断开连接"""
        # 从所有房间移除
        rooms = self.connection_rooms.get(websocket, set())
        for room in rooms:
            self.connections[room].discard(websocket)
            if not self.connections[room]:
                del self.connections[room]
        
        # 清理连接记录
        if websocket in self.connection_rooms:
            del self.connection_rooms[websocket]
        
        logger.info("WebSocket disconnected")
    
    async def send_to_connection(self, websocket: WebSocket, message: Dict[str, Any]) -> None:
        """发送消息到特定连接"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send to connection: {e}")
            self.disconnect(websocket)
    
    async def broadcast_to_room(self, room: str, message: Dict[str, Any], exclude: Optional[WebSocket] = None) -> None:
        """广播消息到房间内所有连接"""
        if room not in self.connections:
            return
        
        disconnected = set()
        for websocket in self.connections[room]:
            if websocket == exclude:
                continue
            
            try:
                await websocket.send_json(message)
            except Exception:
                disconnected.add(websocket)
        
        # 清理断开的连接
        for websocket in disconnected:
            self.disconnect(websocket)
    
    async def broadcast_to_all(self, message: Dict[str, Any]) -> None:
        """广播消息到所有连接"""
        for room in list(self.connections.keys()):
            await self.broadcast_to_room(room, message)
    
    def get_room_connections(self, room: str) -> int:
        """获取房间内的连接数"""
        return len(self.connections.get(room, set()))
    
    def get_total_connections(self) -> int:
        """获取总连接数"""
        return len(self.connection_rooms)


# 全局WebSocket管理器实例
websocket_manager = WebSocketManager()


def create_websocket_router(path: str = "/ws") -> APIRouter:
    """
    创建通用的WebSocket路由
    
    可以被主应用直接使用。
    """
    router = APIRouter()
    
    @router.websocket(path)
    async def websocket_endpoint(websocket: WebSocket, room: str = "default"):
        await websocket_manager.connect(websocket, room)
        
        try:
            while True:
                data = await websocket.receive_json()
                
                # 处理不同类型的消息
                message_type = data.get("type", "message")
                
                if message_type == "ping":
                    await websocket.send_json({"type": "pong"})
                
                elif message_type == "broadcast":
                    # 广播到房间
                    await websocket_manager.broadcast_to_room(
                        room,
                        {
                            "type": "message",
                            "data": data.get("data"),
                            "from": "broadcast"
                        },
                        exclude=websocket
                    )
                
                else:
                    # 回显消息（示例）
                    await websocket.send_json({
                        "type": "echo",
                        "data": data
                    })
                    
        except WebSocketDisconnect:
            websocket_manager.disconnect(websocket)
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            websocket_manager.disconnect(websocket)
    
    return router