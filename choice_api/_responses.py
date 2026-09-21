"""Helpers for reading the {"Status": ..., "Response": ..., "Reason": ...} envelope."""

from typing import Any


def is_success(resp: Any) -> bool:
    """True when an API response body reports Status == Success (case-insensitive)."""
    return isinstance(resp, dict) and str(resp.get("Status", "")).strip().lower() == "success"


def failure_message(resp: Any) -> str:
    """The broker's own explanation from a failed response body."""
    if isinstance(resp, dict):
        for key in ("Reason", "Message", "Error"):
            if resp.get(key):
                return str(resp[key])
    return str(resp)[:300]
