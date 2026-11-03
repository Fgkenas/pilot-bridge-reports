"""Share the report request budget across every status checker."""
import requests
from api_rate_limiter import acquire_slot, release_slot, wait_for_gap


def post_status(*args,**kwargs):
    kwargs.setdefault('timeout',(5,60))
    acquire_slot()
    try:
        wait_for_gap()
        return requests.post(*args,**kwargs)
    finally:release_slot()
