"""Shared counter for estimating data consumption from API response sizes."""
from __future__ import annotations

import threading

_total_bytes: int = 0
_lock = threading.Lock()

# JSON overhead correction factor (REST API responses are ~2.5x raw data size)
_JSON_OVERHEAD_FACTOR = 2.5


def add_bytes(n: int) -> None:
    global _total_bytes
    with _lock:
        _total_bytes += n


def get_total_mb() -> float:
    global _total_bytes
    with _lock:
        return _total_bytes / 1_000_000.0 / _JSON_OVERHEAD_FACTOR


def reset() -> None:
    global _total_bytes
    with _lock:
        _total_bytes = 0
