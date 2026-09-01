"""
[DEPRECATED] High-rate acceleration chart generator.

This module is no longer used in the main pipeline.
Its functionality has been integrated into sensor_data.py which
fetches HPA data and handles event detection in a single pass.

Adapted from greece-reporting/accel_handler.py (save_highrate_plot2).
Kept for reference or standalone use only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from api_rate_limiter import acquire_slot, release_slot, wait_for_gap

import api_bytes_tracker

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import urllib3
import requests
from matplotlib.ticker import AutoMinorLocator, FuncFormatter, MaxNLocator
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# EventSegmenter from smart_bridges_shm
# ---------------------------------------------------------------------------
_SHM_PATH = Path(__file__).parent.parent / "smart_bridges_shm"
if _SHM_PATH.exists() and str(_SHM_PATH) not in sys.path:
    sys.path.insert(0, str(_SHM_PATH))

try:
    from src.data.events import EventSegmenter
    _HAS_SEGMENTER = True
except ImportError:
    _HAS_SEGMENTER = False

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")
API_KEY = os.getenv("API_KEY")
RESENSYS_API_ROOT = "https://greece-api.resensys.cloud/api/v3"

# Shared session for HTTP keep-alive (connection pooling)
_hpa_session = requests.Session()
_hpa_session.verify = False


# ---------------------------------------------------------------------------
# API fetch
# ---------------------------------------------------------------------------

def _fetch_hpa_data(sid: str, did: str, t_start: str, t_end: str, axis: str = "HPA-Z",
                    max_attempts: int = 3) -> dict:
    """Fetch HPA acceleration data via Resensys REST API for the given axis.
    Retries only on server errors (5xx) and network failures with exponential backoff.
    Does NOT retry on HTTP 204, client errors (4xx), or empty responses.
    """
    import time
    import random

    url = f"{RESENSYS_API_ROOT}/data_acquire"
    payload = json.dumps({
        "SID": sid,
        "DID": did,
        "DF": "33000",
        "Q": axis,
        "U": "mg",
        "T_start": t_start,
        "T_end": t_end,
        "Sample_int": "H1",
    })
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    sleep_time = 1
    last_exc: Exception | None = None
    acquire_slot()
    try:
        for attempt in range(max_attempts):
            try:
                wait_for_gap()
                resp = _hpa_session.post(url, headers=headers, data=payload, timeout=120, verify=False)
                api_bytes_tracker.add_bytes(len(resp.content))

                if resp.status_code == 204:
                    print(f"    ! {axis}: HTTP 204 (no data) — skipping retries")
                    return {"ok": False, "empty": True}

                if resp.status_code in (502, 503, 504):
                    raise requests.HTTPError(f"{resp.status_code} Server Error", response=resp)

                if resp.status_code >= 400 and resp.status_code < 500:
                    print(f"    ! {axis}: client error {resp.status_code} (not retryable)")
                    return {"ok": False, "error": f"HTTP {resp.status_code}"}

                resp.raise_for_status()
                data = resp.json()
                if not data or data == []:
                    print(f"    ! {axis}: empty response — skipping retries")
                    return {"ok": False, "empty": True}
                return data

            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code < 500:
                    print(f"    ! {axis} client error (not retryable): {exc}")
                    return {"ok": False, "error": str(exc)}
                last_exc = exc
                print(f"    ! {axis} attempt {attempt + 1}/{max_attempts} failed: {exc}")
                if attempt < max_attempts - 1:
                    jitter = random.uniform(0.5, 1.5)
                    actual_sleep = sleep_time * jitter
                    print(f"    ! {axis} retrying in {actual_sleep:.1f} seconds...")
                    time.sleep(actual_sleep)
                    sleep_time *= 2
            except Exception as exc:
                last_exc = exc
                print(f"    ! {axis} attempt {attempt + 1}/{max_attempts} failed: {exc}")
                if attempt < max_attempts - 1:
                    jitter = random.uniform(0.5, 1.5)
                    actual_sleep = sleep_time * jitter
                    print(f"    ! {axis} retrying in {actual_sleep:.1f} seconds...")
                    time.sleep(actual_sleep)
                    sleep_time *= 2
        print(f"    ! {axis} giving up after {max_attempts} attempts")
        raise last_exc if last_exc else Exception("Unknown error")
    finally:
        release_slot()


def _parse_to_df(resp: dict) -> pd.DataFrame | None:
    """Parse API response into a DataFrame with columns [Time, Value]."""
    if not resp:
        return None

    # Handle list-of-dicts with Time/Value keys
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
            # Flat dict of {key: {Time: ..., Value: ...}}
            inner = list(resp.values())
            if inner and isinstance(inner[0], dict) and "Time" in inner[0]:
                rows = inner
            else:
                return None
    else:
        return None

    if not rows:
        return None

    # rows may be [[ts, val], ...] or [{Time:..., Value:...}, ...]
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

    # Parse timestamps - handle UNIX seconds or ISO strings
    try:
        ts_numeric = pd.to_numeric(df["Time"], errors="coerce")
        if ts_numeric.notna().all():
            # UNIX timestamp - convert to datetime
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


# ---------------------------------------------------------------------------
# Plotting (save_highrate_plot2 style)
# ---------------------------------------------------------------------------

def _plot_highrate(df: pd.DataFrame, did: str, sensor_name: str, out_path: str, axis: str = "HPA-Z") -> str | None:
    """Plot high-rate acceleration data (zero-mean) and save to out_path."""
    df = df.copy()

    # Zero-mean the signal
    df["Value"] = df["Value"] - np.mean(df["Value"])

    if len(df) > 20000:
        df_plot = df.iloc[::5]
    else:
        df_plot = df

    # Set default font to Arial 9pt
    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["font.size"] = 9

    fig, ax = plt.subplots(figsize=(12, 4), dpi=150)

    ax.scatter(
        df_plot["Time"],
        df_plot["Value"],
        s=4,
        marker=".",
        linewidths=0,
        alpha=0.9,
        color="red",
    )
    ax.plot(
        df_plot["Time"],
        df_plot["Value"],
        color="red",
        linewidth=0.4,
        alpha=0.6,
    )

    if not df_plot["Value"].isna().all():
        idx_peak = df_plot["Value"].idxmax()
        t_peak = df_plot.loc[idx_peak, "Time"]
        v_peak = df_plot.loc[idx_peak, "Value"]
        ax.scatter([t_peak], [v_peak], color="black", s=20, zorder=5)
        ax.annotate(
            f"Peak: {v_peak:.1f} mg",
            xy=(t_peak, v_peak),
            xytext=(0, -20),
            textcoords="offset points",
            ha="center",
            fontsize=14,
            arrowprops=dict(arrowstyle="->", lw=0.8),
        )

    # No title - moved to caption
    ax.set_xlabel("Time (UTC)", fontsize=18)
    ax.set_ylabel(f"{axis} (mg)", fontsize=18)
    ax.grid(True, which="both", axis="both", alpha=0.3)

    # Add frame (box) around the plot
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

    os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)

    print(f"    Saved: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# Event segmentation & peak windowing
# ---------------------------------------------------------------------------

_MAX_EVENTS = 5  # maximum charts per sensor per axis


def _top_peak_windows(df: pd.DataFrame, n: int = 5, half_window: int = 500) -> list:
    """
    Return up to n non-overlapping sample windows centred on the top absolute
    peaks in df.  Uses positional indexing so it works at any sampling rate.
    """
    df = df.copy().reset_index(drop=True)
    hw = max(10, min(half_window, len(df) // 4))
    used = pd.Series(False, index=df.index)
    windows = []

    for _ in range(n):
        available = df.loc[~used]
        if available.empty:
            break
        peak_pos = available["Value"].abs().idxmax()  # label in original df
        lo = max(0, peak_pos - hw)
        hi = min(len(df) - 1, peak_pos + hw)
        windows.append(df.iloc[lo: hi + 1].copy())
        used.iloc[lo: hi + 1] = True

    return windows


def _segment_events_from_df(df: pd.DataFrame) -> list:
    """Segment a (Time, Value) DataFrame into burst events using EventSegmenter."""
    if not _HAS_SEGMENTER:
        return []
    seg_df = df.rename(columns={"Time": "timestamp", "Value": "acceleration_mg"})
    segmenter = EventSegmenter(
        gap_threshold=5.0,
        min_duration=0.5,
        max_duration=120.0,
        min_samples=10,
    )
    return segmenter.segment(seg_df)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_accel_highrate_charts(
    sensors: list[dict],
    graphs_dir: Path,
    pilot_start: str,
    pilot_end: str,
) -> tuple[list[str], dict]:
    """
    For each accelerometer sensor, fetch HPA-X/Y/Z data via REST API and
    produce a high-rate detection-style chart (section 1.2.2).

    Also extracts the HPA-Z measurement closest to 14:00 on the last pilot day
    for use in the HPA quality table.

    Args:
        sensors: list of sensor dicts (must have 'type', 'device_id', 'site_id').
        graphs_dir: directory to save chart PNGs.
        pilot_start: start date string, e.g. "2025-06-06".
        pilot_end: end date string, e.g. "2025-08-06".

    Returns:
        tuple of (list of saved chart file paths, dict of 14:00 HPA values per sensor).
    """
    if not API_KEY:
        print("  ! accel_highrate_charts: API_KEY not set, skipping")
        return [], {}

    accel_sensors = [
        s for s in sensors
        if s.get("type", "").lower() in ("accelerometer", "accel", "vibration")
        or "-ac-" in s.get("Code_name", s.get("code_name", "")).lower()
    ]

    if not accel_sensors:
        print("  ! accel_highrate_charts: no accelerometer sensors found")
        return [], {}

    t_start = f"{pilot_start} 00:00:00" if " " not in pilot_start else pilot_start
    t_end = f"{pilot_end} 23:59:59" if " " not in pilot_end else pilot_end

    # Target datetime for 14:00 extraction
    from datetime import datetime as _dt, timezone as _tz
    _pilot_end_dt = _dt.strptime(pilot_end.split(" ")[0] if " " in pilot_end else pilot_end, "%Y-%m-%d")
    _target_14h = _pilot_end_dt.replace(hour=14, minute=0, second=0, tzinfo=_tz.utc)

    saved = []
    near_14h_values: dict = {}
    for sensor in accel_sensors:
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

        for axis in ("HPA-X", "HPA-Y", "HPA-Z"):
            print(f"  Accel highrate chart: {name} {axis} (DID={did}, SID={sid})")

            try:
                resp = _fetch_hpa_data(sid, did, t_start, t_end, axis=axis)
            except Exception as e:
                print(f"    ! HPA fetch failed ({axis}): {e}")
                continue

            df = _parse_to_df(resp)
            if df is None or df.empty:
                print(f"    ! No data returned for {name} {axis}")
                continue

            # Extract HPA-Z value closest to 14:00 on the last pilot day
            if axis == "HPA-Z" and name not in near_14h_values:
                try:
                    df_near = df.copy()
                    df_near["dist"] = abs(df_near["Time"] - _target_14h)
                    nearest = df_near.loc[df_near["dist"].idxmin()]
                    near_14h_values[name] = {
                        "hpa": round(float(nearest["Value"]), 3),
                        "timestamp": nearest["Time"].strftime("%Y-%m-%d %H:%M:%S"),
                    }
                except Exception as e14:
                    print(f"    ! 14:00 HPA extraction failed for {name}: {e14}")

            axis_tag = axis.replace("-", "").lower()  # hpax / hpay / hpaz

            # Remove stale charts from previous runs for this sensor+axis
            for old in graphs_dir.glob(f"{name.strip()}_{axis_tag}*_accel_highrate.png"):
                old.unlink(missing_ok=True)

            events = _segment_events_from_df(df)
            if events:
                # Sort by absolute peak, keep top _MAX_EVENTS, restore chronological order
                events_ranked = sorted(
                    events,
                    key=lambda e: float(np.max(np.abs(e.data))),
                    reverse=True,
                )[:_MAX_EVENTS]
                events_ranked.sort(key=lambda e: e.start_time)
                print(f"    {len(events)} event(s) found, plotting top {len(events_ranked)} by peak")
                for i, evt in enumerate(events_ranked):
                    mask = (df["Time"] >= evt.start_time) & (df["Time"] <= evt.end_time)
                    evt_df = df[mask].copy()
                    if evt_df.empty:
                        continue
                    out_path = str(
                        graphs_dir / f"{name.strip()}_{axis_tag}_evt{i:03d}_accel_highrate.png"
                    )
                    result = _plot_highrate(evt_df, did, name, out_path, axis=axis)
                    if result:
                        saved.append(result)
            else:
                # No discrete events: find top _MAX_EVENTS non-overlapping peak windows
                half_w = max(50, min(1000, len(df) // 20))
                peak_windows = _top_peak_windows(df, n=_MAX_EVENTS, half_window=half_w)
                print(f"    No burst events; top {len(peak_windows)} peak windows (\u00b1{half_w} samples)")
                for i, win_df in enumerate(peak_windows):
                    out_path = str(
                        graphs_dir / f"{name.strip()}_{axis_tag}_evt{i:03d}_accel_highrate.png"
                    )
                    result = _plot_highrate(win_df, did, name, out_path, axis=axis)
                    if result:
                        saved.append(result)

    return saved, near_14h_values
