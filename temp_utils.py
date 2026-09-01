"""Temperature unit auto-detection and conversion to Celsius."""

from pathlib import Path

import pandas as pd

_NON_CELSIUS_LOG = Path(__file__).parent / "non_celsius_sensors.txt"


def _log_non_celsius(sensor_name: str, bridge_name: str, unit: str, low: float, high: float) -> None:
    """Append sensor to non_celsius_sensors.txt if not already listed."""
    existing: set[str] = set()
    if _NON_CELSIUS_LOG.exists():
        for line in _NON_CELSIUS_LOG.read_text(encoding="utf-8").splitlines():
            if " | " in line:
                existing.add(line.split(" | ")[0].strip())

    if sensor_name in existing:
        return

    with open(_NON_CELSIUS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{sensor_name} | {bridge_name} | {unit} | {low:.1f} | {high:.1f}\n")


def detect_and_convert_celsius(
    df: pd.DataFrame,
    *,
    sensor_name: str = "",
    bridge_name: str = "",
) -> tuple[pd.DataFrame, str]:
    """Detect temperature unit from value range and convert to Celsius.

    Detection rules:
        - values consistently > 200   → Kelvin   → subtract 273.15
        - values between 30 and 130   → Fahrenheit → (val-32)*5/9
        - otherwise                    → Celsius  → no change

    Returns (converted_df, unit_label) where unit_label is "K" or "F" or "C".
    Logs non-Celsius sensors to non_celsius_sensors.txt when bridge_name is provided.
    """
    if df is None or df.empty:
        return df, "C"

    vals = df["value"].dropna()
    if vals.empty:
        return df, "C"

    v_min = vals.min()
    v_max = vals.max()
    v_median = vals.median()

    unit = "C"
    result = df.copy()

    if v_min > 200:
        result["value"] = vals - 273.15
        unit = "K"
    elif 30 <= v_median <= 130 and (v_max - v_min) < 60:
        result["value"] = (vals - 32.0) * 5.0 / 9.0
        unit = "F"

    if unit != "C" and sensor_name and bridge_name:
        _log_non_celsius(sensor_name, bridge_name, unit, v_min, v_max)

    return result, unit
