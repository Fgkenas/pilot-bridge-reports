"""HPA (accelerometer) checker for Bridge Report."""
from __future__ import annotations

from status_checker import BASE_PASS_WORDS, check_status

# Thin wrapper: the request/response handling lives in status_checker so
# all six checkers share one implementation. Only this module's endpoint,
# wording and metric names differ.


def check_hpa(did: str) -> dict:
    """Check HPA status for a device ID.

    Args:
        did: Device ID (sensor's device_id)

    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    return check_status(
        did,
        endpoint="hpa_status",
        log_label="HPA",
        explain_good="Οι μετρήσεις του επιταχυνσιογράφου είναι ΚΑΛΕΣ με την τελευταία μέτρηση για το HPA-Z να είναι {latest}.",
        explain_bad="Οι ενδείξεις του επιταχυνσιόμετρου είναι ΚΑΚΕΣ, με την τελευταία ένδειξη για το HPA-Z να είναι {latest}.",
        metrics=(("HPA", "latest"),),
        pass_words=BASE_PASS_WORDS + ("καλό",),
    )
