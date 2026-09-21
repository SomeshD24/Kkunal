"""
Exception types raised by choice_api (Kkunal).

All of them derive from ChoiceAPIError, so a single `except ChoiceAPIError`
catches everything this library raises while still letting you handle
authentication, transport and rejected requests separately.

Example:
    >>> from choice_api import ChoiceAPIError, AuthenticationError, RateLimitError
    >>> try:
    ...     client.orders.place_order(...)
    ... except AuthenticationError:
    ...     client.login(mobile_no, force=True)   # session expired, log in again
    ... except RateLimitError as e:
    ...     time.sleep(e.retry_after or 1)
    ... except ChoiceAPIError as e:
    ...     print(e.status_code, e.response)

Credentials never reach an exception message or a log line: every message is
passed through `scrub()`, which redacts anything shaped like a secret as well
as the literal API key / session id / access token the client is holding.
The raw body stays available on the `.response` attribute for programmatic use.
"""

import re
import threading
from typing import Any, Optional, Set

# --- secret redaction ---------------------------------------------------------

_SECRET_KEYS = (
    r"(?:Bearer|VendorId|vendor_?id|api_?key|AccessToken|access_token|SessionId"
    r"|session_?id|OTP|MobileNo|mobile_?no|password)"
)

_SECRET_PATTERNS = (
    # key: value / key = value, value optionally quoted with either quote style
    # (a dict repr uses ', JSON uses ").
    re.compile(r"""(['"]?""" + _SECRET_KEYS + r"""['"]?\s*[:=]\s*)(['"]?)([^'",;&\s}\]]{4,})""", re.I),
    # "SessionId abc123" - whitespace separated, as in the Authorization header.
    re.compile(r"\b(" + _SECRET_KEYS + r")(\s+)([A-Za-z0-9._-]{6,})", re.I),
    # JWTs, wherever they appear.
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9._-]+"),
)

_known_secrets: Set[str] = set()
_known_lock = threading.Lock()
_MIN_SECRET_LENGTH = 8


def remember_secret(value: Any) -> None:
    """Registers a credential so it is redacted wherever it later appears."""
    text = str(value or "")
    if len(text) < _MIN_SECRET_LENGTH:
        return
    with _known_lock:
        _known_secrets.add(text)


def scrub(text: Any) -> str:
    """Redacts anything that looks like a credential, or is known to be one."""
    s = str(text)
    with _known_lock:
        # Longest first, so a secret containing another is not half-replaced.
        secrets = sorted(_known_secrets, key=len, reverse=True)
    for secret in secrets:
        s = s.replace(secret, "<redacted>")
    s = _SECRET_PATTERNS[0].sub(r"\1\2<redacted>", s)
    s = _SECRET_PATTERNS[1].sub(r"\1\2<redacted>", s)
    s = _SECRET_PATTERNS[2].sub("<redacted-jwt>", s)
    return s


# --- exception hierarchy ------------------------------------------------------

class ChoiceAPIError(Exception):
    """Base class for every error raised by this library."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response: Optional[Any] = None,
        endpoint: Optional[str] = None
    ):
        self.message = scrub(message)
        super().__init__(self.message)
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


class StaticIPError(AuthenticationError):
    """
    The request did not originate from the static IP registered for this API key.

    Choice binds every API key to declared static IPs and rejects everything
    else, which makes this the most common failure when a script is run from a
    different network, a VPN, or a cloud host with dynamic egress.
    """

    HINT = (
        "Request rejected as coming from an undeclared IP. Check that your current "
        "public IP matches the static IP registered against this API key "
        "(finx.choiceindia.com -> Profile -> Settings -> Generate API Key). "
        "VPNs and proxies will always fail this check."
    )

    def __init__(self, message: str = "", **kwargs: Any):
        super().__init__(f"{message} {self.HINT}".strip(), **kwargs)


class APIResponseError(ChoiceAPIError):
    """
    The request reached the API but was rejected.

    `response` holds the decoded body, so the broker's own message is available:
        >>> except APIResponseError as e:
        ...     print(e.response)
    """


class RateLimitError(APIResponseError):
    """HTTP 429: the broker is throttling. `retry_after` holds the wait in seconds, if given."""

    def __init__(self, message: str, retry_after: Optional[float] = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class HistoricalDataError(APIResponseError):
    """
    ChartData answered with a non-Success status.

    Distinct from an empty result: this means the request *failed* (bad token,
    unsupported interval, expired session), not that the window has no candles.
    """


class OrderValidationError(ChoiceAPIError, ValueError):
    """An order was rejected locally, before being sent, because an argument is invalid."""


class NetworkError(ChoiceAPIError):
    """The request never completed: timeout, DNS failure, connection reset."""


class InvalidResponseError(ChoiceAPIError):
    """A 2xx response whose body could not be decoded as JSON."""


class ScripMasterError(ChoiceAPIError):
    """The daily scrip master could not be downloaded or parsed."""


class AmbiguousSymbolError(ScripMasterError, KeyError):
    """
    A symbol matched several instruments and no unique one could be chosen.

    Typical for derivatives, where hundreds of contracts share one Symbol.
    `candidates` lists a sample of the matching SecDesc values.
    """

    def __init__(self, message: str, candidates: Optional[list] = None):
        super().__init__(message)
        self.candidates = candidates or []

    def __str__(self) -> str:          # KeyError would repr() the message
        return self.message


class WebSocketError(ChoiceAPIError):
    """A live feed or interactive socket failed."""


__all__ = [
    "ChoiceAPIError",
    "AuthenticationError",
    "StaticIPError",
    "APIResponseError",
    "RateLimitError",
    "HistoricalDataError",
    "OrderValidationError",
    "NetworkError",
    "InvalidResponseError",
    "ScripMasterError",
    "AmbiguousSymbolError",
    "WebSocketError",
    "scrub",
    "remember_secret",
]
