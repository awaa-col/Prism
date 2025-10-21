"""
IPC communication optimizer for plugin system.
Uses MessagePack for efficient serialization and shared memory for large data.
"""

import asyncio
import pickle
import msgpack
import hashlib
import mmap
import os
import tempfile
from typing import Any, Dict, Optional, Tuple, Union
from dataclasses import dataclass
from multiprocessing import shared_memory
from multiprocessing.connection import Connection

from app.core.structured_logging import get_logger

logger = get_logger("plugin.ipc_optimizer")


@dataclass
class SharedMemoryInfo:
    """Information about shared memory segment"""
    name: str
    size: int
    checksum: str


class IPCOptimizer:
    """
    Optimizes IPC communication between main process and plugin processes.
    
    Features:
    - MessagePack for efficient serialization
    - Shared memory for large data transfers
    - Automatic protocol selection based on data size
    - Data integrity verification
    """
    
    # Threshold for using shared memory (1MB)
    SHARED_MEMORY_THRESHOLD = 1024 * 1024
    
    # Maximum message size for pipe communication (10MB)
    MAX_PIPE_MESSAGE_SIZE = 10 * 1024 * 1024
    
    def __init__(self):
        self.shared_memories: Dict[str, shared_memory.SharedMemory] = {}
        self._msgpack_available = True
        
        try:
            import msgpack
        except ImportError:
            logger.warning("MessagePack not available, falling back to pickle")
            self._msgpack_available = False
    
    def cleanup(self):
        """Cleanup all shared memory segments"""
        for shm in self.shared_memories.values():
            try:
                shm.close()
                shm.unlink()
            except:
                pass
        self.shared_memories.clear()
    
    async def send_optimized(self, conn: Connection, data: Any) -> None:
        """
        Send data using the most efficient method.
        
        Args:
            conn: Pipe connection
            data: Data to send
        """
        # Serialize data first to check size
        serialized = self._serialize(data)
        data_size = len(serialized)
        
        if data_size > self.SHARED_MEMORY_THRESHOLD:
            # Use shared memory for large data
            await self._send_via_shared_memory(conn, serialized)
        else:
            # Use direct pipe communication
            await self._send_via_pipe(conn, data)
    
    async def receive_optimized(self, conn: Connection) -> Any:
        """
        Receive data using the appropriate method.
        
        Args:
            conn: Pipe connection
            
        Returns:
            Deserialized data
        """
        # Wait for data with timeout
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        
        def check_data():
            if conn.poll():
                try:
                    message = conn.recv()
                    future.set_result(message)
                except Exception as e:
                    future.set_exception(e)
            else:
                loop.call_later(0.01, check_data)
        
        check_data()
        
        try:
            message = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            raise TimeoutError("IPC receive timeout")
        
        # Check if it's a shared memory reference
        if isinstance(message, dict) and message.get("type") == "shared_memory":
            return await self._receive_via_shared_memory(message)
        else:
            # Direct data
            return message
    
    def _serialize(self, data: Any) -> bytes:
        """Serialize data using the most efficient method"""
        if self._msgpack_available:
            try:
                # Try MessagePack first (more efficient)
                return msgpack.packb(data, use_bin_type=True)
            except (TypeError, ValueError):
                # Fall back to pickle for complex objects
                pass
        
        # Use pickle as fallback
        return pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
    
    def _deserialize(self, data: bytes) -> Any:
        """Deserialize data"""
        if self._msgpack_available:
            try:
                # Try MessagePack first
                return msgpack.unpackb(data, raw=False)
            except:
                # Fall back to pickle
                pass
        
        # Use pickle as fallback
        return pickle.loads(data)
    
    async def _send_via_pipe(self, conn: Connection, data: Any) -> None:
        """Send data directly through pipe"""
        try:
            conn.send(data)
        except Exception as e:
            logger.error(f"Failed to send via pipe: {e}")
            raise
    
    async def _send_via_shared_memory(self, conn: Connection, serialized: bytes) -> None:
        """Send large data via shared memory"""
        try:
            # Create shared memory
            shm = shared_memory.SharedMemory(create=True, size=len(serialized))
            
            # Copy data to shared memory
            shm.buf[:len(serialized)] = serialized
            
            # Calculate checksum for integrity
            checksum = hashlib.sha256(serialized).hexdigest()
            
            # Send reference through pipe
            reference = {
                "type": "shared_memory",
                "name": shm.name,
                "size": len(serialized),
                "checksum": checksum
            }
            
            conn.send(reference)
            
            # Store reference for cleanup
            self.shared_memories[shm.name] = shm
            
            logger.debug(f"Sent {len(serialized)} bytes via shared memory")
            
        except Exception as e:
            logger.error(f"Failed to send via shared memory: {e}")
            raise
    
    async def _receive_via_shared_memory(self, reference: Dict[str, Any]) -> Any:
        """Receive large data from shared memory"""
        shm = None
        try:
            # Access shared memory
            shm = shared_memory.SharedMemory(name=reference["name"])
            
            # Read data
            serialized = bytes(shm.buf[:reference["size"]])
            
            # Verify checksum
            checksum = hashlib.sha256(serialized).hexdigest()
            if checksum != reference["checksum"]:
                raise ValueError("Data corruption detected in shared memory transfer")
            
            # Deserialize
            data = self._deserialize(serialized)
            
            logger.debug(f"Received {reference['size']} bytes via shared memory")
            
            return data
            
        except Exception as e:
            logger.error(f"Failed to receive via shared memory: {e}")
            raise
        finally:
            # Clean up shared memory
            if shm:
                shm.close()
                try:
                    shm.unlink()
                except:
                    pass


class BatchedIPCOptimizer(IPCOptimizer):
    """
    Extended IPC optimizer with message batching support.
    
    Reduces communication overhead by batching multiple small messages.
    """
    
    def __init__(self, batch_size: int = 10, batch_timeout: float = 0.1):
        super().__init__()
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.pending_messages: list = []
        self._batch_lock = asyncio.Lock()
        self._batch_event = asyncio.Event()
        self._batch_task: Optional[asyncio.Task] = None
    
    async def start_batching(self, conn: Connection):
        """Start the batching task"""
        self._batch_task = asyncio.create_task(self._batch_sender(conn))
    
    async def stop_batching(self):
        """Stop the batching task"""
        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
    
    async def send_batched(self, data: Any) -> None:
        """Add message to batch"""
        async with self._batch_lock:
            self.pending_messages.append(data)
            
            if len(self.pending_messages) >= self.batch_size:
                self._batch_event.set()
    
    async def _batch_sender(self, conn: Connection):
        """Background task to send batched messages"""
        while True:
            try:
                # Wait for batch to fill or timeout
                try:
                    await asyncio.wait_for(
                        self._batch_event.wait(),
                        timeout=self.batch_timeout
                    )
                except asyncio.TimeoutError:
                    pass
                
                # Send pending messages
                async with self._batch_lock:
                    if self.pending_messages:
                        batch = self.pending_messages[:]
                        self.pending_messages.clear()
                        self._batch_event.clear()
                        
                        # Send as single message
                        await self.send_optimized(conn, {"batch": batch})
                        
                        logger.debug(f"Sent batch of {len(batch)} messages")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in batch sender: {e}")


# Global IPC optimizer instance
_ipc_optimizer: Optional[IPCOptimizer] = None


def get_ipc_optimizer() -> IPCOptimizer:
    """Get global IPC optimizer instance"""
    global _ipc_optimizer
    if _ipc_optimizer is None:
        _ipc_optimizer = IPCOptimizer()
    return _ipc_optimizer


def optimize_plugin_communication(func):
    """
    Decorator to optimize plugin communication.
    
    Automatically uses the IPC optimizer for send/receive operations.
    """
    async def wrapper(self, *args, **kwargs):
        # Inject IPC optimizer if not present
        if not hasattr(self, '_ipc_optimizer'):
            self._ipc_optimizer = get_ipc_optimizer()
        
        return await func(self, *args, **kwargs)
    
    return wrapper