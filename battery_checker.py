"""Battery status, preserving values even when the battery is weak."""
from status_client import fetch_status


def check_battery(did):
    return fetch_status('battery_status',did,'Battery Voltage')
