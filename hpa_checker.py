"""Accelerometer status, distinct from request or data availability errors."""
from status_client import fetch_status


def check_hpa(did):
    return fetch_status('hpa_status',did,'HPA')
