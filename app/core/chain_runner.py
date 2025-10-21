"""
Plugin chain runner for middleware-style request processing.
"""

import asyncio
from typing import Dict, List, Any, Optional, Callable, Awaitable, Coroutine
from functools import partial

from app.core.config import get_settings
from app.core.structured_logging import get_logger
from app.plugins.interface import PluginInterface, RequestContext, MetaPlugin
from app.core.audit_sandbox import current_plugin_context
from prometheus_client import Histogram, Counter

# Performance monitor removed in minimal build
import time
import uuid

logger = get_logger("chain_runner")

# Prometheus 指标
CHAIN_DURATION = Histogram(
    "prism_chain_duration_seconds",
    "Duration of plugin chain execution",
    ["route"]
)
PLUGIN_DURATION = Histogram(
    "prism_plugin_duration_seconds",
    "Duration of individual plugin execution",
    ["plugin_name"]
)
CHAIN_ACTIONS_TOTAL = Counter(
    "prism_chain_actions_total",
    "Total chain actions",
    ["route", "status"]  # 'success', 'error', 'short_circuited'
)

class ChainRunner:
    """
    插件调用链执行器
    
    负责根据配置中的路由信息，按顺序执行插件链，实现中间件模式的请求处理。
    """
    
    def __init__(self, plugins: Dict[str, PluginInterface]):
        """
        初始化调用链执行器
        
        Args:
            plugins: 已加载的插件实例字典 {plugin_name: plugin_instance}
        """
        settings = get_settings()
        self.plugins = plugins
        self.routes_config = settings.routes
        self._chain_cache: Dict[str, List[str]] = {}
        logger.info("ChainRunner initialized", plugin_count=len(plugins))
    
    def clear_cache(self) -> None:
        """清空路由到链条的缓存（配置变更后调用）"""
        self._chain_cache.clear()
    
    def get_chain_for_route(self, route: str, context: Optional[RequestContext] = None) -> List[str]:
        """
        获取指定路由的插件链
        
        Args:
            route: API路由路径
            context: 可选的请求上下文，用于记录追踪日志
            
        Returns:
            插件名称列表，按执行顺序排列
        """
        def trace(msg):
            if context:
                context.add_trace(msg)

        # 读取缓存
        if route in self._chain_cache:
            chain = self._chain_cache[route]
            trace(f"Chain for route '{route}' found in cache: {chain}")
            return chain
        
        trace(f"Chain for route '{route}' not in cache, resolving from config or meta plugin.")
        chain = self.routes_config.get_chain_for_route(route)
        trace(f"Initial chain from config: {chain}")

        # 元插件调用链支持：直接请求形如 "meta_plugin:chain_name"
        if not chain and ":" in route:
            normalized_route = route.lstrip("/")
            meta_plugin_name, chain_name = normalized_route.split(":", 1)
            meta_plugin = self.plugins.get(meta_plugin_name)
            if isinstance(meta_plugin, MetaPlugin):
                meta_chain_key = f"{meta_plugin_name}:{chain_name}"
                meta_chain_def = meta_plugin.chains.get(meta_chain_key)
                if meta_chain_def:
                    trace(f"Meta chain '{meta_chain_key}' resolved via meta plugin registry.")
                    chain = list(meta_chain_def.get("plugins", []))
        
        # 展开预设调用链引用
        expanded_chain = []
        for plugin_ref in chain:
            # 检查是否是预设调用链引用 (例如: "prism-security-suite:secure_api")
            if ':' in plugin_ref and not plugin_ref.startswith('http'):
                meta_plugin_name, chain_name = plugin_ref.split(':', 1)
                
                # 获取元插件
                if meta_plugin_name in self.plugins:
                    meta_plugin = self.plugins[meta_plugin_name]
                    # 显式检查是否为 MetaPlugin
                    if isinstance(meta_plugin, MetaPlugin):
                        if hasattr(meta_plugin, 'chains') and plugin_ref in meta_plugin.chains:
                            # 展开预设调用链
                            preset_chain_def = meta_plugin.chains[plugin_ref]
                            preset_chain = preset_chain_def.get('plugins', [])
                            trace(f"Expanding meta-chain '{plugin_ref}' to {preset_chain}")
                            for item in preset_chain:
                                if item == '__NEXT__':
                                    # 占位符，跳过
                                    trace("  -> Skipping '__NEXT__' placeholder.")
                                    continue
                                expanded_chain.append(item)
                            continue
            
            # 普通插件引用
            trace(f"Adding normal plugin ref '{plugin_ref}' to chain.")
            expanded_chain.append(plugin_ref)
        
        # 写入缓存
        self._chain_cache[route] = expanded_chain
        trace(f"Resolved and cached final chain for route '{route}': {expanded_chain}")
        return expanded_chain
    
    def get_default_chain_for_route(self, route: str, request_data: Dict[str, Any]) -> List[str]:
        """
        获取路由的默认调用链
        
        Args:
            route: API路由路径
            request_data: 请求数据
            
        Returns:
            默认插件名称列表，按执行顺序排列
        """
        # 默认策略：对于未配置的路由，使用认证插件
        if self.plugins.get("auth_plugin"):
            return ["auth_plugin"]
        
        return []
    
    async def run(self, route: str, request_data: Dict[str, Any]) -> RequestContext:
        """
        执行插件调用链
        
        Args:
            route: API路由路径
            request_data: 原始请求数据
            
        Returns:
            处理完成的请求上下文
            
        Raises:
            ValueError: 如果路由未配置或插件不存在
            Exception: 如果插件执行过程中出现错误
        """
        start_time = time.time()
        status = "success"  # 默认状态

        # 创建请求上下文
        context = RequestContext(request_data=request_data)
        if request_data.get("_trace"):
            context.trace_log = []
            context.add_trace(f"Execution trace enabled for request_id: {request_data.get('request_id')}")

        try:
            # 获取该路由的插件链
            plugin_chain = self.get_chain_for_route(route, context)
            
            # 如果没有配置插件链，尝试使用默认策略
            if not plugin_chain:
                context.add_trace("No plugin chain configured for route, attempting to find default.")
                plugin_chain = self.get_default_chain_for_route(route, request_data)
                if not plugin_chain:
                    logger.warning("No plugin chain configured and no default available for route", route=route)
                    context.add_trace("No default chain available. Aborting.")
                    context.response_data = {"error": f"No handlers configured for route: {route}"}
                    status = "error"
                    return context
                context.add_trace(f"Using default chain: {plugin_chain}")

            # 验证所有插件都存在
            missing_plugins = []
            context.add_trace("Validating plugins in chain...")
            for plugin_name in plugin_chain:
                try:
                    self._resolve_plugin(plugin_name, context)
                except (KeyError, ValueError) as e:
                    missing_plugins.append(f"{plugin_name}: {str(e)}")
            
            if missing_plugins:
                error_msg = f"Missing or invalid plugins for route {route}: {missing_plugins}"
                logger.error(error_msg)
                context.add_trace(f"Validation failed: {error_msg}")
                context.response_data = {"error": error_msg}
                status = "error"
                return context
            
            context.add_trace("All plugins in chain are valid.")
            # 如果请求中包含 user_id，自动注入到上下文，便于下游插件识别用户
            try:
                user_id_val = request_data.get("user_id")
                if user_id_val:
                    context.set_user_id(str(user_id_val))
            except Exception:
                pass
            
            # 构建调用链
            await self._execute_chain(context, plugin_chain, 0)
            context.add_trace("Plugin chain execution finished.")
            logger.info("Plugin chain execution completed", route=route, chain=plugin_chain)

            if context.is_short_circuited:
                status = "short_circuited"
            
            # 如果开启了追踪，将日志附加到响应数据中
            if context.trace_log is not None:
                context.response_data["_trace"] = context.trace_log

            return context
        except Exception as e:
            logger.error("Plugin chain execution failed", route=route, error=str(e), exc_info=True)
            context = RequestContext(request_data=request_data) # 确保 context 存在
            context.response_data = {"error": f"Plugin chain execution failed: {str(e)}"}
            context.is_short_circuited = True
            status = "error"
            return context
        finally:
            duration = time.time() - start_time
            CHAIN_DURATION.labels(route=route).observe(duration)
            CHAIN_ACTIONS_TOTAL.labels(route=route, status=status).inc()
    
    async def _execute_chain(self, context: RequestContext, plugin_chain: List[str], index: int) -> None:
        """
        递归执行插件链
        
        Args:
            context: 请求上下文
            plugin_chain: 插件链
            index: 当前插件在链中的索引
        """
        # 检查是否已被中断
        if context.is_short_circuited:
            context.add_trace(f"Chain short-circuited at index {index}. Halting execution.")
            logger.info("Plugin chain short-circuited", index=index)
            return
        
        # 检查是否到达链的末尾
        if index >= len(plugin_chain):
            context.add_trace("Reached end of chain.")
            logger.debug("Reached end of plugin chain")
            return
        
        plugin_name = plugin_chain[index]
        context.current_plugin_name = plugin_name
        
        # 解析插件名，支持子插件引用 (例如: "prism-security-suite.auth")
        context.add_trace(f"Executing plugin at index {index}: '{plugin_name}'")
        plugin = self._resolve_plugin(plugin_name, context)
        
        logger.debug("Executing plugin", plugin=plugin_name, index=index)
        
        # 创建下一个插件的回调函数
        next_plugin_callback: Optional[Callable[[RequestContext], Awaitable[None]]] = None
        if index + 1 < len(plugin_chain):
            # 使用 lambda 表达式来解决 partial 的类型提示问题
            next_plugin_callback = lambda ctx: self._execute_chain(ctx, plugin_chain, index + 1)
        
        # 执行当前插件（在沙箱上下文中，带耗时记录）
        with PLUGIN_DURATION.labels(plugin_name=plugin_name).time(), current_plugin_context.use(plugin_name):
            try:
                await plugin.handle(context, next_plugin_callback)
                context.add_trace(f"Plugin '{plugin_name}' execution finished successfully.")
                logger.debug("Plugin execution completed within sandbox", plugin=plugin_name)
            except Exception as e:
                logger.error("Plugin execution failed", plugin=plugin_name, error=str(e), exc_info=True)
                context.add_trace(f"Plugin '{plugin_name}' execution FAILED: {e}")
                # 将错误信息添加到响应中
                if "errors" not in context.response_data:
                    context.response_data["errors"] = []
                context.response_data["errors"].append({
                    "plugin": plugin_name,
                    "error": str(e)
                })
                # 中断调用链
                context.is_short_circuited = True
    
    def validate_chain(self, route: str) -> Dict[str, Any]:
        """
        验证指定路由的插件链配置
        
        Args:
            route: API路由路径
            
        Returns:
            验证结果字典
        """
        plugin_chain = self.get_chain_for_route(route, context=None)
        
        result = {
            "route": route,
            "chain": plugin_chain,
            "valid": True,
            "issues": []
        }
        
        if not plugin_chain:
            result["valid"] = False
            # 修复: issues 是列表，可以直接 append
            result["issues"].append("No plugin chain configured")
            return result
        
        # 检查插件是否存在
        for plugin_name in plugin_chain:
            try:
                self._resolve_plugin(plugin_name, context=None)
            except (KeyError, ValueError) as e:
                result["valid"] = False
                # 修复: issues 是列表，可以直接 append
                result["issues"].append(str(e))
        
        # 检查链是否为空
        if not plugin_chain: # 使用更 pythonic 的方式
            result["valid"] = False
            # 修复: issues 是列表，可以直接 append
            result["issues"].append("Empty plugin chain")
        
        # 检查是否有提供者插件（通常在链的末尾）
        if plugin_chain:
            last_plugin_name = plugin_chain[-1]
            if last_plugin_name in self.plugins:
                last_plugin = self.plugins[last_plugin_name]
                # 简单启发式：如果插件名包含"provider"或实现了chat_completion，认为是提供者
                metadata = last_plugin.get_metadata()
                if "provider" not in metadata.name.lower():
                    # 修复: issues 是列表，可以直接 append
                    result["issues"].append(
                        f"Last plugin '{last_plugin_name}' might not be a provider plugin"
                    )
        
        logger.info("Chain validation completed", **result)
        return result
    
    def _resolve_plugin(self, plugin_name: str, context: Optional[RequestContext] = None) -> PluginInterface:
        """
        解析插件名称，支持子插件引用
        
        Args:
            plugin_name: 插件名称，可能是 "plugin" 或 "meta_plugin.subplugin"
            context: 可选的请求上下文，用于记录追踪日志
            
        Returns:
            插件实例
            
        Raises:
            KeyError: 如果插件不存在
        """
        def trace(msg):
            if context:
                context.add_trace(msg)
        # 检查是否是子插件引用
        if '.' in plugin_name:
            trace(f"Resolving sub-plugin reference: '{plugin_name}'")
            # 解析元插件和子插件名称
            meta_plugin_name, subplugin_name = plugin_name.split('.', 1)
            
            # 获取元插件
            if meta_plugin_name not in self.plugins:
                raise KeyError(f"Meta plugin '{meta_plugin_name}' not found")
            
            meta_plugin = self.plugins[meta_plugin_name]
            trace(f"  -> Found meta-plugin: '{meta_plugin_name}'")
            
            # 检查是否是元插件
            if not isinstance(meta_plugin, MetaPlugin):
                raise ValueError(f"Plugin '{meta_plugin_name}' is not a meta plugin")
            
            # 获取子插件
            subplugin = meta_plugin.get_subplugin(subplugin_name)
            if not subplugin:
                raise KeyError(f"Subplugin '{subplugin_name}' not found in '{meta_plugin_name}'")
            
            trace(f"  -> Resolved to sub-plugin: '{subplugin_name}'")
            return subplugin
        else:
            # 普通插件
            trace(f"Resolving normal plugin reference: '{plugin_name}'")
            if plugin_name not in self.plugins:
                raise KeyError(f"Plugin '{plugin_name}' not found")
            
            trace(f"  -> Resolved to plugin: '{plugin_name}'")
            return self.plugins[plugin_name]
