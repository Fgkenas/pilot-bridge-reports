"""
[DEPRECATED] Strain raw-vs-temperature-compensated chart generator.

This module is no longer used in the main pipeline.
Its functionality has been integrated into sensor_data.py which
fetches strain + temperature data in a single pass.

Adapted from greece-reporting/get_report_stats.py (plot_raw_vs_comp).
Kept for reference or standalone use only.
"""
from __future__ import annotations

from resensys_hosts import API_ROOT

import json
import os
from pathlib import Path

from api_rate_limiter import acquire_slot, release_slot, wait_for_gap

import api_bytes_tracker

from temp_utils import detect_and_convert_celsius, response_value_field

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import urllib3
import requests
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")
API_KEY = os.getenv("API_KEY")
RESENSYS_API_ROOT = API_ROOT

# Shared session for HTTP keep-alive (connection pooling)
_http_session = requests.Session()
_http_session.verify = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_unix_seconds(val) -> float:
    """Convert a UNIX timestamp (seconds, ms, or µs) into seconds."""
    s = str(val).strip()
    if not s or s.lower() in {"null", "none"}:
        raise ValueError("Empty/None timestamp")
    sec = float(s)
    if sec > 1e14:
        sec /= 1_000_000.0
    elif sec > 1e11:
        sec /= 1_000.0
    return sec


def _extract_xy(resp, *, time_key="Time", value_key=None):
    """Return list of series, each as list of [ts, y]."""
    if isinstance(resp, dict) and isinstance(resp.get("series"), list):
        out = []
        for s in resp["series"]:
            if isinstance(s, dict):
                if isinstance(s.get("data"), list):
                    out.append(s["data"])
                elif isinstance(s.get("values"), list):
                    out.append(s["values"])
        return out

    if isinstance(resp, dict) and ("data" in resp or "values" in resp):
        data = resp.get("data", resp.get("values"))
        return [data] if isinstance(data, list) else []

    if isinstance(resp, list):
        return [resp]

    if isinstance(resp, dict) and resp:
        vals = list(resp.values())
        if vals and isinstance(vals[0], dict) and any(time_key in v for v in vals):
            if value_key is None:
                sample = next((v for v in vals if isinstance(v, dict) and time_key in v), None)
                if sample is None:
                    return []
                value_key = next((k for k in sample.keys() if str(k).lower() != str(time_key).lower()), None)
                if value_key is None:
                    return []
            points = []
            for v in vals:
                if not isinstance(v, dict) or time_key not in v:
                    continue
                try:
                    ts_sec = _parse_unix_seconds(v[time_key])
                    y = v.get(value_key, None)
                    y = float(y) if y is not None else np.nan
                    points.append([ts_sec, y])
                except Exception:
                    continue
            if not points:
                return []
            points.sort(key=lambda p: p[0])
            return [points]
    return []


def _safe_float(val) -> float | None:
    """Convert a value to float, returning None if conversion fails or value is None."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _pack_as_series(series_list, series_names=None):
    series_names = series_names or [None] * len(series_list)
    out = {"series": []}
    for data, name in zip(series_list, series_names):
        s = {"data": data}
        if name:
            s["name"] = name
        out["series"].append(s)
    return out


# ---------------------------------------------------------------------------
# API data fetching
# ---------------------------------------------------------------------------

def _fetch_with_retry(url: str, payload: str, headers: dict,
                      max_attempts: int = 3, label: str = "") -> dict:
    """POST with exponential backoff after transient failures.
    Does NOT retry on client errors (4xx), HTTP 204, or empty responses.
    """
    import time
    import random
    sleep_time = 1
    last_exc: Exception | None = None
    acquire_slot()
    try:
        for attempt in range(max_attempts):
            try:
                wait_for_gap()
                resp = _http_session.post(url, headers=headers, data=payload, timeout=120, verify=False)
                api_bytes_tracker.add_bytes(len(resp.content))

                print(f"    ! {label} attempt {attempt + 1}/{max_attempts}: status={resp.status_code}, content_length={len(resp.content)}")

                if resp.status_code == 204:
                    print(f"    ! {label}: HTTP 204 (no data) — skipping retries")
                    return {"ok": False, "empty": True}

                if resp.status_code in (502, 503, 504):
                    raise requests.HTTPError(f"{resp.status_code} Server Error", response=resp)

                if resp.status_code >= 400 and resp.status_code < 500:
                    raise requests.HTTPError(
                        f"{resp.status_code} Client Error (not retryable)", response=resp
                    )

                try:
                    resp.raise_for_status()
                    data = resp.json()
                    if not data or data == []:
                        print(f"    ! {label}: empty response — skipping retries")
                        return {"ok": False, "empty": True}
                    return data
                except json.JSONDecodeError as json_exc:
                    print(f"    ! {label} JSON decode error: {json_exc}")
                    print(f"    ! {label} response text (first 500 chars): {resp.text[:500]}")
                    print(f"    ! {label} response headers: {dict(resp.headers)}")
                    raise json_exc

            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code < 500:
                    print(f"    ! {label} client error (not retryable): {exc}")
                    return {"ok": False, "error": str(exc)}
                last_exc = exc
                print(f"    ! {label} attempt {attempt + 1}/{max_attempts} failed: {exc}")
                if attempt < max_attempts - 1:
                    jitter = random.uniform(0.5, 1.5)
                    actual_sleep = sleep_time * jitter
                    print(f"    ! {label} retrying in {actual_sleep:.1f} seconds...")
                    time.sleep(actual_sleep)
                    sleep_time *= 2
            except Exception as exc:
                last_exc = exc
                print(f"    ! {label} attempt {attempt + 1}/{max_attempts} failed: {exc}")
                if attempt < max_attempts - 1:
                    jitter = random.uniform(0.5, 1.5)
                    actual_sleep = sleep_time * jitter
                    print(f"    ! {label} retrying in {actual_sleep:.1f} seconds...")
                    time.sleep(actual_sleep)
                    sleep_time *= 2
        print(f"    ! {label} giving up after {max_attempts} attempts")
        raise last_exc if last_exc else Exception("Unknown error")
    finally:
        release_slot()


def _fetch_strain_data(sid: str, did: str, t_start: str, t_end: str) -> dict:
    """Fetch raw strain data via Resensys REST API."""
    url = f"{RESENSYS_API_ROOT}/data_acquire"
    payload = json.dumps({
        "SID": sid,
        "DID": did,
        "DF": "32000",
        "U": "µε",
        "T_start": t_start,
        "T_end": t_end,
        "Sample_int": "H1",
    })
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    return _fetch_with_retry(url, payload, headers, label="strain")


def _fetch_temperature_data(sid: str, did: str, t_start: str, t_end: str) -> dict:
    """Fetch internal temperature data via Resensys REST API (Fahrenheit to match original)."""
    url = f"{RESENSYS_API_ROOT}/data_acquire"
    payload = json.dumps({
        "SID": sid,
        "DID": did,
        "DF": "3002",
        "U": "C",
        "T_start": t_start,
        "T_end": t_end,
    })
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    return _fetch_with_retry(url, payload, headers, label="temperature")


# ---------------------------------------------------------------------------
# Temperature compensation (Single-Baseline CTE method)
# ---------------------------------------------------------------------------

def medfilt(x, k):
    """Centered rolling-median filter (kernel length k; forced odd)."""
    if k % 2 == 0:
        k += 1
    return pd.Series(x).rolling(window=k, center=True, min_periods=1).median().to_numpy()


def pearson_corr(x, y):
    """Pearson correlation coefficient."""
    mask = ~(np.isnan(x) | np.isnan(y))
    c = np.corrcoef(np.asarray(x)[mask], np.asarray(y)[mask])
    return c[0, 1]


def fit_and_compensate_baseline(temp, strain_raw, ts_sec, DEBUG=False):
    """
    Fit a single CTE baseline on quiet windows, subtract it from the
    strain signal, and return the compensated strain.

    The "baseline" = the polyfit line itself. It is, by construction,
    the thermal component of the strain (apparent CTE * temperature).

    Fit on quiet windows only: bottom 20% of a 2-hr rolling strain std
    is treated as quasi-zero-load. This keeps daily traffic cycles that
    happen to correlate with time-of-day from biasing the slope.

    Parameters
    ----------
    temp : np.ndarray
        Temperature signal [°C].
    strain_raw : np.ndarray
        Raw strain signal.
    ts_sec : np.ndarray
        Timestamps in seconds.
    DEBUG : bool
        If True, print validation stats.

    Returns
    -------
    dict with keys: alpha, intercept, coeffs, baseline, eps_mech,
    is_quiet, activity
    """
    fs = 1.0 / np.median(np.diff(ts_sec))  # sample rate [Hz]
    
    eps_total = medfilt(strain_raw, 5)
    
    win = round(2 * 3600 * fs)  # half-window length in samples (matches MATLAB [w w])
    full_win = 2 * win + 1

    activity = (
        pd.Series(eps_total)
        .rolling(window=full_win, center=True, min_periods=1)
        .std()
        .to_numpy()
    )

    quiet_thr = np.nanquantile(activity, 0.20)  # bottom 20% = quiet
    is_quiet = activity < quiet_thr

    coeffs = np.polyfit(temp[is_quiet], eps_total[is_quiet], 1)
    alpha = coeffs[0]      # [ue/C] apparent CTE
    intercept = coeffs[1]  # [ue]  at T = 0 C

    print(f"  Quiet windows used  : {np.sum(is_quiet)} / {is_quiet.size} "
          f"({100 * np.mean(is_quiet):.1f}%)")
    print(f"  Fitted alpha (CTE)  : {alpha:.3f} ue/C")

    baseline = alpha * temp + intercept   # the polyfit line
    eps_mech = eps_total - baseline

    if DEBUG:
        print("\n--- Correlation with Temperature ---")
        print(f"  Raw strain  : {pearson_corr(temp, eps_total):+.4f}")
        print(f"  Residual    : {pearson_corr(temp, eps_mech):+.4f}")

    return {
        "alpha": alpha,
        "intercept": intercept,
        "coeffs": coeffs,
        "baseline": baseline,
        "eps_mech": eps_mech,
        "is_quiet": is_quiet,
        "activity": activity,
    }

def _temperature_compensate_strain(
    strain_resp,
    temp_resp,
    *,
    tolerance="2h",
    min_points=20,
    e0: float | None = None,
    t0: float | None = None,
    DEBUG=False,
    sensor_name: str = "",
    bridge_name: str = "",
):
    """
    Pair strain and temperature by timestamp, apply single-baseline CTE
    compensation (quiet window method), and return a comp_resp dict with
    two series (raw_paired, temp_compensated) plus fit_info metadata.

    If e0 is provided it is subtracted from raw strain before compensation.
    """
    s_list = _extract_xy(strain_resp, time_key="Time")
    t_list = _extract_xy(temp_resp, time_key="Time")

    if not s_list or not t_list:
        return None, {"ok": False, "reason": "Missing strain or temperature series"}

    s = pd.DataFrame(s_list[0], columns=["ts_sec", "strain"])
    t = pd.DataFrame(t_list[0], columns=["ts_sec", "temp"])

    s["ts_sec"] = pd.to_numeric(s["ts_sec"], errors="coerce")
    t["ts_sec"] = pd.to_numeric(t["ts_sec"], errors="coerce")
    s["strain"] = pd.to_numeric(s["strain"], errors="coerce")
    t["temp"] = pd.to_numeric(t["temp"], errors="coerce")

    s = s.dropna().sort_values("ts_sec")
    t = t.dropna().sort_values("ts_sec")

    if s.empty or t.empty:
        return None, {"ok": False, "reason": "No valid numeric data after cleaning"}

    t_renamed = t.rename(columns={"temp": "value"})
    t_renamed, _ = detect_and_convert_celsius(
        t_renamed,
        sensor_name=sensor_name,
        bridge_name=bridge_name,
        value_field=response_value_field(temp_resp),
    )
    t["temp"] = t_renamed["value"]

    # Subtract initial strain offset (ε0) before fitting and compensation
    if e0 is not None:
        s["strain"] = s["strain"] - e0

    tol = pd.Timedelta(tolerance).total_seconds()
    merged = pd.merge_asof(s, t, on="ts_sec", direction="nearest", tolerance=tol).dropna(subset=["temp"])

    n_pairs = len(merged)
    if n_pairs < min_points:
        return None, {"ok": False, "reason": f"Too few paired points ({n_pairs}<{min_points})", "pairs": n_pairs}

    strain_vals = merged["strain"].to_numpy(float)
    temp_vals = merged["temp"].to_numpy(float)
    ts_sec_vals = merged["ts_sec"].to_numpy(float)

    # Apply single-baseline CTE compensation (quiet window method)
    result = fit_and_compensate_baseline(temp_vals, strain_vals, ts_sec_vals, DEBUG=DEBUG)
    
    eps_mech = result["eps_mech"]
    baseline = result["baseline"]
    alpha = result["alpha"]
    intercept = result["intercept"]

    ts_iso = pd.to_datetime(merged["ts_sec"], unit="s", utc=True).astype(str).to_list()

    raw_paired = [[ts, float(y)] for ts, y in zip(ts_iso, strain_vals)]
    comp_paired = [[ts, float(y)] for ts, y in zip(ts_iso, eps_mech)]

    out = _pack_as_series(
        [raw_paired, comp_paired],
        series_names=["strain_raw_paired", "strain_temp_comp"],
    )

    info = {
        "ok": True,
        "pairs": int(n_pairs),
        "alpha_cte": float(alpha),
        "intercept": float(intercept),
        "e0": float(e0) if e0 is not None else None,
        "t0": float(t0) if t0 is not None else None,
        "tolerance": str(tolerance),
        "eps_mech": eps_mech,
    }
    return out, info


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _shorten_sensor_name(name: str) -> str:
    """Convert full sensor name to short form (e.g., ATT-ATHA-SG-01 -> SG-01)."""
    if not name:
        return name
    # Take last segment after splitting by '-' or '_'
    for sep in ["-", "_"]:
        if sep in name:
            parts = name.split(sep)
            # Return last 2 parts if they look like type-number (e.g., SG-01)
            if len(parts) >= 2:
                return f"{parts[-2]}{sep}{parts[-1]}"
    return name


def _plot_raw_vs_comp(
    strain_resp,
    comp_resp,
    out_dir,
    output_file,
    title="ε_mechanical",
    e0: float | None = None,
    sensor_name: str = "",
    eps_mech: np.ndarray | None = None,
):
    """
    Plot the mechanical residual over time (Panel 4 of the MATLAB-equivalent
    diagnostic): ε_mechanical = ε_total - baseline.

    Two traces: the raw strain (centered on its mean, gray) and the
    temperature-compensated mechanical strain (green). Save to file.
    Returns the output path, or None on failure.
    """
    series_list = _extract_xy(comp_resp)
    if len(series_list) < 2:
        print(f"    ! comp_resp should contain two series: raw and compensated")
        return None

    if eps_mech is None:
        print(f"    ! eps_mech required for mechanical-residual plot")
        return None

    bg, fg, grid = "#ffffff", "#111827", "#e5e7eb"
    raw_color = [0.6, 0.6, 0.6]
    mech_color = [0.1, 0.55, 0.3]

    raw = pd.DataFrame(series_list[0], columns=["ts", "y_raw"])
    cmp = pd.DataFrame(series_list[1], columns=["ts", "y_cmp"])

    raw["ts"] = pd.to_datetime(raw["ts"], errors="coerce", utc=True)
    cmp["ts"] = pd.to_datetime(cmp["ts"], errors="coerce", utc=True)
    raw["y_raw"] = pd.to_numeric(raw["y_raw"], errors="coerce")
    cmp["y_cmp"] = pd.to_numeric(cmp["y_cmp"], errors="coerce")

    df = raw.merge(cmp, on="ts", how="inner").dropna().sort_values("ts")

    eps_mech = np.asarray(eps_mech, dtype=float)
    if len(eps_mech) != len(df):
        print(f"    ! eps_mech length ({len(eps_mech)}) != paired data ({len(df)})")
        print(f"    ! Trimming eps_mech to match paired data length")
        eps_mech = eps_mech[:len(df)]

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, output_file)

    # Set default font to Arial 9pt
    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["font.size"] = 9

    # Single panel: mechanical residual (ε_mechanical = ε_total - baseline)
    fig, ax = plt.subplots(1, 1, figsize=(12, 5), dpi=150)

    raw_centered = df["y_raw"].to_numpy() - np.mean(df["y_raw"].to_numpy())
    ax.plot(df["ts"], raw_centered, linewidth=1, color=raw_color,
            label="raw (zero-meaned)")
    ax.plot(df["ts"], eps_mech, linewidth=2, color=mech_color,
            label="Διορθωμένο (ε_mechanical)")

    ax.set_title("ε_mechanical = ε_total − baseline (με)", fontsize=10, loc="left")
    ax.set_xlabel("Χρόνος", fontsize=14)
    ax.set_ylabel("Παραμόρφωση (με)", fontsize=14)

    # Add frame (box) around the plot
    for spine in ax.spines.values():
        spine.set_edgecolor(grid)
        spine.set_linewidth(1.2)

    ax.grid(True, axis="y", linewidth=0.8, alpha=0.5, color=grid)
    ax.grid(True, axis="x", which="major", linewidth=0.6, alpha=0.25, color=grid)

    ax.tick_params(axis="x", colors=fg, labelsize=12)
    ax.tick_params(axis="y", colors=fg, labelsize=12)
    ax.legend(facecolor=bg, edgecolor=grid, framealpha=0.95, loc="best", fontsize=12)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor=bg)
    plt.close(fig)

    return out_path


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_strain_comp_charts(
    sensors: list[dict],
    graphs_dir: Path,
    pilot_start: str,
    pilot_end: str,
) -> tuple[list[str], dict]:
    """
    For each SG (strain) sensor, fetch strain + temperature via API,
    temperature-compensate, and produce a raw-vs-comp chart.

    Also extracts the measurement closest to 14:00 on the last pilot day
    for use in the strain quality / temperature tables.

    Args:
        sensors: list of sensor dicts (must have 'type', 'device_id', 'site_id',
                 and one of 'Code_name'/'code_name' for labelling).
        graphs_dir: directory to save chart PNGs.
        pilot_start: start date string for API, e.g. "2025-06-06".
        pilot_end: end date string for API, e.g. "2025-08-06".

    Returns:
        tuple of (list of saved chart file paths, dict of 14:00 values per sensor).
        The 14:00 values dict has the form {sensor_name: {"strain": float, "temperature": float, "timestamp": str}}.
    """
    if not API_KEY:
        print("  ! strain_comp_charts: API_KEY not set, skipping")
        return [], {}

    saved = []
    near_14h_values: dict = {}
    strain_sensors = [
        s for s in sensors
        if "strain" in s.get("type", "").lower()
    ]

    if not strain_sensors:
        print("  ! strain_comp_charts: no SG (strain) sensors found")
        return [], {}

    # Format dates for API (expects "YYYY-MM-DD HH:MM:SS")
    t_start = f"{pilot_start} 00:00:00" if " " not in pilot_start else pilot_start
    t_end = f"{pilot_end} 23:59:59" if " " not in pilot_end else pilot_end

    # Target datetime for 14:00 extraction: last pilot day at 14:00
    from datetime import datetime as _dt, timedelta as _td
    _pilot_end_dt = _dt.strptime(pilot_end.split(" ")[0] if " " in pilot_end else pilot_end, "%Y-%m-%d")
    _target_14h = _pilot_end_dt.replace(hour=14, minute=0, second=0)
    _target_14h_ts = _target_14h.timestamp()

    for sensor in strain_sensors:
        did = sensor.get("device_id", "")
        sid = sensor.get("site_id", "")
        name = (
            sensor.get("Code_name", "")
            or sensor.get("code_name", "")
            or did
        )
        if not did or not sid:
            print(f"    ! skipping {name}: missing device_id or site_id")
            continue
        
        # Skip sensors with NO DATA status
        sensor_status = sensor.get("sensor_status", "").strip().upper()
        if sensor_status == "NO DATA":
            print(f"    ! skipping {name}: sensor_status='NO DATA'")
            continue

        print(f"  Strain comp chart: {name} (DID={did}, SID={sid})")
        print(f"    Sensor keys: {list(sensor.keys())}")
        print(f"    Code_name: {sensor.get('Code_name')}")
        print(f"    code_name: {sensor.get('code_name')}")

        try:
            strain_resp = _fetch_strain_data(sid, did, t_start, t_end)
        except Exception as e:
            print(f"    ! strain fetch failed: {e}")
            continue

        try:
            temp_resp = _fetch_temperature_data(sid, did, t_start, t_end)
        except Exception as e:
            print(f"    ! temperature fetch failed: {e}")
            continue

        e0 = _safe_float(sensor.get("e0"))
        print(f"    e0={e0}")

        comp_resp, fit_info = _temperature_compensate_strain(
            strain_resp, temp_resp, e0=e0,
            sensor_name=name,
            bridge_name=graphs_dir.name,
        )
        if comp_resp is None:
            print(f"    ! temp compensation failed: {fit_info}")
            continue
        print(f"    Temp compensation fit: {fit_info}")

        # Extract values closest to 14:00 on the last pilot day
        try:
            s_list = _extract_xy(strain_resp, time_key="Time")
            t_list_raw = _extract_xy(temp_resp, time_key="Time")
            if s_list and t_list_raw:
                s_df = pd.DataFrame(s_list[0], columns=["ts_sec", "strain"])
                t_df = pd.DataFrame(t_list_raw[0], columns=["ts_sec", "temp"])
                s_df["ts_sec"] = pd.to_numeric(s_df["ts_sec"], errors="coerce")
                t_df["ts_sec"] = pd.to_numeric(t_df["ts_sec"], errors="coerce")
                _last_day = _target_14h_ts - 86400
                # Only consider points on the last pilot day
                s_last = s_df[s_df["ts_sec"] >= _last_day]
                t_last = t_df[t_df["ts_sec"] >= _last_day]
                if not s_last.empty and not t_last.empty:
                    s_last = s_last.copy()
                    s_last["dist"] = (s_last["ts_sec"] - _target_14h_ts).abs()
                    t_last = t_last.copy()
                    t_last["dist"] = (t_last["ts_sec"] - _target_14h_ts).abs()
                    s_nearest = s_last.loc[s_last["dist"].idxmin()]
                    t_nearest = t_last.loc[t_last["dist"].idxmin()]
                    _ts_dt = _dt.utcfromtimestamp(s_nearest["ts_sec"]).strftime("%Y-%m-%d %H:%M:%S") if s_nearest["ts_sec"] > 1e10 else _dt.utcfromtimestamp(s_nearest["ts_sec"]).strftime("%Y-%m-%d %H:%M:%S")
                    near_14h_values[name] = {
                        "strain": round(float(s_nearest["strain"]), 3),
                        "temperature": round(float(t_nearest["temp"]), 3),
                        "timestamp": _ts_dt,
                    }
        except Exception as e14:
            print(f"    ! 14:00 extraction failed for {name}: {e14}")

        filename = f"{name.strip()}_strain_comp.png"
        print(f"    Target filename: {filename}")
        eps_mech = fit_info.get("eps_mech")
        out = _plot_raw_vs_comp(strain_resp, comp_resp, str(graphs_dir), filename, e0=e0, sensor_name=name, eps_mech=eps_mech)
        if out:
            saved.append(out)
            print(f"    Saved: {out}")
        else:
            print(f"    ! Plot returned None")

    return saved, near_14h_values


# ---------------------------------------------------------------------------
# Demo / standalone test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("DEBUG: Starting demo...")
    from bridge_info import fetch_bridge

    # --- Demo config: adjust as needed ---
    DEMO_CODE_NAME = "ΑΤΤ_003_ΑΘΗ"
    DEMO_PILOT_START = "2025-11-15"
    DEMO_PILOT_END = "2026-01-15"
    DEMO_OUTPUT_DIR = Path("graphs") / "demo_strain_comp"

    print(f"=== Strain Comp Charts Demo ===")
    print(f"Bridge: {DEMO_CODE_NAME}")
    print(f"Date range: {DEMO_PILOT_START} -> {DEMO_PILOT_END}")
    print(f"Output dir: {DEMO_OUTPUT_DIR}\n")

    # Fetch sensors from bridge API
    print("DEBUG: Fetching bridge data...")
    bridge_data = fetch_bridge(DEMO_CODE_NAME)
    print(f"DEBUG: Got bridge data: {type(bridge_data)}")
    sensors = []
    for region in bridge_data.get("regions", []):
        for bridge in region.get("bridges", []):
            for site in bridge.get("sites", []):
                for sensor in site.get("sensors", []):
                    sensor["site_id"] = site.get("site_id")
                    sensors.append(sensor)

    strain_sensors = [s for s in sensors if "strain" in s.get("type", "").lower()]
    print(f"Found {len(sensors)} total sensors, {len(strain_sensors)} strain (SG):")
    for s in strain_sensors:
        print(f"  - {s.get('Code_name', s.get('device_id'))} "
              f"(DID={s.get('device_id')}, SID={s.get('site_id')})")

    if not strain_sensors:
        print("No strain sensors found. Nothing to test.")
    else:
        print(f"DEBUG: Calling generate_strain_comp_charts with {len(strain_sensors)} sensors...")
        results = generate_strain_comp_charts(
            sensors, DEMO_OUTPUT_DIR, DEMO_PILOT_START, DEMO_PILOT_END
        )
        print(f"\n=== Done: {len(results)} charts saved ===")
