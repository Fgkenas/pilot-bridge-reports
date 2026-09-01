"""
Shared utilities for BridgeReport.

Provides commonly used functions that were previously duplicated
across multiple checker and section files.
"""

from __future__ import annotations


def round_value(val, decimals: int = 3):
    """Round a value to specified decimals if it's numeric, otherwise return as-is."""
    try:
        return f"{float(val):.{decimals}f}"
    except (ValueError, TypeError):
        return val


def shorten_sensor_name(name: str) -> str:
    """Remove first two parts of sensor name (e.g., 'ATT-ATHA-AC-01' -> 'AC-01')."""
    if not name:
        return name
    parts = name.split("-")
    if len(parts) > 2:
        return "-".join(parts[2:])
    return name


def get_sensor_name(sensor: dict) -> str:
    """Extract sensor name from a sensor dict, trying multiple fields."""
    return (
        sensor.get("Code_name", "")
        or sensor.get("code_name", "")
        or sensor.get("name", "")
        or sensor.get("sensor_name", "")
        or sensor.get("sensor_code", "")
        or sensor.get("device_id", "")
    )
