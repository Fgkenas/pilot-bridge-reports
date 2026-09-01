"""
resensys_time.py
================
Timezone-correct date handling for the legacy `plot_if_active` endpoint,
and for the epoch timestamps it returns.

Ported from GUI_Smart_Bridges/accel_core.py (`_local_and_utc_range`,
`_site_tz`), where these same three bugs were found and fixed. Before
this module existed, accel_fetcher.py, wls_raw_charts.py and
deformation_report_section.py each built their request window inline with
`- timedelta(hours=3)`, which was wrong in three separate ways:

  * the UTC conversion used a FIXED -3 h, which is Athens' SUMMER offset
    (EEST, UTC+3). Greece is UTC+2 (EET) from late October to late March,
    so every request covering the winter half of the year asked the
    server for a window shifted one hour off what its own local half
    said;

  * the local end was 23:59:59 but the UTC end was derived from the end
    date's MIDNIGHT (`end_dt - timedelta(hours=3)` -> 21:00:00 the
    PREVIOUS day), so the two halves of one request described windows
    ~27 h apart -- and the sent UTC start and end came out as the same
    instant, i.e. a zero-length window;

  * any clock time in the input was discarded, forcing whole days.

All of it now comes from one pair of tz-aware datetimes. Date-only input
still expands to a whole local day, so the Pilot_Date_Start/Finish values
this project actually passes behave exactly as intended.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# The timezone the Resensys portal reports this deployment's local time
# in. Sent to the server as `local_timezone` on every plot_if_active
# request, so the same value has to drive our own conversions.
RESENSYS_TIMEZONE = "Europe/Athens"


def site_tz() -> ZoneInfo:
    """The deployment's timezone, falling back to UTC if the system has
    no tz database entry for it (rather than raising mid-report)."""
    try:
        return ZoneInfo(RESENSYS_TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def local_and_utc_range(t_start: str, t_end: str) -> tuple[str, str, str, str]:
    """Turn 'YYYY-MM-DD' (or 'YYYY-MM-DD HH:MM:SS') start/end into the
    four strings plot_if_active wants: a local MM/DD/YYYY range and the
    SAME two instants expressed in UTC.

    Returns (start_local, end_local, start_utc, end_utc). Date-only input
    expands to 00:00:00 -> 23:59:59 of that local day; a supplied clock
    time is preserved instead of being thrown away.
    """
    tz = site_tz()

    def _parse(value: str, *, is_end: bool) -> datetime:
        text = str(value).strip()
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except ValueError:
            pass  # date-only input: fall through to the whole-day window
        day = datetime.strptime(text[:10], "%Y-%m-%d")
        return (day.replace(hour=23, minute=59, second=59, tzinfo=tz) if is_end
                else day.replace(hour=0, minute=0, second=0, tzinfo=tz))

    start_dt = _parse(t_start, is_end=False)
    end_dt = _parse(t_end, is_end=True)

    start_local = start_dt.strftime("%m/%d/%Y %H:%M:%S")
    end_local = end_dt.strftime("%m/%d/%Y %H:%M:%S")
    start_utc = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_utc = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return start_local, end_local, start_utc, end_utc


def timestamps_to_site_local(raw_x) -> list[datetime]:
    """Convert plot_if_active's epoch seconds to NAIVE datetimes in the
    site's timezone.

    Replaces `datetime.fromtimestamp(ts)`, which converts using whatever
    timezone the machine generating the report happens to be in -- right
    in Athens, silently shifted anywhere else, even though the request
    itself declares `local_timezone: Europe/Athens`. Naive (tzinfo
    stripped) on purpose: charts and DOCX tables downstream format these
    directly, and a tz-aware value would render with an offset suffix
    they don't expect.
    """
    tz = site_tz()
    return [datetime.fromtimestamp(ts, tz=timezone.utc)
            .astimezone(tz).replace(tzinfo=None) for ts in raw_x]
