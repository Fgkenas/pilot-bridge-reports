"""
[DEPRECATED] Acceleration data fetching from Resensys AJAX API.

This module is no longer used in the main pipeline.
It has been replaced by sensor_data.py which uses the REST API
and avoids duplicate data fetching.

Kept for reference or standalone use only.
"""
from __future__ import annotations

from resensys_hosts import PORTAL_BASE, PORTAL_PLOT_URL, portal_graph_referer

import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import urllib3

from api_rate_limiter import acquire_slot, release_slot, wait_for_gap

import api_bytes_tracker

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)


def fetch_acceleration_axis(
    session: requests.Session,
    csrf_token: str,
    device_id: str,
    site_id: str,
    axis: str = "HPA-Z",
    start_date: str = "",
    end_date: str = "",
) -> pd.DataFrame | None:
    if not start_date or not end_date:
        raise ValueError("start_date and end_date are required (Pilot_Date_Start / Pilot_Date_Finish)")
    """Fetch acceleration data from Resensys API for a specific axis (HPA-X, HPA-Y, HPA-Z)."""
    url = PORTAL_PLOT_URL

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_local = start_dt.strftime("%m/%d/%Y 00:00:00")
    end_local = end_dt.strftime("%m/%d/%Y 23:59:59")
    start_utc = (start_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = (end_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    form_data = {
        'username': 'Vassilis',
        'selected_DID': device_id,
        'selected_SID': site_id,
        'selected_DF': '32800',
        'selected_Q': axis,
        'selected_Unit': 'mg',
        'selected_time_range': f"{start_local} - {end_local}",
        'selected_time_start_utc': start_utc,
        'selected_time_end_utc': end_utc,
        'local_timezone': 'Europe/Athens',
        'left_or_right': '0',
        'new_chart': '1',
        'add_chart': '1',
        'remove_avg': '1',
        'show_alert': '0',
        'median_filter': '0',
        'utc_times': '0',
        'twos_comp': '0',
        'downsample_interval': ''
    }

    headers = {
        'X-CSRFToken': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': portal_graph_referer(site_id),
        'Origin': PORTAL_BASE,
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }

    import random
    max_attempts = 3
    sleep_time = 1
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            print(f"    Fetching {axis} for device {device_id}... (attempt {attempt + 1}/{max_attempts})")

            acquire_slot()
            try:
                wait_for_gap()
                response = session.post(url, data=form_data, headers=headers, timeout=120, verify=False)
                api_bytes_tracker.add_bytes(len(response.content))

                if response.status_code == 204:
                    print(f"      HTTP 204 (no data) — skipping retries")
                    return None

                if response.status_code >= 400 and response.status_code < 500:
                    print(f"      Client error {response.status_code} (not retryable)")
                    return None

                response.raise_for_status()
                data = response.json()
            finally:
                release_slot()

            if 'data_page_json' not in data:
                return None
            raw_x = data.get('raw_x', [])
            raw_y = data.get('raw_y', [])
            if not raw_x or not raw_y:
                return None
            timestamps = [datetime.fromtimestamp(ts) for ts in raw_x]
            df = pd.DataFrame({'time': timestamps, 'value': raw_y})
            print(f"      Got {len(df)} points")
            return df
        except Exception as e:
            last_exc = e
            print(f"      Error: {e}")
            if attempt < max_attempts - 1:
                sleep_time *= 2
                jitter = random.uniform(0.5, 1.5)
                actual_sleep = sleep_time * jitter
                print(f"      Retrying in {actual_sleep:.1f} seconds...")
                time.sleep(actual_sleep)

    print(f"      Giving up after {max_attempts} attempts")
    return None


def process_bridge_acceleration(
    bridge_name: str,
    sensors: list[dict],
    session: requests.Session,
    csrf_token: str,
    out_dir: Path,
    start_date: str = "",
    end_date: str = "",
) -> Path | None:
    if not start_date or not end_date:
        raise ValueError("start_date and end_date are required (Pilot_Date_Start / Pilot_Date_Finish)")
    """Fetch acceleration data for all AC sensors and create CSV + charts."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    # Debug: show all sensors
    print(f"  Total sensors passed: {len(sensors)}")
    for s in sensors:
        print(f"    - {s.get('Code_name', s.get('code_name', 'unknown'))}: type={s.get('type', 'unknown')}")

    accel_sensors = [s for s in sensors
                     if 'accelerometer' in s.get('type', '').lower() or '-ac-' in s.get('Code_name', '').lower() or '-ac-' in s.get('code_name', '').lower()]
    print(f"  Acceleration sensors found: {len(accel_sensors)}")
    for s in accel_sensors:
        print(f"    AC: {s.get('Code_name', s.get('code_name'))}")

    if not accel_sensors:
        print(f"  No acceleration sensors for {bridge_name}")
        return None

    print(f"  Processing {len(accel_sensors)} acceleration sensor(s)")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for sensor in accel_sensors:
        code = sensor.get('Code_name', sensor.get('code_name', ''))
        print(f"  Processing sensor: Code_name='{code}'")
        if not code:
            code = sensor.get('device_id', '')
            print(f"  -> Using device_id: '{code}'")
        if not code:
            print(f"  ! Skipping sensor with no code name or device_id")
            continue
        did = sensor.get('device_id', '')
        sid = sensor.get('site_id', '')
        print(f"  Processing {code}...")

        # Fetch HPA-X, HPA-Y, HPA-Z data
        for axis in ('HPA-X', 'HPA-Y', 'HPA-Z'):
            df = fetch_acceleration_axis(session, csrf_token, did, sid, axis, start_date, end_date)
            if df is None or df.empty:
                continue

            mean_val = df['value'].mean()
            min_val = df['value'].min()
            max_val = df['value'].max()
            daily_var = max_val - min_val

            rows.append({
                'Αισθητηρας': code,
                'Άξονας': axis,
                'Μέσος όρος (mg)': round(mean_val, 2),
                'Ελάχιστη τιμή (mg)': round(min_val, 2),
                'Μέγιστη τιμή (mg)': round(max_val, 2),
                'Ημερήσια διακύμανση': round(daily_var, 2),
            })

            # Defensive: ensure code is not empty
            if not code or not str(code).strip():
                print(f"  ! Skipping sensor with empty code after fetch")
                continue
            safe_code = str(code).strip()
            axis_tag = axis.replace('-', '').lower()  # hpax / hpay / hpaz

            # Zero-mean all axes before plotting (stats computed from raw values above)
            df_plot = df.copy()
            df_plot['value'] = df_plot['value'] - df_plot['value'].mean()

            # Create chart (red like strain charts)
            fig, ax = plt.subplots(figsize=(12, 6))
            ax.plot(df_plot['time'], df_plot['value'], color='#cc0000', linewidth=1.2, label=axis)
            ax.set_title(f'{code}', fontsize=18, fontweight='bold')
            ax.set_ylabel(f'{axis} (mg)', fontsize=14)
            ax.set_xlabel('Time (UTC)', fontsize=14)
            ax.legend()
            ax.grid(True, color='lightgray', linewidth=0.5)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            plt.tight_layout()
            chart_path = out_dir / f"{safe_code}_{axis_tag}_acceleration.png"
            plt.savefig(chart_path, dpi=150, bbox_inches='tight')
            plt.close()
            print(f"    Chart: {chart_path.name}")

    if not rows:
        return None

    df_stats = pd.DataFrame(rows)
    csv_path = out_dir / f"{bridge_name}_acceleration_stats.csv"
    df_stats.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"  CSV: {csv_path}")
    return csv_path
