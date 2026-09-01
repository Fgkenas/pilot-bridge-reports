# -*- coding: utf-8 -*-
"""
Shared rate limiter for Resensys API requests.

Import and call `rate_limit_request()` immediately before every
requests.post() call to enforce a minimum inter-request gap and cap
on concurrent in-flight requests.

Set MAX_API_WORKERS environment variable to override concurrent limit
(default 3). For parallel batch execution, set to 1 per process:
    set MAX_API_WORKERS=1
"""
from __future__ import annotations

import os
import threading
import time

_MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_API_WORKERS", "3"))
_request_semaphore = threading.Semaphore(_MAX_CONCURRENT_REQUESTS)
_last_request_time: float = 0.0
_request_lock = threading.Lock()
_MIN_REQUEST_INTERVAL = 1.0  # seconds between requests


def acquire_slot() -> None:
    """Block until a request slot is available, then enforce minimum gap."""
    _request_semaphore.acquire()


def release_slot() -> None:
    """Release the request slot after the call completes (or fails)."""
    _request_semaphore.release()


def wait_for_gap() -> None:
    """Enforce the minimum inter-request interval (call while holding slot)."""
    global _last_request_time
    with _request_lock:
        now = time.time()
        gap = now - _last_request_time
        if gap < _MIN_REQUEST_INTERVAL:
            time.sleep(_MIN_REQUEST_INTERVAL - gap)
        _last_request_time = time.time()
