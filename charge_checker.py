"""Charge checker for Bridge Report."""
from __future__ import annotations

from status_checker import BASE_PASS_WORDS, check_status

# Thin wrapper: the request/response handling lives in status_checker so
# all six checkers share one implementation. Only this module's endpoint,
# wording and metric names differ.


def check_charge(did: str) -> dict:
    """Check charge status for a device ID.

    Args:
        did: Device ID (sensor's device_id)

    Returns:
        Dict with keys: ok, raw, latest_time, explain, metrics
    """
    return check_status(
        did,
        endpoint="charge_status",
        log_label="Charge",
        explain_good="Η φόρτιση είναι ΚΑΛΉ με τη μέγιστη ένδειξη να είναι {latest}.",
        explain_bad="Η φόρτιση είναι ΑΔΥΝΑΜΗ, με τη μέγιστη ένδειξη να είναι {latest}.",
        metrics=(("Charge", "latest"),),
        pass_words=BASE_PASS_WORDS + ("καλό",),
    )
