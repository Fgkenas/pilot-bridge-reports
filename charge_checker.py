"""Charge checker for Bridge Report."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv
import requests
from utils import round_value as _round_value

# Load API key from secrets/.env
secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")
API_KEY = os.getenv("API_KEY")

RESENSYS_API_ROOT = "https://greece-api.resensys.cloud/api/v3"


def check_charge(did: str) -> dict:
    """Check charge status for a gateway device ID.
    
    Args:
        did: Device ID (gateway's device_id)
    
    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    if not did:
        return {"ok": False, "error": "did required"}

    max_attempts = 3
    retry_sleep = 1
    for attempt in range(max_attempts):
        try:
            print(f"Checking Charge for DID: {did}")
            r = requests.post(
                f"{RESENSYS_API_ROOT}/charge_status",
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
                print(f"Charge check retry {attempt + 1}/{max_attempts}: {e}")
                import time
                time.sleep(retry_sleep)
                retry_sleep *= 2
            else:
                print(f"Error checking charge: {e}")
                return {"ok": False, "error": f"upstream error: {e}"}

    if r.status_code != 200:
        return {"ok": False, "status_code": r.status_code, "body": r.text}

    try:
        upstream = r.json()
    except ValueError:
        upstream = {}

    print(f"Charge backend response: {upstream}")
    print(f"  - status: {upstream.get('status')}")
    print(f"  - latest: {upstream.get('latest')}")
    print(f"  - latest_time: {upstream.get('latest_time')}")

    # Extract values from upstream response
    status = upstream.get('status', '')
    latest = upstream.get('latest', '')
    latest_time = upstream.get('latest_time', '')

    if status == "good" or status == "Καλό":
        explain = f"Η φόρτιση είναι ΚΑΛΉ με τη μέγιστη ένδειξη να είναι {latest}."
    else:
        explain = f"Η φόρτιση είναι ΑΔΥΝΑΜΗ, με τη μέγιστη ένδειξη να είναι {latest}."

    # Determine if charge passed
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
        "metrics": {"Charge": _round_value(latest)}
    }
