"""
Data consumption calculator for BridgeReport.

Fetches total data consumption from MySQL database for site IDs
during a specified time period.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv
import mysql.connector

# Load credentials from secrets/.env
secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")

MYSQL_USER = os.getenv("SQL_USERNAME")
MYSQL_PASSWORD = os.getenv("SQL_PASSWORD")
MYSQL_HOST = os.getenv("SQL_HOST", "resensys.net")
MYSQL_PORT = int(os.getenv("SQL_PORT", "3306"))

_MYSQL_FALLBACK_HOSTS: list[str] = []


def get_site_data_consumption(start_utc: str, end_utc: str, site_ids: list[str]) -> float | None:
    """
    Returns total data consumption in MB from gateways in specified time period.

    Args:
        start_utc: Start time in UTC format (e.g., "2025-06-06")
        end_utc: End time in UTC format (e.g., "2025-08-06")
        site_ids: List of site/sensor IDs to query

    Returns:
        Total data consumption in MB as float, or None if unable to query.
    """
    if not MYSQL_USER or not MYSQL_PASSWORD:
        print("     ! MySQL credentials not found in secrets/.env")
        return None

    total = 0.0
    success_count = 0
    fail_count = 0
    hosts_to_try = [MYSQL_HOST] + [h for h in _MYSQL_FALLBACK_HOSTS if h != MYSQL_HOST]

    conn = None
    for host in hosts_to_try:
        try:
            conn = mysql.connector.connect(
                host=host,
                port=MYSQL_PORT,
                user=MYSQL_USER,
                password=MYSQL_PASSWORD,
                use_pure=True,
                connect_timeout=15,
            )
            print(f"     MySQL connected to {host}")
            break
        except Exception as exc:
            print(f"     ! MySQL connection to {host} failed: {exc}")
            conn = None

    if conn is None:
        print("     ! Could not connect to MySQL on any host")
        return None

    cur = None
    try:
        cur = conn.cursor()
        for sid in site_ids:
            sid_clean = sid.replace("-", "")
            sql = f"""
            SELECT SUM(Optional)/1000000 AS Data_Consumption_Total_MB 
            FROM `Data_{sid_clean}`.`Data.0000{sid_clean}.{sid_clean}.3003` 
            WHERE Time BETWEEN "{start_utc}" AND "{end_utc}";
            """
            try:
                cur.execute(sql)
                result = cur.fetchall()
                subtotal = result[0][0] if result else 0.0
                if subtotal is not None:
                    total += float(subtotal)
                    success_count += 1
                    print(f"     MySQL {sid}: {float(subtotal):.4f} MB")
                else:
                    total += 0.0
                    success_count += 1
                    print(f"     MySQL {sid}: 0 MB (no data)")
            except Exception as exc:
                print(f"     ! MySQL query failed for {sid}: {exc}")
                fail_count += 1
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    print(f"     MySQL summary: {success_count} OK, {fail_count} failed, {total:.4f} MB total")
    return total if success_count > 0 else None
