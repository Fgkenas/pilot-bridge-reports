"""Generate water level sensor (WLS) raw-data charts for section 1.1.4.

Fetches Distance_Avr-S (mm) from Resensys for each waterlevel sensor and saves
a blue line chart as {sensor_name}_wls.png in the graphs directory.
"""
from __future__ import annotations

from resensys_hosts import PORTAL_BASE, PORTAL_PLOT_URL, portal_graph_referer

import json
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests

from api_rate_limiter import acquire_slot, release_slot, wait_for_gap


RESENSYS_PLOT_URL = PORTAL_PLOT_URL
USERNAME = "Vassilis"


def _fetch_wls_data(sensor: dict, session: requests.Session, csrftoken: str) -> pd.DataFrame | None:
    """Fetch Distance_Avr-S (mm) data for a waterlevel sensor."""
    device_id = sensor.get("device_id", "")
    site_id = sensor.get("site_id", "")

    pilot_start = sensor.get("Pilot_Date_Start") or sensor.get("pilot_date_start", "")
    pilot_finish = sensor.get("Pilot_Date_Finish") or sensor.get("pilot_date_finish", "")

    if not pilot_start or not pilot_finish:
        raise ValueError(f"Missing pilot dates for WLS sensor {sensor.get('Code_name', device_id)} (Pilot_Date_Start / Pilot_Date_Finish required)")
    else:
        start_dt = datetime.strptime(pilot_start, "%Y-%m-%d")
        end_dt = datetime.strptime(pilot_finish, "%Y-%m-%d")

    start_local = start_dt.strftime("%m/%d/%Y 00:00:00")
    end_local = end_dt.strftime("%m/%d/%Y 23:59:59")
    start_utc = (start_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = (end_dt - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    form_data = {
        "username": USERNAME,
        "selected_DID": device_id,
        "selected_SID": site_id,
        "selected_DF": "35004",
        "selected_Q": "Distance_Avr-S",
        "selected_Unit": "mm",
        "selected_time_range": f"{start_local} - {end_local}",
        "selected_time_start_utc": start_utc,
        "selected_time_end_utc": end_utc,
        "local_timezone": "Europe/Athens",
        "left_or_right": "0",
        "new_chart": "1",
        "add_chart": "1",
        "remove_avg": "0",
        "show_alert": "0",
        "median_filter": "0",
        "utc_times": "0",
        "twos_comp": "0",
        "downsample_interval": "",
    }

    headers = {
        "X-CSRFToken": csrftoken,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": portal_graph_referer(site_id),
        "Origin": PORTAL_BASE,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    code_name = sensor.get("Code_name") or sensor.get("code_name", device_id)
    try:
        print(f"  Fetching WLS Distance_Avr-S data for {code_name}...")
        acquire_slot()
        try:
            wait_for_gap()
            response = session.post(RESENSYS_PLOT_URL, data=form_data, headers=headers, timeout=60, verify=False)
        finally:
            release_slot()
        response.raise_for_status()
        data = response.json()

        if "data_page_json" not in data:
            print(f"    No data returned from Resensys for {code_name}")
            return None

        raw_x = data.get("raw_x", [])
        raw_y = data.get("raw_y", [])

        if not raw_x or not raw_y:
            print(f"    Empty data arrays for {code_name}")
            return None

        timestamps = [datetime.fromtimestamp(ts) for ts in raw_x]
        df = pd.DataFrame({"time": timestamps, "value": raw_y})
        print(f"    Got {len(df)} data points for {code_name}")
        return df

    except Exception as exc:
        print(f"    Error fetching WLS data for {code_name}: {exc}")
        return None


def _create_wls_chart(df: pd.DataFrame, sensor: dict, output_dir: Path) -> Path | None:
    """Create a blue water level chart and save as {sensor_name}_wls.png."""
    code_name = sensor.get("Code_name") or sensor.get("code_name", "")
    if not code_name:
        print("    ! Skipping WLS chart: empty sensor name")
        return None

    # Set default font to Arial 9pt
    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["font.size"] = 9

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ax.plot(df["time"], df["value"], color="#0066cc", linewidth=1.2)

    ax.set_ylabel("Distance_Avr-S (mm)", fontsize=16)
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

    for spine in ax.spines.values():
        spine.set_linewidth(1.2)

    plt.tight_layout()

    filename = output_dir / f"{code_name}_wls.png"
    plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    Saved WLS chart: {filename.name}")
    return filename


def generate_wls_charts(
    sensors: list,
    graphs_dir: Path,
    session: requests.Session,
    csrftoken: str,
) -> list[Path]:
    """Fetch and chart all waterlevel sensors. Returns list of saved chart paths."""
    wls_sensors = [
        s for s in sensors
        if (s.get("type") or "").lower() == "waterlevel"
    ]

    if not wls_sensors:
        print("  No waterlevel sensors found, skipping WLS raw charts")
        return []

    print(f"  Generating WLS raw charts for {len(wls_sensors)} sensor(s)...")
    charts = []
    for sensor in wls_sensors:
        df = _fetch_wls_data(sensor, session, csrftoken)
        if df is not None and not df.empty:
            chart_path = _create_wls_chart(df, sensor, graphs_dir)
            if chart_path:
                charts.append(chart_path)

    print(f"  WLS charts done: {len(charts)} saved")
    return charts
