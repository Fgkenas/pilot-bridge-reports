"""Battery status checker for Bridge Report."""
from __future__ import annotations

from status_checker import BASE_PASS_WORDS, check_status

# Thin wrapper: the request/response handling lives in status_checker so
# all six checkers share one implementation. Only this module's endpoint,
# wording and metric names differ.


def check_battery(did: str) -> dict:
    """Check battery status for a device ID.

    Args:
        did: Device ID (sensor's device_id)

    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    return check_status(
        did,
        endpoint="battery_status",
        log_label="Battery",
        explain_good="Η μπαταρία είναι ΚΑΛΗ με την τελευταία ανάγνωση να είναι {latest}.",
        explain_bad="Η μπαταρία είναι ΑΔΥΝΑΜΗ, με την τελευταία ένδειξη να είναι {latest}.",
        metrics=(("Battery Voltage", "latest"),),
        pass_words=BASE_PASS_WORDS,
    )
