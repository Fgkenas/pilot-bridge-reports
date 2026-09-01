"""Signal (RSSI) checker for Bridge Report."""
from __future__ import annotations

from status_checker import BASE_PASS_WORDS, check_status

# Thin wrapper: the request/response handling lives in status_checker so
# all six checkers share one implementation. Only this module's endpoint,
# wording and metric names differ.


def check_signal(did: str) -> dict:
    """Check RSSI signal status for a device ID.

    Args:
        did: Device ID (sensor's device_id)

    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    return check_status(
        did,
        endpoint="rssi_status",
        log_label="RSSI",
        explain_good="Το RSSI και το σήμα της συσκευής είναι ΚΑΛΑ με την τελευταία ανάγνωση να είναι {latest}.",
        explain_bad="Το RSSI και το σήμα της συσκευής είναι ΑΔΥΝΑΜΟ, με την τελευταία ένδειξη να είναι {latest}.",
        metrics=(("RSSI", "latest"),),
        pass_words=BASE_PASS_WORDS + ("καλό",),
    )
