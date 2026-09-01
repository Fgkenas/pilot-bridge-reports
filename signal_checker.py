"""Signal (RSSI) checker for Bridge Report."""
from __future__ import annotations
import os
from pathlib import Path
import urllib3
from dotenv import load_dotenv
import requests
from utils import round_value as _round_value

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load API key from secrets/.env
secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")
API_KEY = os.getenv("API_KEY")

RESENSYS_API_ROOT = "https://greece-api.resensys.cloud/api/v3"


def check_signal(did: str) -> dict:
    """Check RSSI signal quality for a device ID.
    
    Args:
        did: Device ID (sensor's device_id)
    
    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    if not did:
        return {"ok": False, "error": "did required"}

    max_attempts = 3
    retry_sleep = 1
    for attempt in range(max_attempts):
        try:
            print(f"Checking RSSI for DID: {did}")
            r = requests.post(
                f"{RESENSYS_API_ROOT}/rssi_status",
                headers={
                    "X-API-Key": API_KEY,
                    "Content-Type": "application/json"
                },
                json={"did": did},
                verify=False,
            )
            break
        except requests.RequestException as e:
            if attempt < max_attempts - 1:
                print(f"RSSI check retry {attempt + 1}/{max_attempts}: {e}")
                import time
                time.sleep(retry_sleep)
                retry_sleep *= 2
            else:
                print(f"Error checking RSSI: {e}")
                return {"ok": False, "error": f"upstream error: {e}"}

    if r.status_code != 200:
        return {"ok": False, "status_code": r.status_code, "body": r.text}

    try:
        upstream = r.json()
    except ValueError:
        upstream = {}

    print(f"RSSI backend response: {upstream}")
    print(f"  - status: {upstream.get('status')}")
    print(f"  - latest: {upstream.get('latest')}")
    print(f"  - latest_time: {upstream.get('latest_time')}")

    # Extract values from upstream response
    status = upstream.get('status', '')
    latest = upstream.get('latest', '')
    latest_time = upstream.get('latest_time', '')

    if status == "good" or status == "Καλό":
        explain = f"Το RSSI και το σήμα της συσκευής είναι ΚΑΛΑ με την τελευταία ανάγνωση να είναι {latest}."
    else:
        explain = f"Το RSSI και το σήμα της συσκευής είναι ΑΔΥΝΑΜΟ, με την τελευταία ένδειξη να είναι {latest}."

    # Determine if signal passed
    if isinstance(upstream.get("ok"), bool):
        passed = upstream["ok"]
    elif isinstance(status, str):
        s = status.lower()
        passed = ("ok" in s) or ("pass" in s) or ("good" in s) or ("within" in s) or ("καλό" in s)
    else:
        passed = False

    return {
        "ok": passed,
        "raw": upstream,
        "latest_time": latest_time,
        "explain": explain,
        "metrics": {"RSSI": _round_value(latest)}
    }
