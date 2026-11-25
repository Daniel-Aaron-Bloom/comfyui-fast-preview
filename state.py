"""Process-wide singletons, shared by the node and the routes."""

import concurrent.futures
import os

from . import store as store_mod
from .hub import Hub


def _mb(name, default):
    try:
        return int(float(os.environ[name]) * (1 << 20))
    except (KeyError, ValueError):
        return default


store = store_mod.Store(
    max_bytes=_mb("FAST_PREVIEW_CACHE_MB", store_mod.DEFAULT_MAX_BYTES),
    png_bytes=_mb("FAST_PREVIEW_PNG_CACHE_MB", store_mod.DEFAULT_PNG_BYTES),
)
hub = Hub()

# PIL and torch release the GIL while they work, so threads really do overlap.
pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(2, min(8, os.cpu_count() or 2)),
    thread_name_prefix="fastpreview",
)
