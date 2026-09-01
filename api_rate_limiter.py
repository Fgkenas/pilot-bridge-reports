# -*- coding: utf-8 -*-
"""
Shared rate limiter for Resensys API requests.

Call `acquire_slot()` / `wait_for_gap()` immediately before every
requests.post(), and `release_slot()` in a finally block. Two independent
limits apply:

  * an aggregate REQUEST RATE  -- at most one request start per
    MIN_REQUEST_INTERVAL seconds (env: API_MIN_REQUEST_INTERVAL)
  * a CONCURRENCY cap          -- at most N requests in flight at once
    (env: MAX_API_WORKERS)

Report generation is network-bound, so these two numbers set the wall-clock
cost of a run more than anything else here. Throughput is

    min(1 / MIN_REQUEST_INTERVAL, MAX_API_WORKERS / server_seconds)

so raising one alone is usually wasted: whichever term is not raised simply
becomes the binding one. Both defaults were raised together, on the project
owner's explicit instruction, accepting roughly double the load on Resensys.
Set the environment variables to go back to the gentler pair:

    MAX_API_WORKERS=3 API_MIN_REQUEST_INTERVAL=1.0

For parallel batch execution across processes, divide the budget: each process
should get MAX_API_WORKERS=1 so the machine as a whole stays within the cap.
The run_*.py batch scripts already do this and are unaffected by the new
defaults.
"""
from __future__ import annotations

import os
import threading
import time

MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_API_WORKERS", "4"))
MIN_REQUEST_INTERVAL = float(os.getenv("API_MIN_REQUEST_INTERVAL", "0.75"))

# Kept as private aliases: existing callers imported these names.
_MAX_CONCURRENT_REQUESTS = MAX_CONCURRENT_REQUESTS
_MIN_REQUEST_INTERVAL = MIN_REQUEST_INTERVAL

_request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
_next_slot_time: float = 0.0
_schedule_lock = threading.Lock()


def acquire_slot() -> None:
    """Block until a concurrency slot is free."""
    _request_semaphore.acquire()


def release_slot() -> None:
    """Release the concurrency slot after the call completes (or fails)."""
    _request_semaphore.release()


def wait_for_gap() -> None:
    """Block until this request is allowed to start.

    Reserves the next start time under a short lock and sleeps OUTSIDE it.
    The previous version slept while holding the lock, which serialised every
    request against every other: with a 1.0 s interval no two requests could
    overlap at all, so the concurrency cap above it was almost inert and
    adding workers changed nothing. Threads now queue for distinct start
    times and their sleeps run in parallel, so concurrency does what it says
    while the aggregate rate stays exactly as configured.
    """
    global _next_slot_time
    with _schedule_lock:
        now = time.monotonic()
        start_at = max(now, _next_slot_time)
        _next_slot_time = start_at + MIN_REQUEST_INTERVAL
    delay = start_at - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def describe() -> str:
    """One-line summary of the active limits and the throughput they imply."""
    rate = 1.0 / MIN_REQUEST_INTERVAL if MIN_REQUEST_INTERVAL > 0 else float("inf")
    return (
        f"[rate-limit] max_concurrent={MAX_CONCURRENT_REQUESTS} "
        f"min_interval={MIN_REQUEST_INTERVAL}s -> ceiling {rate:.2f} req/s "
        f"({rate * 60:.0f} req/min), whichever is lower against "
        f"concurrency/server_time"
    )


if __name__ == "__main__":
    print(describe())
