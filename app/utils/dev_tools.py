"""
Developer tools and utilities for plugin development.
"""

import time
import traceback
import asyncio
from typing import Dict, Any, List, Optional, Callable, TypeVar, Coroutine
from functools import wraps
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json

from app.core.structured_logging import get_logger
from app.plugins.interface import RequestContext, PluginInterface

logger = get_logger("dev_tools")

T = TypeVar('T')


@dataclass
class PerformanceMetrics:
    """Performance metrics for a function call"""
    function_name: str
    start_time: float
    end_time: float
    duration: float
    success: bool
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "function": self.function_name,
            "duration_ms": self.duration * 1000,
            "success": self.success,
            "error": self.error
        }


@dataclass
class CallTrace:
    """Trace information for plugin calls"""
    plugin_name: str
    method_name: str
    start_time: float
    end_time: Optional[float] = None
    children: List['CallTrace'] = field(default_factory=list)
    context_before: Optional[Dict] = None
    context_after: Optional[Dict] = None
    error: Optional[str] = None
    
    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "plugin": self.plugin_name,
            "method": self.method_name,
            "duration_ms": self.duration * 1000,
            "children": [child.to_dict() for child in self.children],
            "error": self.error
        }


class PerformanceTracker:
    """Track performance metrics for functions"""
    
    def __init__(self):
        self.metrics: List[PerformanceMetrics] = []
        self._enabled = True
    
    def track(self, func: Callable) -> Callable:
        """Decorator to track function performance"""
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not self._enabled:
                return await func(*args, **kwargs)
            
            start_time = time.time()
            success = True
            error = None
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                end_time = time.time()
                metric = PerformanceMetrics(
                    function_name=func.__name__,
                    start_time=start_time,
                    end_time=end_time,
                    duration=end_time - start_time,
                    success=success,
                    error=error
                )
                self.metrics.append(metric)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not self._enabled:
                return func(*args, **kwargs)
            
            start_time = time.time()
            success = True
            error = None
            
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error = str(e)
                raise
            finally:
                end_time = time.time()
                metric = PerformanceMetrics(
                    function_name=func.__name__,
                    start_time=start_time,
                    end_time=end_time,
                    duration=end_time - start_time,
                    success=success,
                    error=error
                )
                self.metrics.append(metric)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    def get_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        if not self.metrics:
            return {"total_calls": 0}
        
        by_function = {}
        for metric in self.metrics:
            if metric.function_name not in by_function:
                by_function[metric.function_name] = {
                    "calls": 0,
                    "total_duration": 0,
                    "min_duration": float('inf'),
                    "max_duration": 0,
                    "errors": 0
                }
            
            stats = by_function[metric.function_name]
            stats["calls"] += 1
            stats["total_duration"] += metric.duration
            stats["min_duration"] = min(stats["min_duration"], metric.duration)
            stats["max_duration"] = max(stats["max_duration"], metric.duration)
            if not metric.success:
                stats["errors"] += 1
        
        # Calculate averages
        for func_name, stats in by_function.items():
            stats["avg_duration"] = stats["total_duration"] / stats["calls"]
            stats["total_duration_ms"] = stats["total_duration"] * 1000
            stats["avg_duration_ms"] = stats["avg_duration"] * 1000
            stats["min_duration_ms"] = stats["min_duration"] * 1000
            stats["max_duration_ms"] = stats["max_duration"] * 1000
            del stats["total_duration"]
            del stats["avg_duration"]
            del stats["min_duration"]
            del stats["max_duration"]
        
        return {
            "total_calls": len(self.metrics),
            "by_function": by_function
        }
    
    def clear(self):
        """Clear all metrics"""
        self.metrics.clear()


class PluginCallTracer:
    """Trace plugin call chains for debugging"""
    
    def __init__(self):
        self.traces: List[CallTrace] = []
        self.current_trace: Optional[CallTrace] = None
        self._enabled = True
    
    @asynccontextmanager
    async def trace_call(self, plugin_name: str, method_name: str, context: Optional[RequestContext] = None):
        """Context manager to trace a plugin call"""
        if not self._enabled:
            yield
            return
        
        # Create trace entry
        trace = CallTrace(
            plugin_name=plugin_name,
            method_name=method_name,
            start_time=time.time(),
            context_before=self._extract_context(context) if context else None
        )
        
        # Link to parent trace if exists
        parent_trace = self.current_trace
        if parent_trace:
            parent_trace.children.append(trace)
        else:
            self.traces.append(trace)
        
        # Set as current trace
        self.current_trace = trace
        
        try:
            yield trace
            trace.end_time = time.time()
            trace.context_after = self._extract_context(context) if context else None
        except Exception as e:
            trace.end_time = time.time()
            trace.error = str(e)
            raise
        finally:
            # Restore parent trace
            self.current_trace = parent_trace
    
    def _extract_context(self, context: RequestContext) -> Dict[str, Any]:
        """Extract serializable context data"""
        return {
            "request_data": context.request_data,
            "response_data": context.response_data,
            "user_id": context.user_id,
            "is_short_circuited": context.is_short_circuited,
            "plugin_data_keys": list(context.plugin_data.keys())
        }
    
    def get_trace_tree(self) -> List[Dict[str, Any]]:
        """Get trace tree as dict"""
        return [trace.to_dict() for trace in self.traces]
    
    def clear(self):
        """Clear all traces"""
        self.traces.clear()
        self.current_trace = None


class MockPluginTester:
    """Test plugins with mock data and contexts"""
    
    @staticmethod
    def create_mock_context(
        request_data: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None
    ) -> RequestContext:
        """Create a mock request context for testing"""
        return RequestContext(
            request_data=request_data or {"test": True},
            user_id=user_id or "test_user"
        )
    
    @staticmethod
    async def test_plugin_handle(
        plugin: PluginInterface,
        request_data: Optional[Dict[str, Any]] = None,
        with_next: bool = True
    ) -> RequestContext:
        """Test a plugin's handle method"""
        context = MockPluginTester.create_mock_context(request_data)
        
        async def mock_next(ctx: RequestContext):
            ctx.response_data["next_called"] = True
        
        await plugin.handle(context, mock_next if with_next else None)
        
        return context
    
    @staticmethod
    async def test_plugin_chain(
        plugins: List[PluginInterface],
        request_data: Optional[Dict[str, Any]] = None
    ) -> RequestContext:
        """Test a chain of plugins"""
        context = MockPluginTester.create_mock_context(request_data)
        
        async def execute_chain(ctx: RequestContext, plugins_list: List[PluginInterface], index: int = 0):
            if index >= len(plugins_list):
                return
            
            plugin = plugins_list[index]
            
            async def next_plugin(c: RequestContext):
                await execute_chain(c, plugins_list, index + 1)
            
            await plugin.handle(ctx, next_plugin if index < len(plugins_list) - 1 else None)
        
        await execute_chain(context, plugins, 0)
        
        return context


def debug_context(context: RequestContext) -> str:
    """Pretty print a request context for debugging"""
    return json.dumps({
        "request_data": context.request_data,
        "response_data": context.response_data,
        "user_id": context.user_id,
        "plugin_data": {k: str(v)[:100] for k, v in context.plugin_data.items()},
        "is_short_circuited": context.is_short_circuited
    }, indent=2)


def profile_async(func: Callable) -> Callable:
    """Profile an async function and log performance"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = 0
        
        try:
            import psutil
            process = psutil.Process()
            start_memory = process.memory_info().rss / 1024 / 1024  # MB
        except:
            pass
        
        try:
            result = await func(*args, **kwargs)
            duration = time.time() - start_time
            
            end_memory = 0
            if start_memory:
                try:
                    end_memory = process.memory_info().rss / 1024 / 1024
                except:
                    pass
            
            logger.info(
                f"Profiled {func.__name__}",
                duration_ms=duration * 1000,
                memory_delta_mb=end_memory - start_memory if start_memory else 0
            )
            
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(
                f"Profiled {func.__name__} failed",
                duration_ms=duration * 1000,
                error=str(e)
            )
            raise
    
    return wrapper


class PluginDebugger:
    """Interactive plugin debugger"""
    
    def __init__(self):
        self.breakpoints: Dict[str, List[str]] = {}  # plugin_name -> method_names
        self.watch_expressions: List[str] = []
        self.step_mode = False
        
    def set_breakpoint(self, plugin_name: str, method_name: str):
        """Set a breakpoint on a plugin method"""
        if plugin_name not in self.breakpoints:
            self.breakpoints[plugin_name] = []
        self.breakpoints[plugin_name].append(method_name)
        
    def remove_breakpoint(self, plugin_name: str, method_name: str):
        """Remove a breakpoint"""
        if plugin_name in self.breakpoints:
            self.breakpoints[plugin_name].remove(method_name)
            
    def add_watch(self, expression: str):
        """Add a watch expression"""
        self.watch_expressions.append(expression)
        
    async def debug_hook(self, plugin_name: str, method_name: str, 
                        context: RequestContext, locals_dict: Dict[str, Any]):
        """Hook to be called at potential breakpoints"""
        if not self._should_break(plugin_name, method_name):
            return
            
        logger.info(f"Breakpoint hit: {plugin_name}.{method_name}")
        
        # Evaluate watch expressions
        for expr in self.watch_expressions:
            try:
                result = eval(expr, globals(), locals_dict)
                logger.info(f"Watch: {expr} = {result}")
            except Exception as e:
                logger.error(f"Watch error: {expr} - {e}")
        
        # Log context
        logger.info(f"Context:\n{debug_context(context)}")
        
        # In a real debugger, this would pause execution
        # For now, just log
        if self.step_mode:
            await asyncio.sleep(0.1)  # Simulate pause
    
    def _should_break(self, plugin_name: str, method_name: str) -> bool:
        """Check if should break at this point"""
        if self.step_mode:
            return True
        return plugin_name in self.breakpoints and method_name in self.breakpoints[plugin_name]


# Global instances
performance_tracker = PerformanceTracker()
call_tracer = PluginCallTracer()
plugin_debugger = PluginDebugger()


# Convenience decorators
track_performance = performance_tracker.track


def with_tracing(plugin_method: Callable) -> Callable:
    """Decorator to add tracing to a plugin method"""
    @wraps(plugin_method)
    async def wrapper(self, *args, **kwargs):
        plugin_name = self.get_metadata().name if hasattr(self, 'get_metadata') else 'unknown'
        
        async with call_tracer.trace_call(plugin_name, plugin_method.__name__):
            return await plugin_method(self, *args, **kwargs)
    
    return wrapper