"""
Bridge info fetching and tag-map building.

Encapsulates all API calls (bridge API + Resensys) and the logic
that flattens an API response into a {TAG: value} dictionary for
docx placeholder replacement.
"""

from __future__ import annotations

from resensys_hosts import BRIDGES_API_URL, PORTAL_LOGIN_URL, PORTAL_REGISTRATION_URL, PORTAL_SITENAME_URL, portal_graph_referer

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

import requests

try:
    from pyproj import Transformer as _ProjTransformer
except ImportError:
    _ProjTransformer = None

# ---------------------------------------------------------------------------
# Manual Pilot_Date_Start / Pilot_Date_Finish overrides
# (DB stores day/month swapped for these bridges; correct values here)
# ---------------------------------------------------------------------------
_MANUAL_PILOT_DATE_OVERRIDES: dict[str, tuple[str, str]] = {
    "ΒΟΑ_001_ΦΟΝ": ("2026-04-03", "2026-06-03"),
}


# ---------------------------------------------------------------------------
# Manual Monthly_1_Date_Start overrides (from Monthly Reports-progress.xlsx Col F)
# ---------------------------------------------------------------------------
_MANUAL_MONTHLY_DATE_OVERRIDES: dict[str, str] = {
    "ΗΠΕ_004_ΚΟΝ": "2026-03-08",
    "ΗΠΕ_005_ΣΠΗ": "2026-03-06",
    "ΗΠΕ_006_ΑΓΙ": "2026-03-10",
    "ΗΠΕ_011_ΚΟΡ": "2026-04-04",
    "ΗΠΕ_013_ΠΑΛ": "2026-04-07",
    "ΗΠΕ_014_ΠΕΡ": "2026-04-06",
    "ΗΠΕ_015_ΤΖΑ": "2026-04-02",
    "ΚΡ_005_ΔΕΜ": "2025-09-30",
    "ΚΡ_006_ΠΟΜ": "2025-09-29",
    "ΚΡ_010_ΤΑΥ": "2025-11-03",
    "ΚΡ_011_ΚΑΡ": "2025-10-12",
    "ΔΕ_008_ΣΠΟ": "2025-01-11",
    "ΔΕ_013_ΠΕΙ": "2025-11-08",
    "ΔΕ_014_ΚΑΒ": "2025-08-10",
    "ΝΟΑ_001_ΓΑΙ": "2026-04-02",
    "ΝΟΑ_002_ΦΟΝ": "2026-04-03",
    "ΝΟΑ_003_ΑΣΚ": "2026-04-05",
    "ΝΟΑ_004_ΚΗΠ": "2026-04-07",
    "ΝΟΑ_005_ΤΡΑ": "2026-03-24",
    "ΝΟΑ_006_ΑΦΑ": "2026-03-27",
    "ΝΟΑ_007_ΛΟΥ": "2026-03-30",
    "ΝΟΑ_008_ΚΟΛ": "2026-03-22",
    "ΝΟΑ_009_ΣΚΟ": "2026-04-09",
    "ΝΟΑ_010_ΛΑΧ": "2026-04-11",
    "ΝΟΑ_011_ΓΕΝ": "2026-04-12",
    "ΝΟΑ_012_ΦΙΛ": "2026-04-25",
    "ΝΟΑ_013_ΧΑΛ": "2026-04-21",
    "ΝΟΑ_014_ΜΕΣ": "2026-04-19",
    "ΝΟΑ_015_ΤΙΓ": "2026-04-23",
    "ΒΟΑ_001_ΦΟΝ": "2026-04-06",
    "ΒΟΑ_002_ΣΤΑ": "2026-04-29",
    "ΒΟΑ_003_ΑΓΕ": "2026-03-25",
    "ΒΟΑ_004_ΕΥ1": "2026-03-27",
    "ΒΟΑ_005_ΕΥ2": "2026-03-30",
    "ΒΟΑ_006_ΣΕΔ": "2026-04-02",
    "ΒΟΑ_007_ΠΕΤ": "2026-05-03",
    "ΒΟΑ_008_ΗΡΑ": "2026-04-16",
    "ΒΟΑ_009_ΣΙΔ": "2026-05-03",
    "ΒΟΑ_010_ΣΤΕ": "2026-05-03",
    "ΒΟΑ_013_ΒΑΡ": "2026-05-03",
    "ΒΟΑ_011_ΔΕΗ": "2026-04-09",
    "ΒΟΑ_012_ΚΟΚ": "2026-04-11",
    "ΑΜΘ_008_ΤΡΕ": "2026-04-17",
    "ΑΜΘ_009_ΜΑΚ": "2026-04-18",
    "ΑΜΘ_010_ΑΡΔ": "2026-04-19",
    "ΑΜΘ_011_ΔΙΔ": "2026-04-20",
    "ΑΜΘ_012_ΕΡΥ": "2026-04-21",
    "ΑΜΘ_013_ΚΥΠ": "2026-04-24",
    "ΑΜΘ_014_ΝΕΣ": "2026-04-04",
    "ΑΜΘ_015_ΕΙΣ": "2026-04-05",
    "ΑΜΘ_016_ΛΙΜ": "2026-04-06",
    "ΑΜΘ_017_ΕΞΟ": "2026-04-07",
    "ΑΜΘ_018_ΔΟΞ": "2026-04-10",
    "ΑΜΘ_019_ΚΑΛ": "2026-04-11",
    "ΑΜΘ_020_ΝΕΥ": "2026-04-12",
    "ΚΜΑ_001_Β01": "2026-05-02",
    "ΚΜΑ_002_Β07": "2026-05-03",
    "ΚΜΑ_003_Β08": "2026-05-01",
    "ΙΟΝ_007_ΑΓΟ": "2026-04-28",
    "ΙΟΝ_008_ΚΕΦ": "2026-04-25",
    "ΙΟΝ_009_ΞΙΦ": "2026-04-27",
    "ΙΟΝ_010_ΜΚΑ": "2026-04-28",
    "ΙΟΝ_011_ΧΕΙ": "2026-05-01",
    "ΙΟΝ_012_ΠΟΡ": "2026-05-03",
    "ΑΤΤ_036_ΚΟΣ": "2025-07-09",
    "ΠΕΛ_010_ΜΑΓ": "2026-02-21",
    "ΠΕΛ_015_ΥΦΙ": "2026-03-05",
    "ΠΕΛ_016_ΠΟΡ": "2026-03-04",
    "ΠΕΛ_017_ΚΑΣ": "2026-03-14",
    "ΠΕΛ_018_ΧΚΜ": "2026-04-28",
}


def _apply_monthly_date_overrides(data: Dict[str, Any], code_name: str) -> None:
    if code_name not in _MANUAL_MONTHLY_DATE_OVERRIDES:
        return
    new_date = _MANUAL_MONTHLY_DATE_OVERRIDES[code_name]
    for region in data.get("regions", []):
        for bridge in region.get("bridges", []):
            for site in bridge.get("sites", []):
                for sensor in site.get("sensors", []):
                    sensor["Monthly_1_Date_Start"] = new_date


def _apply_pilot_date_overrides(data: Dict[str, Any], code_name: str) -> None:
    if code_name not in _MANUAL_PILOT_DATE_OVERRIDES:
        return
    new_start, new_finish = _MANUAL_PILOT_DATE_OVERRIDES[code_name]
    for region in data.get("regions", []):
        for bridge in region.get("bridges", []):
            for site in bridge.get("sites", []):
                for sensor in site.get("sensors", []):
                    sensor["Pilot_Date_Start"] = new_start
                    sensor["Pilot_Date_Finish"] = new_finish


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_URL = BRIDGES_API_URL

# ---------------------------------------------------------------------------
# Region code prefixes recognised in code_names like "ΔΜΑ_ΒΕΘ15α_Γ002".
# Used only as a fallback: if the API returns 404 for such a name, retry
# without the prefix (e.g. "ΒΕΘ15α_Γ002") since some bridges are stored in
# the API without the region prefix.
# ---------------------------------------------------------------------------
KNOWN_REGION_CODES = {
    "ΑΤΤ", "ΚΜΑ", "ΘΕΣ", "ΣΤΕ", "ΙΟΝ", "ΠΕΛ", "ΑΜΘ", "ΒΟΑ", "ΚΡ", "ΝΟΑ",
    "ΔΜΑ", "ΔΕ", "ΗΠΕ",
}

RESENSYS_LOGIN_URL = PORTAL_LOGIN_URL
RESENSYS_REGISTRATION_URL = PORTAL_REGISTRATION_URL

# Credentials come from secrets/.env (never hardcode them)
_resensys_env = Path(__file__).parent / "secrets" / ".env"
if _resensys_env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_resensys_env)
    except ImportError:
        pass

RESENSYS_USERNAME = os.getenv("RESENSYS_USERNAME", "")
RESENSYS_PASSWORD = os.getenv("RESENSYS_PASSWORD", "")


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_bridge(code_name: str, timeout: int = 30) -> Dict[str, Any]:
    """Call the bridge API and return parsed JSON.

    If the API returns 404 and the code_name starts with a known region
    code prefix (e.g. "ΔΜΑ_ΒΕΘ15α_Γ002"), retry without the prefix
    (e.g. "ΒΕΘ15α_Γ002") since some bridges are stored that way.

    Applies MANUAL_MONTHLY_DATE_OVERRIDES to sensor Monthly_1_Date_Start
    and MANUAL_PILOT_DATE_OVERRIDES to Pilot_Date_Start / Pilot_Date_Finish
    before returning, so downstream consumers see the corrected dates.
    """
    resp = requests.get(API_URL, params={"code_name": code_name}, timeout=timeout)

    if resp.status_code == 404:
        parts = code_name.split("_")
        if len(parts) >= 2 and parts[0] in KNOWN_REGION_CODES:
            fallback_name = "_".join(parts[1:])
            print(f"     ! 404 for {code_name} - retrying without region prefix: {fallback_name}")
            fallback_resp = requests.get(API_URL, params={"code_name": fallback_name}, timeout=timeout)
            if fallback_resp.status_code == 200:
                data_fb = fallback_resp.json()
                region_ok = all(
                    region.get("region_code") == parts[0]
                    for region in data_fb.get("regions", [])
                )
                if region_ok:
                    print(f"     [OK] fallback matched region {parts[0]} for {code_name}")
                    resp = fallback_resp

    resp.raise_for_status()
    data = resp.json()
    _apply_monthly_date_overrides(data, code_name)
    _apply_pilot_date_overrides(data, code_name)
    return data


def fetch_site_ids(code_name: str, timeout: int = 30) -> list[str]:
    """Call the bridge API and extract all site_id values from the 'sites' array."""
    data = fetch_bridge(code_name, timeout=timeout)
    sites = data.get("sites", [])
    return [s["site_id"] for s in sites if "site_id" in s]


def resensys_login(
    username: str = RESENSYS_USERNAME,
    password: str = RESENSYS_PASSWORD,
    timeout: int = 30,
    max_attempts: int = 3,
) -> tuple[requests.Session, str]:
    """Full login to Resensys: GET login page for CSRF, POST credentials, return (session, csrftoken)."""
    import time
    import random
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    last_exc: Exception | None = None
    
    for attempt in range(max_attempts):
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            })
            # Step 1: follow the 301 redirect to get csrftoken cookie
            r0 = session.get(RESENSYS_LOGIN_URL, timeout=timeout, verify=False, allow_redirects=True)
            csrftoken_cookie = session.cookies.get("csrftoken", "")
            # Extract csrfmiddlewaretoken from form HTML (different from cookie value)
            import re as _re
            m = _re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', r0.text)
            csrf_form_token = m.group(1) if m else csrftoken_cookie
            # Step 2: POST to /login/ using form token
            login_post_url = PORTAL_LOGIN_URL + "/"
            r1 = session.post(
                login_post_url,
                data={"username": username, "password": password, "csrfmiddlewaretoken": csrf_form_token, "next": "/"},
                headers={"X-CSRFToken": csrf_form_token, "Referer": RESENSYS_LOGIN_URL},
                timeout=timeout,
                verify=False,
                allow_redirects=True,
            )
            r1.raise_for_status()
            print(f"     [debug] login response status: {r1.status_code}")
            print(f"     [debug] login cookies: {dict(session.cookies)}")
            csrftoken = session.cookies.get("csrftoken", csrf_form_token)
            return session, csrftoken
        except Exception as e:
            last_exc = e
            if attempt < max_attempts - 1:
                sleep_time = 2 ** attempt
                jitter = random.uniform(0.5, 1.5)
                actual_sleep = sleep_time * jitter
                print(f"     [debug] login attempt {attempt + 1}/{max_attempts} failed: {e}, retrying in {actual_sleep:.1f}s...")
                time.sleep(actual_sleep)
            else:
                print(f"     [debug] login POST exception: {e}")
                raise


def get_site_name(
    session: requests.Session,
    csrftoken: str,
    site_id: str,
    username: str = RESENSYS_USERNAME,
    timeout: int = 30,
) -> Dict[str, Any]:
    """POST to Resensys ajax/getSiteName/ and return parsed JSON."""
    url = PORTAL_SITENAME_URL
    r = session.post(
        url,
        data={"username": username, "SID": site_id},
        headers={
            "X-CSRFToken": csrftoken,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": portal_graph_referer(site_id),
        },
        timeout=timeout,
        verify=False,
    )
    r.raise_for_status()
    if not r.text:
        return {}
    return r.json()


# ---------------------------------------------------------------------------
# Flatten JSON -> {TAG: value} map
# ---------------------------------------------------------------------------
def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _clean_description(text: str) -> str:
    """Clean up description text: remove extra newlines, normalize whitespace."""
    if not text:
        return ""
    # Replace \r\n and \n with space
    text = text.replace("\r\n", " ").replace("\n", " ")
    # Replace multiple spaces with single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_tag_map(data: Dict[str, Any]) -> Dict[str, str]:
    """Flatten the API response into a flat {UPPERCASE_TAG: str} mapping.

    Rules:
      - Top-level scalar keys -> {KEY}
      - `bridge_info` keys exposed both as {KEY} and {BRIDGE_INFO_KEY}
      - Nested dicts flattened with `_` separator
      - Lists stored as JSON under their key (available for custom use)
    """
    tags: Dict[str, str] = {}

    # Handle new API structure: regions->bridges->bridge_info
    # Extract bridge_info from nested structure if present
    if "regions" in data and not "bridge_info" in data:
        for region in data.get("regions", []):
            for bridge in region.get("bridges", []):
                if "bridge_info" in bridge and bridge["bridge_info"] is not None:
                    # Flatten bridge_info into data for processing
                    data = {**data, **bridge}
                    break
                else:
                    # If bridge_info is null, use bridge-level fields
                    data = {**data, **bridge}
                    # Map location to BRIDGE_INFO_NAME for compatibility
                    if "location" in bridge:
                        data["BRIDGE_INFO_NAME"] = bridge["location"]
                break
            break  # Only process first bridge

    def add(key: str, value: Any) -> None:
        val = _stringify(value)
        # Clean up description fields
        if "DESCRIPTION" in key.upper():
            val = _clean_description(val)
        tags[key.upper()] = val

    for k, v in data.items():
        if isinstance(v, dict):
            for sub_k, sub_v in v.items():
                add(f"{k}_{sub_k}", sub_v)
            if k == "bridge_info":
                for sub_k, sub_v in v.items():
                    add(sub_k, sub_v)  # also as top-level shortcut
        else:
            add(k, v)

    # WGS84 -> EGSA87 conversion
    egsa_x, egsa_y = None, None
    try:
        lat = float(tags.get("BRIDGE_INFO_LAT") or tags.get("LAT", ""))
        lng = float(tags.get("BRIDGE_INFO_LNG") or tags.get("LNG", ""))
        if _ProjTransformer is None:
            raise ImportError("pyproj not installed")
        transformer = _ProjTransformer.from_crs("EPSG:4326", "EPSG:2100", always_xy=True)
        egsa_x, egsa_y = transformer.transform(lng, lat)
    except Exception:
        pass
    # Fallback: parse EGSA coords from description "(X, Y)" pattern
    if egsa_x is None:
        desc = tags.get("BRIDGE_INFO_DESCRIPTION", "") or tags.get("DESCRIPTION", "")
        m = re.search(r'\((\d+[\d.,]+)\s*,\s*(\d+[\d.,]+)\)', desc)
        if m:
            try:
                egsa_x = float(m.group(1).replace(",", "."))
                egsa_y = float(m.group(2).replace(",", "."))
            except ValueError:
                pass
    if egsa_x is not None:
        tags["EGSA_X"] = f"{egsa_x:.2f}"
        tags["EGSA_Y"] = f"{egsa_y:.2f}"
        tags["BRIDGE_INFO_EGSA_X"] = tags["EGSA_X"]
        tags["BRIDGE_INFO_EGSA_Y"] = tags["EGSA_Y"]

    # Derived split tags
    for base in ("BRIDGE_INFO_XWROTHETHSH", "XWROTHETHSH"):
        if base in tags:
            tags[f"{base}_0"] = tags[base].split("/")[0].strip()
    for base in ("BRIDGE_INFO_DIASTASEIS_ODOSTRWMATOS", "DIASTASEIS_ODOSTRWMATOS"):
        if base in tags:
            tags[f"{base}_0"] = tags[base].split("/")[0].strip()

    # Image URL - use image_urls from new API (first URL only)
    image_urls_raw = tags.get("BRIDGE_INFO_IMAGE_URLS") or tags.get("IMAGE_URLS", "")
    if image_urls_raw:
        try:
            urls = json.loads(image_urls_raw)
            if isinstance(urls, list) and urls:
                image_url = urls[0]
                tags["BRIDGE_INFO_IMAGE"] = image_url
                tags["IMAGE"] = image_url
        except json.JSONDecodeError:
            pass

    # Google Maps link from lat/lng
    lat = tags.get("BRIDGE_INFO_LAT") or tags.get("LAT")
    lng = tags.get("BRIDGE_INFO_LNG") or tags.get("LNG")
    if lat and lng:
        tags["GOOGLE_MAPS_LINK"] = f"https://www.google.com/maps?q={lat},{lng}"

    return tags
