"""
Unified sensor data fetching via REST API.

Replaces the duplicate AJAX-based fetchers (accel_fetcher.py,
deformation_report_section.py) with a single REST-based pipeline
that fetches strain, temperature, and acceleration data once and
produces all required charts, CSVs, and metadata.

Usage:
    from sensor_data import process_all_sensor_data
    result = process_all_sensor_data(sensors, graphs_dir, pilot_start, pilot_end)
"""

from __future__ import annotations

import os
from datetime import datetime as _dt
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, AutoMinorLocator
import numpy as np
import pandas as pd

from api_rate_limiter import acquire_slot, release_slot, wait_for_gap
import api_bytes_tracker

from dotenv import load_dotenv

secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")
API_KEY = os.getenv("API_KEY")

RESENSYS_API_ROOT = "https://greece-api.resensys.cloud/api/v3"

from temp_utils import detect_and_convert_celsius

# ---------------------------------------------------------------------------
# Shared HTTP session
# ---------------------------------------------------------------------------
_http_session = None

def _get_session():
    global _http_session
    if _http_session is None:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _http_session = requests.Session()
        _http_session.verify = False
    return _http_session


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _fetch_with_retry(url: str, payload: str, headers: dict,
                      max_attempts: int = 3, label: str = "") -> dict:
    import time
    import random
    sleep_time = 1
    last_exc = None
    session = _get_session()
    acquire_slot()
    try:
        for attempt in range(max_attempts):
            try:
                wait_for_gap()
                resp = session.post(url, headers=headers, data=payload, timeout=120, verify=False)
                api_bytes_tracker.add_bytes(len(resp.content))
                if resp.status_code == 204:
                    print(f"    [API] HTTP 204 (No Content) for {label}")
                    return {"ok": False, "empty": True}
                if resp.status_code in (502, 503, 504):
                    raise Exception(f"{resp.status_code} Server Error")
                if 400 <= resp.status_code < 500:
                    print(f"    [API] HTTP {resp.status_code} for {label}")
                    return {"ok": False, "error": f"HTTP {resp.status_code}"}
                resp.raise_for_status()
                data = resp.json()
                if not data or data == []:
                    return {"ok": False, "empty": True}
                return data
            except Exception as e:
                last_exc = e
                if attempt < max_attempts - 1:
                    jitter = random.uniform(0.5, 1.5)
                    time.sleep(sleep_time * jitter)
                    sleep_time *= 2
        raise last_exc if last_exc else Exception("Unknown error")
    finally:
        release_slot()


def _fetch_strain_data(sid: str, did: str, t_start: str, t_end: str) -> dict:
    import json
    url = f"{RESENSYS_API_ROOT}/data_acquire"
    payload = json.dumps({
        "SID": sid, "DID": did, "DF": "32000", "U": "\u00b5\u03b5",
        "T_start": t_start, "T_end": t_end, "Sample_int": "H1",
    })
    return _fetch_with_retry(url, payload,
                             {"X-API-Key": API_KEY, "Content-Type": "application/json"},
                             label="strain")


def _fetch_temperature_data(sid: str, did: str, t_start: str, t_end: str) -> dict:
    import json
    url = f"{RESENSYS_API_ROOT}/data_acquire"
    payload = json.dumps({
        "SID": sid, "DID": did, "DF": "3002", "U": "C",
        "T_start": t_start, "T_end": t_end,
    })
    return _fetch_with_retry(url, payload,
                             {"X-API-Key": API_KEY, "Content-Type": "application/json"},
                             label="temperature")


def _fetch_hpa_data(sid: str, did: str, t_start: str, t_end: str, axis: str = "HPA-Z") -> dict:
    import json
    url = f"{RESENSYS_API_ROOT}/data_acquire"
    payload = json.dumps({
        "SID": sid, "DID": did, "DF": "33000", "Q": axis, "U": "mg",
        "T_start": t_start, "T_end": t_end, "Sample_int": "H1",
    })
    return _fetch_with_retry(url, payload,
                             {"X-API-Key": API_KEY, "Content-Type": "application/json"},
                             label=axis)


# ---------------------------------------------------------------------------
# Data parsing helpers
# ---------------------------------------------------------------------------

def _parse_unix_seconds(val) -> float:
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
    def _dicts_to_points(items):
        if not items or not isinstance(items[0], dict):
            return items
        sample = items[0]
        if time_key not in sample:
            return items
        vk = value_key
        if vk is None:
            vk = next((k for k in sample.keys() if str(k).lower() != str(time_key).lower()), None)
            if vk is None:
                return items
        points = []
        for v in items:
            if not isinstance(v, dict) or time_key not in v:
                continue
            try:
                ts_sec = _parse_unix_seconds(v[time_key])
                y = v.get(vk, None)
                y = float(y) if y is not None else np.nan
                points.append([ts_sec, y])
            except Exception:
                continue
        if not points:
            return []
        points.sort(key=lambda p: p[0])
        return points

    if isinstance(resp, dict) and isinstance(resp.get("series"), list):
        out = []
        for s in resp["series"]:
            if isinstance(s, dict):
                if isinstance(s.get("data"), list):
                    out.append(_dicts_to_points(s["data"]))
                elif isinstance(s.get("values"), list):
                    out.append(_dicts_to_points(s["values"]))
        return out
    if isinstance(resp, dict) and ("data" in resp or "values" in resp):
        data = resp.get("data", resp.get("values"))
        return [_dicts_to_points(data)] if isinstance(data, list) else []
    if isinstance(resp, list):
        return [_dicts_to_points(resp)]
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


def _parse_hpa_to_df(resp: dict) -> pd.DataFrame | None:
    if not resp:
        return None
    if isinstance(resp, list):
        rows = resp
    elif isinstance(resp, dict):
        if isinstance(resp.get("series"), list):
            series = resp["series"]
            if series and isinstance(series[0].get("data"), list):
                rows = series[0]["data"]
            else:
                return None
        elif "data" in resp:
            rows = resp["data"]
        elif "values" in resp:
            rows = resp["values"]
        else:
            inner = list(resp.values())
            if inner and isinstance(inner[0], dict) and "Time" in inner[0]:
                rows = inner
            else:
                return None
    else:
        return None
    if not rows:
        return None
    sample = rows[0]
    if isinstance(sample, (list, tuple)):
        df = pd.DataFrame(rows, columns=["Time", "Value"])
    elif isinstance(sample, dict):
        time_key = next((k for k in sample if str(k).lower() == "time"), None)
        val_key = next((k for k in sample if str(k).lower() not in ("time",)), None)
        if not time_key or not val_key:
            return None
        df = pd.DataFrame(rows)
        df = df.rename(columns={time_key: "Time", val_key: "Value"})
    else:
        return None
    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
    try:
        ts_numeric = pd.to_numeric(df["Time"], errors="coerce")
        if ts_numeric.notna().all():
            ts = ts_numeric.astype(float)
            if (ts > 1e14).any():
                ts = ts / 1_000_000.0
            elif (ts > 1e11).any():
                ts = ts / 1_000.0
            df["Time"] = pd.to_datetime(ts, unit="s", utc=True)
        else:
            df["Time"] = pd.to_datetime(df["Time"], errors="coerce", utc=True)
    except Exception:
        df["Time"] = pd.to_datetime(df["Time"], errors="coerce", utc=True)
    df = df.dropna(subset=["Time", "Value"]).sort_values("Time").reset_index(drop=True)
    return df if not df.empty else None


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _shorten_sensor_name(name: str) -> str:
    if not name:
        return name
    for sep in ["-", "_"]:
        if sep in name:
            parts = name.split(sep)
            if len(parts) >= 2:
                return f"{parts[-2]}{sep}{parts[-1]}"
    return name


# ---------------------------------------------------------------------------
# Temperature compensation (from strain_comp_charts.py)
# ---------------------------------------------------------------------------

def _medfilt(x, k):
    if k % 2 == 0:
        k += 1
    return pd.Series(x).rolling(window=k, center=True, min_periods=1).median().to_numpy()


def _fit_and_compensate_baseline(temp, strain_raw, ts_sec):
    fs = 1.0 / np.median(np.diff(ts_sec))
    eps_total = _medfilt(strain_raw, 5)
    win = round(2 * 3600 * fs)
    full_win = 2 * win + 1
    activity = (
        pd.Series(eps_total)
        .rolling(window=full_win, center=True, min_periods=1)
        .std()
        .to_numpy()
    )
    quiet_thr = np.nanquantile(activity, 0.20)
    is_quiet = activity < quiet_thr
    coeffs = np.polyfit(temp[is_quiet], eps_total[is_quiet], 1)
    alpha = coeffs[0]
    intercept = coeffs[1]
    baseline = alpha * temp + intercept
    eps_mech = eps_total - baseline
    return {"alpha": alpha, "intercept": intercept, "coeffs": coeffs,
            "baseline": baseline, "eps_mech": eps_mech,
            "is_quiet": is_quiet, "activity": activity}


def _temperature_compensate_strain(strain_resp, temp_resp, *, e0=None,
                                   tolerance="2h", min_points=20,
                                   sensor_name="", bridge_name=""):
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
    t_renamed, _ = detect_and_convert_celsius(t_renamed, sensor_name=sensor_name, bridge_name=bridge_name)
    t["temp"] = t_renamed["value"]
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
    result = _fit_and_compensate_baseline(temp_vals, strain_vals, ts_sec_vals)
    eps_mech = result["eps_mech"]
    baseline = result["baseline"]
    alpha = result["alpha"]
    intercept = result["intercept"]
    ts_iso = pd.to_datetime(merged["ts_sec"], unit="s", utc=True).astype(str).to_list()
    raw_paired = [[ts, float(y)] for ts, y in zip(ts_iso, strain_vals)]
    comp_paired = [[ts, float(y)] for ts, y in zip(ts_iso, eps_mech)]
    out = {
        "series": [
            {"name": "strain_raw_paired", "data": raw_paired},
            {"name": "strain_temp_comp", "data": comp_paired},
        ]
    }
    info = {
        "ok": True, "pairs": int(n_pairs), "alpha_cte": float(alpha),
        "intercept": float(intercept),
        "e0": float(e0) if e0 is not None else None,
        "tolerance": str(tolerance), "eps_mech": eps_mech,
    }
    return out, info


# ---------------------------------------------------------------------------
# Chart generators
# ---------------------------------------------------------------------------

def _create_raw_strain_chart(df, sensor_name, out_path):
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    values = df["value"].copy()
    rolling_window = 2
    values_ma = values.rolling(rolling_window, min_periods=1, center=True).mean()
    ax.plot(df["time"], values, color="#d1d5db", linewidth=0.8, label="Raw")
    ax.plot(df["time"], values_ma, color="#0066cc", linewidth=1.4, label="Κυλιόμενος μέσος")
    ax.set_ylabel("Strain-xx-high_rate(us)", fontsize=16)
    ax.set_xlabel("Time", fontsize=16)
    ax.tick_params(axis="both", labelsize=12)
    ax.grid(True, which="major", axis="y", color="lightgray", linewidth=0.5, alpha=0.5)
    ax.grid(True, which="major", axis="x", color="lightgray", linewidth=0.5, alpha=0.5)
    ax.set_axisbelow(True)
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
    ax.xaxis.set_minor_locator(mdates.MonthLocator())
    ax.xaxis.set_minor_formatter(mdates.DateFormatter("%b"))
    ax.legend(fontsize=12, loc="best")
    for spine in ax.spines.values():
        spine.set_linewidth(1.2)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    return out_path


def _create_temperature_chart(df, sensor_name, out_path):
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    temp_c = (df["value"] - 32) * 5 / 9
    time_numeric = (df["time"] - df["time"].min()).dt.total_seconds() / 86400
    ax.plot(time_numeric, temp_c, color="#0066cc", linewidth=1.2)
    ax.set_ylabel("Internal Temperature (\u00b0C)", fontsize=16)
    ax.set_xlabel("Time (days)", fontsize=16)
    ax.tick_params(axis="both", labelsize=12)
    ax.grid(True, which="major", axis="y", color="lightgray", linewidth=0.5, alpha=0.5)
    ax.grid(True, which="major", axis="x", color="lightgray", linewidth=0.5, alpha=0.5)
    ax.set_axisbelow(True)
    ax.set_xlim(left=0)
    for spine in ax.spines.values():
        spine.set_linewidth(1.2)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    return out_path


def _create_strain_comp_chart(comp_resp, eps_mech, out_path):
    series_list = _extract_xy(comp_resp)
    if len(series_list) < 2 or eps_mech is None:
        return None
    raw = pd.DataFrame(series_list[0], columns=["ts", "y_raw"])
    cmp = pd.DataFrame(series_list[1], columns=["ts", "y_cmp"])
    raw["ts"] = pd.to_datetime(raw["ts"], errors="coerce", utc=True)
    cmp["ts"] = pd.to_datetime(cmp["ts"], errors="coerce", utc=True)
    raw["y_raw"] = pd.to_numeric(raw["y_raw"], errors="coerce")
    cmp["y_cmp"] = pd.to_numeric(cmp["y_cmp"], errors="coerce")
    df = raw.merge(cmp, on="ts", how="inner").dropna().sort_values("ts")
    eps_mech = np.asarray(eps_mech, dtype=float)
    if len(eps_mech) != len(df):
        eps_mech = eps_mech[:len(df)]
    bg, fg, grid = "#ffffff", "#111827", "#e5e7eb"
    raw_color = [0.6, 0.6, 0.6]
    mech_color = [0.1, 0.55, 0.3]
    fig, ax = plt.subplots(1, 1, figsize=(12, 5), dpi=150)
    raw_centered = df["y_raw"].to_numpy() - np.mean(df["y_raw"].to_numpy())
    ax.plot(df["ts"], raw_centered, linewidth=1, color=raw_color, label="raw (zero-meaned)")
    ax.plot(df["ts"], eps_mech, linewidth=2, color=mech_color, label="Διορθωμένο (ε_mechanical)")
    ax.set_title("ε_mechanical = ε_total − baseline (με)", fontsize=10, loc="left")
    ax.set_xlabel("Χρόνος", fontsize=14)
    ax.set_ylabel("Παραμόρφωση (με)", fontsize=14)
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
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    return out_path


def _create_accel_chart(df, sensor_name, axis, out_path):
    df_plot = df.copy()
    df_plot["Value"] = df_plot["Value"] - np.mean(df_plot["Value"])
    if len(df_plot) > 20000:
        df_plot = df_plot.iloc[::5]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df_plot["Time"], df_plot["Value"], color="#cc0000", linewidth=1.2, label=axis)
    ax.set_title(sensor_name, fontsize=18, fontweight="bold")
    ax.set_ylabel(f"{axis} (mg)", fontsize=14)
    ax.set_xlabel("Time (UTC)", fontsize=14)
    ax.legend()
    ax.grid(True, color="lightgray", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    return out_path


def _create_highrate_chart(df, sensor_name, axis, out_path):
    df_plot = df.copy()
    df_plot["Value"] = df_plot["Value"] - np.mean(df_plot["Value"])
    if len(df_plot) > 20000:
        df_plot = df_plot.iloc[::5]
    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["font.size"] = 9
    fig, ax = plt.subplots(figsize=(12, 4), dpi=150)
    ax.scatter(df_plot["Time"], df_plot["Value"], s=4, marker=".", linewidths=0, alpha=0.9, color="red")
    ax.plot(df_plot["Time"], df_plot["Value"], color="red", linewidth=0.4, alpha=0.6)
    if not df_plot["Value"].isna().all():
        idx_peak = df_plot["Value"].idxmax()
        t_peak = df_plot.loc[idx_peak, "Time"]
        v_peak = df_plot.loc[idx_peak, "Value"]
        ax.scatter([t_peak], [v_peak], color="black", s=20, zorder=5)
        ax.annotate(f"Peak: {v_peak:.1f} mg", xy=(t_peak, v_peak),
                    xytext=(0, -20), textcoords="offset points", ha="center",
                    fontsize=14, arrowprops=dict(arrowstyle="->", lw=0.8))
    ax.set_xlabel("Time (UTC)", fontsize=18)
    ax.set_ylabel(f"{axis} (mg)", fontsize=18)
    ax.grid(True, which="both", axis="both", alpha=0.3)
    for spine in ax.spines.values():
        spine.set_linewidth(1.2)
    locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.yaxis.set_major_locator(MaxNLocator(nbins=8))
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax.tick_params(axis="both", labelsize=14)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Event segmentation
# ---------------------------------------------------------------------------

_MAX_EVENTS = 5

try:
    import sys as _sys
    _shm_path = Path(__file__).parent.parent / "smart_bridges_shm"
    if _shm_path.exists() and str(_shm_path) not in _sys.path:
        _sys.path.insert(0, str(_shm_path))
    from src.data.events import EventSegmenter
    _HAS_SEGMENTER = True
except ImportError:
    _HAS_SEGMENTER = False


def _segment_events_from_df(df: pd.DataFrame):
    if not _HAS_SEGMENTER:
        return []
    seg_df = df.rename(columns={"Time": "timestamp", "Value": "acceleration_mg"})
    segmenter = EventSegmenter(gap_threshold=5.0, min_duration=0.5,
                               max_duration=120.0, min_samples=10)
    return segmenter.segment(seg_df)


def _top_peak_windows(df: pd.DataFrame, n: int = 5, half_window: int = 500):
    df = df.copy().reset_index(drop=True)
    hw = max(10, min(half_window, len(df) // 4))
    used = pd.Series(False, index=df.index)
    windows = []
    for _ in range(n):
        available = df.loc[~used]
        if available.empty:
            break
        peak_pos = available["Value"].abs().idxmax()
        lo = max(0, peak_pos - hw)
        hi = min(len(df) - 1, peak_pos + hw)
        windows.append(df.iloc[lo: hi + 1].copy())
        used.iloc[lo: hi + 1] = True
    return windows


# ---------------------------------------------------------------------------
# Main processing functions
# ---------------------------------------------------------------------------

def process_strain_data(
    sensors: list[dict],
    graphs_dir: Path,
    pilot_start: str,
    pilot_end: str,
) -> tuple[Path | None, Path | None, dict]:
    """
    Fetch strain + temperature data via REST API for all SG sensors,
    produce all charts and CSVs.

    Returns: (strain_stats_csv, temp_stats_csv, strain_14h_values)
    """
    strain_sensors = [s for s in sensors if "strain" in (s.get("type") or "").lower()]
    if not strain_sensors:
        print("  No strain sensors found")
        return None, None, {}

    t_start = f"{pilot_start} 00:00:00" if " " not in pilot_start else pilot_start
    t_end = f"{pilot_end} 23:59:59" if " " not in pilot_end else pilot_end

    _pilot_end_dt = _dt.strptime(pilot_end.split(" ")[0] if " " in pilot_end else pilot_end, "%Y-%m-%d")
    _target_14h = _pilot_end_dt.replace(hour=14, minute=0, second=0)
    _target_14h_ts = _target_14h.timestamp()

    strain_rows = []
    temp_rows = []
    near_14h = {}
    graphs_dir.mkdir(parents=True, exist_ok=True)

    for sensor in strain_sensors:
        did = sensor.get("device_id", "")
        sid = sensor.get("site_id", "")
        name = sensor.get("Code_name", "") or sensor.get("code_name", "") or did
        if not did or not sid:
            continue
        sensor_status = sensor.get("sensor_status", "").strip().upper()
        if sensor_status == "NO DATA":
            print(f"    ! skipping {name}: sensor_status='NO DATA'")
            continue

        e0 = _safe_float(sensor.get("e0"))

        # Fetch strain and temperature in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed
        strain_resp = None
        temp_resp = None
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = {
                ex.submit(_fetch_strain_data, sid, did, t_start, t_end): "strain",
                ex.submit(_fetch_temperature_data, sid, did, t_start, t_end): "temp",
            }
            for f in as_completed(futures):
                key = futures[f]
                try:
                    result = f.result()
                    if key == "strain":
                        strain_resp = result
                    else:
                        temp_resp = result
                except Exception as e:
                    print(f"    ! {key} fetch failed for {name}: {e}")

        if strain_resp is None and temp_resp is None:
            continue

        # --- Parse strain data ---
        s_list = _extract_xy(strain_resp, time_key="Time")
        if not s_list:
            print(f"    ! No strain data for {name}")
            continue
        s_df = pd.DataFrame(s_list[0], columns=["ts_sec", "value"])
        s_df["ts_sec"] = pd.to_numeric(s_df["ts_sec"], errors="coerce")
        s_df["value"] = pd.to_numeric(s_df["value"], errors="coerce")
        s_df = s_df.dropna().sort_values("ts_sec")

        # --- Parse temperature data ---
        t_list = _extract_xy(temp_resp, time_key="Time")
        if not t_list:
            print(f"    ! No temperature data for {name}")
            continue
        t_df = pd.DataFrame(t_list[0], columns=["ts_sec", "value"])
        t_df["ts_sec"] = pd.to_numeric(t_df["ts_sec"], errors="coerce")
        t_df["value"] = pd.to_numeric(t_df["value"], errors="coerce")
        t_df = t_df.dropna().sort_values("ts_sec")

        if s_df.empty and t_df.empty:
            continue

        # --- Raw strain chart ---
        if not s_df.empty:
            s_df_dt = s_df.copy()
            s_df_dt["time"] = pd.to_datetime(s_df_dt["ts_sec"], unit="s", utc=True)
            _create_raw_strain_chart(s_df_dt, name, str(graphs_dir / f"{name}_strain.png"))

            mean_val = s_df["value"].mean()
            min_val = s_df["value"].min()
            max_val = s_df["value"].max()
            daily_var = max_val - min_val
            strain_rows.append({
                "Αισθητήρας": name, "Μέσος όρος (με)": round(mean_val, 3),
                "Ελάχιστη τιμή (με)": round(min_val, 3),
                "Μέγιστη τιμή (με)": round(max_val, 3),
                "Ημερήσια διακύμανση": round(daily_var, 3),
            })

        # --- Temperature chart ---
        if not t_df.empty:
            t_df_dt = t_df.copy()
            t_df_dt["time"] = pd.to_datetime(t_df_dt["ts_sec"], unit="s", utc=True)
            _create_temperature_chart(t_df_dt, name, str(graphs_dir / f"{name}_temperature.png"))

            t_df_c = t_df.copy()
            t_df_c, _ = detect_and_convert_celsius(t_df_c, sensor_name=name, bridge_name=graphs_dir.name)
            mean_t = t_df_c["value"].mean()
            min_t = t_df_c["value"].min()
            max_t = t_df_c["value"].max()
            daily_var_t = max_t - min_t
            temp_rows.append({
                "Αισθητήρας": name, "Μέσος όρος (°C)": round(mean_t, 3),
                "Ελάχιστη τιμή (°C)": round(min_t, 3),
                "Μέγιστη τιμή (°C)": round(max_t, 3),
                "Ημερήσια διακύμανση (°C)": round(daily_var_t, 3),
            })

        # --- Temperature compensation + comp chart ---
        try:
            comp_resp, fit_info = _temperature_compensate_strain(
                strain_resp, temp_resp, e0=e0,
                sensor_name=name, bridge_name=graphs_dir.name,
            )
            if comp_resp is not None and fit_info.get("ok"):
                eps_mech = fit_info.get("eps_mech")
                _create_strain_comp_chart(comp_resp, eps_mech, str(graphs_dir / f"{name}_strain_comp.png"))
        except Exception as e:
            print(f"    ! temp compensation failed for {name}: {e}")

        # --- Extract 14:00 values ---
        try:
            if not s_df.empty:
                _last_day = _target_14h_ts - 86400
                s_last = s_df[s_df["ts_sec"] >= _last_day].copy()
                t_last = t_df[t_df["ts_sec"] >= _last_day].copy() if not t_df.empty else pd.DataFrame()
                if not s_last.empty:
                    s_last["dist"] = (s_last["ts_sec"] - _target_14h_ts).abs()
                    s_nearest = s_last.loc[s_last["dist"].idxmin()]
                    t_val = None
                    if not t_last.empty:
                        t_last["dist"] = (t_last["ts_sec"] - _target_14h_ts).abs()
                        t_nearest = t_last.loc[t_last["dist"].idxmin()]
                        t_val = round(float(t_nearest["value"]), 3)
                    _ts_dt_obj = _dt.utcfromtimestamp(s_nearest["ts_sec"]) if s_nearest["ts_sec"] > 1e10 else _dt.utcfromtimestamp(s_nearest["ts_sec"])
                    near_14h[name] = {
                        "strain": round(float(s_nearest["value"]), 3),
                        "temperature": t_val,
                        "timestamp": _ts_dt_obj.strftime("%Y-%m-%d %H:%M:%S"),
                    }
        except Exception as e14:
            print(f"    ! 14:00 extraction failed for {name}: {e14}")

    # --- Save CSVs ---
    strain_csv = None
    temp_csv = None
    if strain_rows:
        df_s = pd.DataFrame(strain_rows)
        safe_name = graphs_dir.name
        strain_csv = graphs_dir / f"{safe_name}_strain_stats.csv"
        df_s.to_csv(strain_csv, index=False, encoding="utf-8-sig")
        print(f"  Strain stats CSV: {strain_csv}")
    if temp_rows:
        df_t = pd.DataFrame(temp_rows)
        safe_name = graphs_dir.name
        temp_csv = graphs_dir / f"{safe_name}_temperature_stats.csv"
        df_t.to_csv(temp_csv, index=False, encoding="utf-8-sig")
        print(f"  Temp stats CSV: {temp_csv}")

    return strain_csv, temp_csv, near_14h


def process_accel_data(
    sensors: list[dict],
    graphs_dir: Path,
    pilot_start: str,
    pilot_end: str,
) -> tuple[Path | None, dict]:
    """
    Fetch HPA-X/Y/Z data via REST API for all AC sensors,
    produce basic accel charts, highrate charts, and stats CSV.
    All 3 axes are fetched in parallel per sensor.

    Returns: (accel_stats_csv, hpa_14h_values)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    accel_sensors = [
        s for s in sensors
        if (s.get("type") or "").lower() in ("accelerometer", "accel", "vibration")
        or "-ac-" in (s.get("Code_name") or s.get("code_name") or "").lower()
    ]
    if not accel_sensors:
        print("  No acceleration sensors found")
        return None, {}

    t_start = f"{pilot_start} 00:00:00" if " " not in pilot_start else pilot_start
    t_end = f"{pilot_end} 23:59:59" if " " not in pilot_end else pilot_end

    from datetime import timezone as _tz
    _pilot_end_dt = _dt.strptime(pilot_end.split(" ")[0] if " " in pilot_end else pilot_end, "%Y-%m-%d")
    _target_14h = _pilot_end_dt.replace(hour=14, minute=0, second=0, tzinfo=_tz.utc)

    rows = []
    near_14h = {}
    graphs_dir.mkdir(parents=True, exist_ok=True)

    for sensor in accel_sensors:
        did = sensor.get("device_id", "")
        sid = sensor.get("site_id", "")
        name = sensor.get("Code_name", "") or sensor.get("code_name", "") or did
        if not did or not sid:
            continue

        axes = ("HPA-X", "HPA-Y", "HPA-Z")
        # Fetch all 3 axes in parallel
        axis_results: dict[str, pd.DataFrame | None] = {}
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                ex.submit(_fetch_hpa_data, sid, did, t_start, t_end, axis): axis
                for axis in axes
            }
            for f in as_completed(futures):
                axis = futures[f]
                try:
                    resp = f.result()
                    if resp and not isinstance(resp, dict):
                        resp = {}
                    df = _parse_hpa_to_df(resp) if isinstance(resp, dict) else None
                    axis_results[axis] = df
                except Exception as e:
                    print(f"    ! {axis} fetch failed for {name}: {e}")
                    axis_results[axis] = None

        for axis in axes:
            df = axis_results.get(axis)
            if df is None or df.empty:
                continue

            mean_val = df["Value"].mean()
            min_val = df["Value"].min()
            max_val = df["Value"].max()
            daily_var = max_val - min_val

            rows.append({
                "Αισθητηρας": name, "Άξονας": axis,
                "Μέσος όρος (mg)": round(mean_val, 2),
                "Ελάχιστη τιμή (mg)": round(min_val, 2),
                "Μέγιστη τιμή (mg)": round(max_val, 2),
                "Ημερήσια διακύμανση": round(daily_var, 2),
            })

            axis_tag = axis.replace("-", "").lower()

            # Basic accel chart
            _create_accel_chart(df, name, axis, str(graphs_dir / f"{name}_{axis_tag}_acceleration.png"))

            # Highrate charts (events or peak windows)
            events = _segment_events_from_df(df)
            if events:
                events_ranked = sorted(events, key=lambda e: float(np.max(np.abs(e.data))), reverse=True)[:_MAX_EVENTS]
                events_ranked.sort(key=lambda e: e.start_time)
                for i, evt in enumerate(events_ranked):
                    mask = (df["Time"] >= evt.start_time) & (df["Time"] <= evt.end_time)
                    evt_df = df[mask].copy()
                    if evt_df.empty:
                        continue
                    _create_highrate_chart(evt_df, name, axis,
                                          str(graphs_dir / f"{name}_{axis_tag}_evt{i:03d}_accel_highrate.png"))
            else:
                half_w = max(50, min(1000, len(df) // 20))
                peak_windows = _top_peak_windows(df, n=_MAX_EVENTS, half_window=half_w)
                for i, win_df in enumerate(peak_windows):
                    _create_highrate_chart(win_df, name, axis,
                                          str(graphs_dir / f"{name}_{axis_tag}_evt{i:03d}_accel_highrate.png"))

            # 14:00 HPA-Z value extraction
            if axis == "HPA-Z" and name not in near_14h:
                try:
                    df_near = df.copy()
                    df_near["dist"] = abs(df_near["Time"] - _target_14h)
                    nearest = df_near.loc[df_near["dist"].idxmin()]
                    near_14h[name] = {
                        "hpa": round(float(nearest["Value"]), 3),
                        "timestamp": nearest["Time"].strftime("%Y-%m-%d %H:%M:%S"),
                    }
                except Exception as e14:
                    print(f"    ! 14:00 HPA extraction failed for {name}: {e14}")

    accel_csv = None
    if rows:
        df_stats = pd.DataFrame(rows)
        accel_csv = graphs_dir / f"{graphs_dir.name}_acceleration_stats.csv"
        df_stats.to_csv(accel_csv, index=False, encoding="utf-8-sig")
        print(f"  Accel stats CSV: {accel_csv}")

    return accel_csv, near_14h


def get_pilot_dates(sensors: list[dict]) -> tuple[str, str]:
    pilot_start = ""
    pilot_finish = ""
    for s in sensors or []:
        ps = s.get("Pilot_Date_Start", "") or ""
        pf = s.get("Pilot_Date_Finish", "") or ""
        if ps and not pilot_start:
            pilot_start = ps
        if pf and not pilot_finish:
            pilot_finish = pf
        if pilot_start and pilot_finish:
            break
    if not pilot_start or not pilot_finish:
        raise RuntimeError(
            f"Δεν βρέθηκαν Pilot_Date_Start / Pilot_Date_Finish για κανέναν αισθητήρα. "
            f"Οι ημερομηνίες είναι υποχρεωτικές."
        )
    return pilot_start, pilot_finish


def process_all_sensor_data(
    sensors: list[dict],
    graphs_dir: Path,
    pilot_start: str | None = None,
    pilot_end: str | None = None,
) -> dict[str, Any]:
    """
    Main entry point: fetch all sensor data via REST API and produce all
    charts, CSVs, and metadata needed for report generation.

    Returns dict with keys:
        pilot_start, pilot_finish, strain_csv, temp_csv, accel_csv,
        strain_14h, hpa_14h
    """
    if pilot_start is None or pilot_end is None:
        pilot_start, pilot_end = get_pilot_dates(sensors)

    print(f"  Pilot dates: {pilot_start} -> {pilot_end}")

    strain_csv, temp_csv, strain_14h = process_strain_data(
        sensors, graphs_dir, pilot_start, pilot_end
    )

    accel_csv, hpa_14h = process_accel_data(
        sensors, graphs_dir, pilot_start, pilot_end
    )

    return {
        "pilot_start": pilot_start,
        "pilot_finish": pilot_end,
        "strain_csv": strain_csv,
        "temp_csv": temp_csv,
        "accel_csv": accel_csv,
        "strain_14h": strain_14h or {},
        "hpa_14h": hpa_14h or {},
    }
