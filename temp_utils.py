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


def response_value_field(resp, *, time_key: str = "Time") -> str:
    """The API's own name for the value field of a /data_acquire response.

    Every reading is labelled with the quantity AND its unit, e.g.
    "Internal Temperature(F)". That is the authoritative answer to "what
    unit is this?", so pass it to detect_and_convert_celsius rather than
    letting it fall back to inferring one.
    """
    rows = None
    if isinstance(resp, dict):
        if isinstance(resp.get("data"), list):
            rows = resp["data"]
        elif isinstance(resp.get("values"), list):
            rows = resp["values"]
        elif isinstance(resp.get("series"), list):
            for series in resp["series"]:
                if isinstance(series, dict):
                    rows = series.get("data") or series.get("values")
                    if rows:
                        break
        else:
            rows = list(resp.values())
    elif isinstance(resp, list):
        rows = resp

    sample = next((r for r in (rows or []) if isinstance(r, dict)), None)
    if not sample:
        return ""
    return next((str(k) for k in sample
                 if str(k).lower() != time_key.lower()), "")


def _unit_from_field(field: str) -> str:
    """"Internal Temperature(F)" -> "F". "" when the name carries no unit."""
    if not field or "(" not in field or ")" not in field:
        return ""
    return field[field.rfind("(") + 1:field.rfind(")")].strip().upper()


def detect_and_convert_celsius(
    df: pd.DataFrame,
    *,
    sensor_name: str = "",
    bridge_name: str = "",
    value_field: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Convert a temperature frame to Celsius.

    Pass `value_field` -- the API's own field name, from
    response_value_field() -- and the unit is read straight off it. DF=3002
    always answers in Fahrenheit, in a field named "Internal Temperature(F)",
    whatever unit the request asks for (verified against both v3 hosts with
    no unit, U=C and Unit=C: identical responses). So the unit is never in
    doubt when the caller can supply that name.

    Without it, this falls back to the old magnitude test, which is a guess:
    a genuine 35 C reading (a sensor in Greek summer sun) sits inside the
    same 30..130 band as 35 F and would be "converted" to 1.7 C. The
    fallback now says so out loud instead of doing it silently, because the
    value feeds thermal compensation and a wrong unit propagates into the
    alpha quoted in the report. Supply value_field wherever you can.

    Returns (converted_df, unit_label) where unit_label is the unit the data
    ARRIVED in ("K", "F" or "C"), not the unit it leaves in.
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
    declared = _unit_from_field(value_field or "")

    if declared:
        if declared == "F":
            result["value"] = (vals - 32.0) * 5.0 / 9.0
            unit = "F"
        elif declared == "K":
            result["value"] = vals - 273.15
            unit = "K"
        # declared == "C" (or anything else): already Celsius, leave it.
    else:
        # Legacy fallback -- a guess, and now a visible one.
        if v_min > 200:
            result["value"] = vals - 273.15
            unit = "K"
        elif 30 <= v_median <= 130 and (v_max - v_min) < 60:
            result["value"] = (vals - 32.0) * 5.0 / 9.0
            unit = "F"
        if unit != "C":
            print(f"      ! Temperature unit for {sensor_name or 'sensor'} not declared "
                  f"by the API; INFERRED {unit} from the value range "
                  f"({v_min:.1f}..{v_max:.1f}). Pass value_field to make this exact.")

    if unit != "C" and sensor_name and bridge_name:
        _log_non_celsius(sensor_name, bridge_name, unit, v_min, v_max)

    return result, unit
