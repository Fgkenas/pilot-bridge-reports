"""
issue_log.py
============
Per-run issue collector shared by run_bridge.py and run_region.py.

Same pattern as the monthly-report project's issue_logger:
clear_issues() at the start of a run, add_issue(type, **info) anywhere,
save_issues(path) at the end (writes an .xlsx). Issues are kept in one
module-level list, guarded by a lock because report generation uses
threads internally.
"""
from __future__ import annotations

import threading
from pathlib import Path

_lock = threading.Lock()
_issues: list[dict] = []


def clear_issues() -> None:
    with _lock:
        _issues.clear()


def add_issue(type_: str, **info) -> None:
    with _lock:
        _issues.append({"type": type_, **info})


def issues() -> list[dict]:
    with _lock:
        return [dict(i) for i in _issues]


def has_issues() -> bool:
    with _lock:
        return bool(_issues)


def save_issues(path: Path) -> Path | None:
    """Write the collected issues to an .xlsx. Returns None (and writes
    nothing) when there are no issues to report."""
    rows = issues()
    if not rows:
        return None
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Issues"

    headers = ["type"]
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path
