"""Performance optimization caching system for RPC operations.

This module provides intelligent caching for frequently accessed Houdini data,
reducing computation overhead and improving response times for repeated operations.
"""

import hashlib
import time
import threading
import weakref
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple, Union
from functools import wraps

try:
    import hou

    HOUDINI_AVAILABLE = True
except ImportError:
    HOUDINI_AVAILABLE = False


class CacheEntry:
    """Represents a single cache entry with metadata."""

    def __init__(
        self,
        value: Any,
        computation_time: float,
        dependencies: Dict[str, Any] = None,
    ):
        """Initialize cache entry.

        Args:
            value: Cached value
            computation_time: Time taken to compute the value
            dependencies: Dependencies that affect cache validity
        """
        self._value = value
        self._computation_time = computation_time
        self._dependencies = dependencies or {}
        self._created_time = time.time()
        self._access_count = 0
        self._last_access_time = time.time()

    @property
    def value(self) -> Any:
        """Get the cached value and update access statistics."""
        self._access_count += 1
        self._last_access_time = time.time()
        return self._value

    @property
    def age(self) -> float:
        """Get the age of the cache entry in seconds."""
        return time.time() - self._created_time

    @property
    def access_count(self) -> int:
        """Get the number of times this entry has been accessed."""
        return self._access_count

    @property
    def computation_time(self) -> float:
        """Get the original computation time."""
        return self._computation_time

    def is_valid(self, current_dependencies: Dict[str, Any] = None) -> bool:
        """Check if the cache entry is still valid based on dependencies.

        Args:
            current_dependencies: Current state of dependencies

        Returns:
            True if cache entry is still valid
        """
        if current_dependencies is None:
            current_dependencies = {}

        # Check if any dependencies have changed
        for key, expected_value in self._dependencies.items():
            if key not in current_dependencies:
                return False
            if current_dependencies[key] != expected_value:
                return False

        return True

    def get_info(self) -> Dict[str, Any]:
        """Get cache entry information."""
        return {
            "age": self.age,
            "access_count": self._access_count,
            "computation_time": self._computation_time,
            "created_time": self._created_time,
            "last_access_time": self._last_access_time,
            "dependencies": self._dependencies,
        }


class PerformanceCache:
    """High-performance cache with intelligent invalidation."""

    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0):
        """Initialize performance cache.

        Args:
            max_size: Maximum number of entries to cache
            default_ttl: Default time-to-live in seconds
        """
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0

        # Weak references to Houdini objects for dependency tracking
        self._node_refs: Dict[str, Any] = weakref.WeakValueDictionary()

        if HOUDINI_AVAILABLE:
            self._setup_houdini_tracking()

    def _setup_houdini_tracking(self):
        """Set up Houdini-specific cache invalidation."""
        # This would integrate with the callback system to invalidate
        # cache entries when nodes are modified
        pass

    def _generate_key(
        self, func_name: str, args: Tuple, kwargs: Dict[str, Any]
    ) -> str:
        """Generate a cache key from function arguments.

        Args:
            func_name: Name of the function
            args: Function positional arguments
            kwargs: Function keyword arguments

        Returns:
            Cache key string
        """
        # Create a deterministic string from the arguments
        key_data = {
            "func": func_name,
            "args": args,
            "kwargs": sorted(kwargs.items()),
        }

        # Hash the key data for consistent key generation
        key_str = str(key_data).encode("utf-8")
        return hashlib.sha256(key_str).hexdigest()

    def _get_node_dependencies(self, node_path: str) -> Dict[str, Any]:
        """Get dependencies for a node that affect cache validity.

        Args:
            node_path: Path to Houdini node

        Returns:
            Dictionary of dependencies
        """
        if not HOUDINI_AVAILABLE:
            return {}

        try:
            node = hou.node(node_path)
            if node is None:
                return {}

            # Track modification time and key properties
            dependencies = {
                "node_exists": True,
                "node_type": node.type().name(),
            }

            # For geometry nodes, track geometry modification
            if hasattr(node, "geometry") and node.geometry() is not None:
                geo = node.geometry()
                dependencies.update(
                    {
                        "point_count": len(geo.points()),
                        "prim_count": len(geo.prims()),
                        # Note: In a real implementation, you might use
                        # node.geometry().sopNode().modificationTime() if available
                    }
                )

            # Track parameter values for cache invalidation
            dependencies["param_hash"] = self._get_parameter_hash(node)

            return dependencies

        except Exception:
            return {"node_exists": False}

    def _get_parameter_hash(self, node) -> str:
        """Get a hash of relevant node parameters.

        Args:
            node: Houdini node

        Returns:
            Hash string of parameter values
        """
        try:
            # Get evaluatable parameters
            param_values = []
            for parm in node.parms():
                try:
                    if not parm.isMultiParmInstance():
                        param_values.append((parm.name(), parm.eval()))
                except:
                    pass

            param_str = str(sorted(param_values)).encode("utf-8")
            return hashlib.md5(param_str).hexdigest()

        except Exception:
            return "unknown"

    def _evict_lru(self):
        """Evict least recently used entries."""
        while len(self._cache) >= self._max_size:
            # Remove the oldest entry (FIFO when access times are equal)
            oldest_key, _ = self._cache.popitem(last=False)
            self._evictions += 1

    def get(
        self, key: str, current_dependencies: Dict[str, Any] = None
    ) -> Optional[Any]:
        """Get a value from the cache.

        Args:
            key: Cache key
            current_dependencies: Current state of dependencies

        Returns:
            Cached value or None if not found/invalid
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return None

            # Check TTL
            if entry.age > self._default_ttl:
                del self._cache[key]
                self._misses += 1
                return None

            # Check dependencies
            if not entry.is_valid(current_dependencies):
                del self._cache[key]
                self._misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self._hits += 1

            return entry.value

    def put(
        self,
        key: str,
        value: Any,
        computation_time: float,
        dependencies: Dict[str, Any] = None,
    ):
        """Put a value in the cache.

        Args:
            key: Cache key
            value: Value to cache
            computation_time: Time taken to compute the value
            dependencies: Dependencies that affect cache validity
        """
        with self._lock:
            # Evict old entries if necessary
            self._evict_lru()

            # Create cache entry
            entry = CacheEntry(value, computation_time, dependencies)
            self._cache[key] = entry

    def invalidate(self, pattern: str = None):
        """Invalidate cache entries.

        Args:
            pattern: Pattern to match keys for invalidation (None = all)
        """
        with self._lock:
            if pattern is None:
                # Clear all entries
                self._cache.clear()
            else:
                # Remove entries matching pattern
                keys_to_remove = [
                    key for key in self._cache.keys() if pattern in key
                ]
                for key in keys_to_remove:
                    del self._cache[key]

    def get_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics.

        Returns:
            Dictionary with cache statistics
        """
        with self._lock:
            total_requests = self._hits + self._misses
            hit_rate = (
                self._hits / total_requests if total_requests > 0 else 0.0
            )

            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": hit_rate,
                "evictions": self._evictions,
                "current_size": len(self._cache),
                "max_size": self._max_size,
                "total_requests": total_requests,
            }

    def clear(self):
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()


# Global cache instance
_performance_cache: Optional[PerformanceCache] = None


def get_performance_cache() -> PerformanceCache:
    """Get the global performance cache instance."""
    global _performance_cache
    if _performance_cache is None:
        _performance_cache = PerformanceCache()
    return _performance_cache


def cached_command(
    ttl: float = 300.0, cache_key_func: Optional[Callable] = None
):
    """Decorator for caching RPC command results.

    Args:
        ttl: Time-to-live in seconds
        cache_key_func: Optional function to generate custom cache keys

    Returns:
        Decorator function
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_performance_cache()

            # Generate cache key
            if cache_key_func:
                cache_key = cache_key_func(*args, **kwargs)
            else:
                cache_key = cache._generate_key(func.__name__, args, kwargs)

            # Get dependencies for nodes if applicable
            dependencies = {}
            if HOUDINI_AVAILABLE and "node_path" in kwargs:
                dependencies = cache._get_node_dependencies(kwargs["node_path"])

            # Try to get from cache
            cached_result = cache.get(cache_key, dependencies)
            if cached_result is not None:
                return cached_result

            # Compute result
            start_time = time.time()
            result = func(*args, **kwargs)
            computation_time = time.time() - start_time

            # Cache the result
            cache.put(cache_key, result, computation_time, dependencies)

            return result

        return wrapper

    return decorator


# RPC command integration for cache management
def register_cache_commands(registry):
    """Register cache management RPC commands."""

    @registry.register("cache.stats", "Get cache performance statistics")
    def get_cache_stats() -> Dict[str, Any]:
        """Get cache performance statistics."""
        cache = get_performance_cache()
        return cache.get_stats()

    @registry.register("cache.clear", "Clear cache entries")
    def clear_cache(pattern: str = None) -> Dict[str, Any]:
        """Clear cache entries.

        Args:
            pattern: Pattern to match for selective clearing

        Returns:
            Operation result
        """
        cache = get_performance_cache()
        old_size = len(cache._cache)
        cache.invalidate(pattern)
        new_size = len(cache._cache)

        return {
            "cleared_entries": old_size - new_size,
            "remaining_entries": new_size,
            "pattern": pattern,
        }

    @registry.register("cache.info", "Get detailed cache information")
    def get_cache_info() -> Dict[str, Any]:
        """Get detailed cache information including entry details."""
        cache = get_performance_cache()

        with cache._lock:
            entries_info = []
            for key, entry in cache._cache.items():
                entries_info.append(
                    {
                        "key": key[:32] + "..." if len(key) > 32 else key,
                        **entry.get_info(),
                    }
                )

            return {
                "stats": cache.get_stats(),
                "entries": entries_info,
                "config": {
                    "max_size": cache._max_size,
                    "default_ttl": cache._default_ttl,
                },
            }


# Apply caching to existing geometry analysis commands
if HOUDINI_AVAILABLE:

    def create_cached_geometry_commands():
        """Create cached versions of geometry analysis commands."""

        @cached_command(ttl=60.0)
        def cached_analyze_geometry(
            node_path: str,
            include_attributes: bool = True,
            include_groups: bool = True,
        ) -> Dict[str, Any]:
            """Cached version of geometry analysis."""
            # This would call the original analyze_geometry function
            # For brevity, not reimplementing the full function here
            pass

        return cached_analyze_geometry
