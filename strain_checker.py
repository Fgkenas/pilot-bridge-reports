"""Strain checker for Bridge Report."""
from __future__ import annotations

from status_checker import BASE_PASS_WORDS, check_status

# Thin wrapper: the request/response handling lives in status_checker so
# all six checkers share one implementation. Only this module's endpoint,
# wording and metric names differ.


def check_strain(did: str) -> dict:
    """Check strain measurement quality for a device ID.

    Args:
        did: Device ID (sensor's device_id)

    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    return check_status(
        did,
        endpoint="strain_status",
        log_label="Strain",
        explain_good="Η ποιότητα των μετρήσεων του strain είναι ΚΑΛΗ με την τελευταία ακατέργαστη μέτρηση του strain να είναι {latest}.",
        explain_bad="Η ποιότητα των μετρήσεων του strain είναι ΑΔΥΝΑΜΗ, με την τελευταία ακατέργαστη μέτρηση του strain να είναι {latest}.",
        metrics=(("Strain", "latest"), ("Info2", "info2")),
        pass_words=BASE_PASS_WORDS + ("καλή", "καλό"),
    extra_logged_fields=("info2",),
    )
