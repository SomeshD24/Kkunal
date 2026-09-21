"""
Exception types raised by choice_api (Kkunal).

All of them derive from ChoiceAPIError, so a single `except ChoiceAPIError`
catches everything this library raises while still letting you handle
authentication, transport and rejected requests separately.

Example:
    >>> from choice_api import ChoiceAPIError, AuthenticationError
    >>> try:
    ...     client.orders.place_order(...)
    ... except AuthenticationError:
    ...     client.login(mobile_no)          # session expired, log in again
    ... except ChoiceAPIError as e:
    ...     print(e.status_code, e.response)
"""

from typing import Any, Dict, Optional


class ChoiceAPIError(Exception):
    """Base class for every error raised by this library."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response: Optional[Any] = None,
        endpoint: Optional[str] = None
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response = response
        self.endpoint = endpoint

    def __str__(self) -> str:
        parts = [self.message]
        if self.endpoint:
            parts.append(f"endpoint={self.endpoint}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        return " | ".join(parts)


class AuthenticationError(ChoiceAPIError):
    """Login failed, or the session is missing/expired (HTTP 401/403)."""


class APIResponseError(ChoiceAPIError):
    """
    The request reached the API but was rejected.

    `response` holds the decoded body, so the broker's own Reason is available:
        >>> except APIResponseError as e:
        ...     print(e.response.get("Reason"))
    """


class NetworkError(ChoiceAPIError):
    """The request never completed: timeout, DNS failure, connection reset."""


class InvalidResponseError(ChoiceAPIError):
    """A 2xx response whose body could not be decoded as JSON."""


class ScripMasterError(ChoiceAPIError):
    """The daily scrip master could not be downloaded or parsed."""


class WebSocketError(ChoiceAPIError):
    """A live feed or interactive socket failed."""


__all__ = [
    "ChoiceAPIError",
    "AuthenticationError",
    "APIResponseError",
    "NetworkError",
    "InvalidResponseError",
    "ScripMasterError",
    "WebSocketError",
]
