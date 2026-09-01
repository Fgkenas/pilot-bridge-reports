"""
resensys_hosts.py
=================
The single place Resensys / Bridges-API host names live.

Before this module existed the same three hosts were hardcoded across 27
separate string literals in 10 files, so changing one (e.g. moving the v3
API from api.resensys.cloud to greece-api.resensys.cloud) meant editing
every copy and hoping none was missed. Import from here instead of
writing a host literal anywhere else.

Each value can be overridden from the environment (secrets/.env is
already loaded by the modules that read these), so a host change no
longer needs a code edit at all:

    RESENSYS_API_ROOT=https://api.resensys.cloud/api/v3
    RESENSYS_PORTAL_HOST=greece.resensys.cloud
    BRIDGES_API_HOST=lms4.globiled.com
"""
from __future__ import annotations

import os

# v3 REST API (X-API-Key auth) -- the *_status endpoints and data_acquire.
API_ROOT = os.getenv("RESENSYS_API_ROOT", "https://greece-api.resensys.cloud/api/v3")

# Legacy web portal (session-cookie auth) -- /login, /ajax/plot_if_active/, ...
PORTAL_HOST = os.getenv("RESENSYS_PORTAL_HOST", "greece.resensys.cloud")
PORTAL_BASE = f"https://{PORTAL_HOST}"
PORTAL_LOGIN_URL = f"{PORTAL_BASE}/login"
PORTAL_PLOT_URL = f"{PORTAL_BASE}/ajax/plot_if_active/"
PORTAL_SITENAME_URL = f"{PORTAL_BASE}/ajax/getSiteName/"
PORTAL_REGISTRATION_URL = f"{PORTAL_BASE}/api/v2/registration2"

# Globiled bridge metadata aggregator -- a separate system from Resensys,
# needs no authentication at all.
BRIDGES_API_HOST = os.getenv("BRIDGES_API_HOST", "lms4.globiled.com")
BRIDGES_API_URL = f"https://{BRIDGES_API_HOST}/Bridges_api/bridges_api_new.php"


def portal_graph_referer(site_id: str) -> str:
    """The Referer header plot_if_active/getSiteName expect, which embeds
    the site id being graphed."""
    return f"{PORTAL_BASE}/graph/{site_id}/"
