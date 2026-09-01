"""Water level sensor (WLS) checker for Bridge Report."""
from __future__ import annotations

from status_checker import BASE_PASS_WORDS, check_status

# Thin wrapper: the request/response handling lives in status_checker so
# all six checkers share one implementation. Only this module's endpoint,
# wording and metric names differ.


def check_wls(did: str) -> dict:
    """Check water level status for a device ID.

    Args:
        did: Device ID (sensor's device_id)

    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    return check_status(
        did,
        endpoint="wls_status",
        log_label="WLS",
        explain_good="Η μέτρηση της στάθμης είναι ΚΑΛΗ με την τελευταία μέτρηση να είναι {latest}.",
        explain_bad="Η μέτρηση της στάθμης είναι ΑΔΥΝΑΜΗ, με την τελευταία μέτρηση να είναι {latest}.",
        metrics=(("Water Level", "latest"), ("Latest", "latest")),
        pass_words=BASE_PASS_WORDS + ("καλό",),
    )
