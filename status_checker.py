"""
status_checker.py
=================
The one implementation behind battery/charge/hpa/signal/strain/wls
_checker.py.

Those six modules were ~95% identical copies of each other (~530 lines
that differed only in an endpoint name, a log label, the Greek sentence
they produce and which key the reading is filed under in `metrics`).
They are now thin wrappers around check_status() below, so a fix to the
request handling -- a timeout, a retry, an auth guard -- lands in all six
at once instead of needing six edits.

Their public API is unchanged: each module still exports
check_<name>(did) and still returns the same dict shape
({ok, raw, latest_time, explain, metrics}, or {ok: False, error/...} on
failure), so main.py's imports and call sites are untouched.
"""
from __future__ import annotations

import os
from pathlib import Path

import time

import requests
import urllib3
from dotenv import load_dotenv

from resensys_hosts import API_ROOT
from utils import round_value

# pilot-bridge-reports talks to the v3 API with verification off; keep that
# posture here rather than silently tightening it during the merge.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load API key from secrets/.env (explicit path, not cwd-dependent).
secrets_dir = Path(__file__).parent / "secrets"
load_dotenv(secrets_dir / ".env")
API_KEY = os.getenv("API_KEY")

REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 1

# Status words that mean "healthy". Each checker adds its own Greek
# variants on top; these are deliberately kept per-checker rather than
# unified, because widening the set for a given quantity would flip real
# report verdicts from FAIL to PASS.
BASE_PASS_WORDS = ("ok", "pass", "good", "within")


def check_status(did: str, *, endpoint: str, log_label: str,
                 explain_good: str, explain_bad: str,
                 metrics: tuple[tuple[str, str], ...],
                 pass_words: tuple[str, ...] = BASE_PASS_WORDS,
                 extra_logged_fields: tuple[str, ...] = ()) -> dict:
    """POST {API_ROOT}/{endpoint} with {"did": did} and interpret the reply.

    Parameters
    ----------
    endpoint : str
        Path under the v3 API root, e.g. "battery_status".
    log_label : str
        Human name used in the progress prints ("Battery", "RSSI", ...).
    explain_good, explain_bad : str
        Greek sentences for the report, formatted with {latest}.
    metrics : tuple of (metric_name, upstream_field) pairs
        Which upstream fields land in the returned "metrics" dict and
        under what name, e.g. (("Battery Voltage", "latest"),).
    pass_words : tuple of str
        Lowercased substrings of `status` that count as healthy.
    extra_logged_fields : tuple of str
        Additional upstream keys to echo in the progress print.

    The healthy/unhealthy sentence is chosen from the SAME verdict that
    sets "ok", rather than from its own separate equality test. Before,
    `passed` used a broad substring match while the sentence used a
    strict `status == "good"`, so an upstream status of "OK" or "within
    range" produced ok=True alongside a sentence stating the sensor was
    faulty -- a contradiction that went straight into the client report.
    """
    if not did:
        return {"ok": False, "error": "did required"}
    # Without this the header goes out as X-API-Key: None (secrets/.env
    # missing or unreadable) and the failure surfaces as a confusing
    # upstream error rather than the real cause.
    if not API_KEY:
        return {"ok": False, "error": "API_KEY not set (create secrets/.env)"}

    # Retry with backoff, as each per-checker copy did before the six were
    # merged into this module. The timeout is the addition: without one a
    # single unresponsive request hangs the whole report indefinitely.
    r = None
    sleep_for = RETRY_SLEEP_SECONDS
    for attempt in range(MAX_ATTEMPTS):
        try:
            print(f"Checking {log_label} for DID: {did}")
            r = requests.post(
                f"{API_ROOT}/{endpoint}",
                headers={
                    "X-API-Key": API_KEY,
                    "Content-Type": "application/json"
                },
                json={"did": did},
                timeout=REQUEST_TIMEOUT,
                verify=False,
            )
            break
        except requests.RequestException as e:
            if attempt < MAX_ATTEMPTS - 1:
                print(f"{log_label} check retry {attempt + 1}/{MAX_ATTEMPTS}: {e}")
                time.sleep(sleep_for)
                sleep_for *= 2
            else:
                print(f"Error checking {log_label}: {e}")
                return {"ok": False, "error": f"upstream error: {e}"}

    if r.status_code != 200:
        return {"ok": False, "status_code": r.status_code, "body": r.text}

    try:
        upstream = r.json()
    except ValueError:
        upstream = {}

    print(f"{log_label} backend response: {upstream}")
    print(f"  - status: {upstream.get('status')}")
    print(f"  - latest: {upstream.get('latest')}")
    for field in extra_logged_fields:
        print(f"  - {field}: {upstream.get(field)}")
    print(f"  - latest_time: {upstream.get('latest_time')}")

    status = upstream.get("status", "")
    latest = upstream.get("latest", "")
    latest_time = upstream.get("latest_time", "")

    # Verdict first, sentence second -- see the docstring.
    if isinstance(upstream.get("ok"), bool):
        passed = upstream["ok"]
    elif isinstance(status, str):
        s = status.lower()
        passed = any(word in s for word in pass_words)
    else:
        passed = False

    explain = (explain_good if passed else explain_bad).format(latest=latest)

    return {
        "ok": passed,
        "raw": upstream,
        "latest_time": latest_time,
        "explain": explain,
        "metrics": {name: round_value(upstream.get(field, ""))
                    for name, field in metrics},
    }
