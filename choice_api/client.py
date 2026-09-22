import requests
import base64
import os
import json
import time
import random
import datetime
import logging
import tempfile
import threading
from email.utils import parsedate_to_datetime
from typing import Dict, Any, Optional, Tuple, Union

from .exceptions import (
    ChoiceAPIError,
    AuthenticationError,
    APIResponseError,
    InvalidResponseError,
    NetworkError,
    RateLimitError,
    StaticIPError,
    remember_secret,
)
from .ratelimit import TokenBucket
from ._responses import is_success, failure_message

from .orders import OrdersAPI
from .portfolio import PortfolioAPI
from .funds import FundsAPI
from .market import MarketAPI
from .historical import HistoricalAPI
from .scrip_master import ScripMaster
from .indicators import IndicatorsAPI

# Available base URL endpoints
BASE_URL_OMNE = "https://finxomne.choiceindia.com"  # Default / legacy endpoint
BASE_URL_FINX = "https://finx.choiceindia.com"       # Alternate endpoint

# Both hosts serve the same API. When one is unreachable the other usually
# still answers, so retry-safe requests fail over between them.
GATEWAYS = (BASE_URL_OMNE, BASE_URL_FINX)

# A 401 from a login endpoint means "these credentials are wrong", not "the
# session expired", so the automatic re-login must never fire for them.
#
# These endpoints also generate and deliver a one-time password, so they are
# never retried either: a retry cannot tell "the server never saw it" from
# "the server sent an OTP and the reply was lost", and the second case puts
# another message on the user's phone.
_AUTH_ENDPOINT_PREFIX = "api/OpenAPIV1/"

# Substrings of a 4xx body that identify the failure more precisely than the
# status code does. Kept narrow on purpose: a bare "session" would also match
# "market session closed" on an ordinary order rejection.
_STATIC_IP_MARKERS = ("static ip", "ip not", "invalid ip", "ip address", "whitelist")
_AUTH_MARKERS = ("invalid session", "session expired", "session has expired",
                 "session not found", "unauthor", "invalid token", "token expired",
                 "not logged in", "please login", "please log in", "re-login", "relogin")

_MAX_RETRY_AFTER = 60.0

ENV_VENDOR_ID = "CHOICE_VENDOR_ID"
ENV_API_KEY = "CHOICE_API_KEY"
ENV_BASE_URL = "CHOICE_BASE_URL"
ENV_MOBILE_NO = "CHOICE_MOBILE_NO"

logger = logging.getLogger(__name__)


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Seconds to wait from a Retry-After header, numeric or HTTP-date. Capped."""
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        seconds = (when - datetime.datetime.now(tz=datetime.timezone.utc)).total_seconds()
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER)


class ChoiceClient:
    """
    Main client for interacting with the Choice API.
    Handles authentication, session management, and provides access to other API modules.

    Available base URLs (use the module-level constants for convenience):
      - BASE_URL_OMNE  ->  "https://finxomne.choiceindia.com"  (default)
      - BASE_URL_FINX  ->  "https://finx.choiceindia.com"

    Args:
        vendor_id: Vendor id issued with the API key.
        api_key: JWT API key.
        base_url: Gateway to use.
        timeout: Seconds before an HTTP call is abandoned. A (connect, read)
            tuple is also accepted.
        max_retries: Extra attempts for retry-safe requests (GETs and read-only
            POSTs such as ChartData) on timeouts, HTTP 429 and 5xx. Requests that
            place, modify or cancel orders or move funds are never retried.
        rate_limit: Optional cap on data requests per second.
        order_rate_limit: Optional cap on order requests per second. Exchanges
            require strategies sending 10+ orders/second to be registered as algos.
        auto_relogin: Log in again and replay the request once when the session
            is rejected mid-day. Needs a prior login() in this process. Each
            re-login sends the user a new OTP, so see relogin_cooldown.
        relogin_cooldown: Seconds during which a freshly issued session will not be
            replaced again. A session rejected this soon after being issued is not
            an expired one, so re-logging in would only send more OTPs.
        failover: Switch between the two gateways when one is unreachable.
        raise_on_error: Raise APIResponseError when a response body reports a
            Status other than Success, instead of returning it to the caller.
    """
    def __init__(
        self,
        vendor_id: str,
        api_key: str,
        base_url: str = BASE_URL_OMNE,
        timeout: Union[float, Tuple[float, float]] = 30.0,
        max_retries: int = 2,
        rate_limit: Optional[float] = None,
        order_rate_limit: Optional[float] = None,
        auto_relogin: bool = True,
        relogin_cooldown: float = 120.0,
        failover: bool = True,
        raise_on_error: bool = False
    ):
        if not vendor_id or not api_key:
            raise ValueError("vendor_id and api_key are both required")

        self.vendor_id = vendor_id
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.active_base_url = self.base_url
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.auto_relogin = auto_relogin
        self.relogin_cooldown = max(0.0, float(relogin_cooldown))
        self.failover = failover
        self.raise_on_error = raise_on_error

        self.session_id: Optional[str] = None
        self.access_token: Optional[str] = None
        self.bcast_ip: Optional[str] = None
        self.bcast_port: Optional[int] = None

        self._http = requests.Session()
        self._auth_lock = threading.RLock()
        self._data_bucket = TokenBucket(rate_limit) if rate_limit else None
        self._order_bucket = TokenBucket(order_rate_limit) if order_rate_limit else None
        self._mobile_no: Optional[str] = None
        self._session_file: Optional[str] = None
        self._login_date: Optional[datetime.date] = None
        # Far enough in the past that a session loaded from disk may be refreshed.
        self._session_created_at = time.monotonic() - relogin_cooldown

        remember_secret(api_key)

        # Initialize sub-modules
        self.orders = OrdersAPI(self)
        self.portfolio = PortfolioAPI(self)
        self.funds = FundsAPI(self)
        self.market = MarketAPI(self)
        self.historical = HistoricalAPI(self)
        self.indicators = IndicatorsAPI(self)
        self.scrip_master = ScripMaster()

    @classmethod
    def from_env(cls, **kwargs: Any) -> "ChoiceClient":
        """
        Builds a client from environment variables, keeping credentials out of source:

            CHOICE_VENDOR_ID, CHOICE_API_KEY   (required)
            CHOICE_BASE_URL                    (optional)

        login() then falls back to CHOICE_MOBILE_NO when called without a number.
        """
        vendor_id = os.environ.get(ENV_VENDOR_ID)
        api_key = os.environ.get(ENV_API_KEY)
        if not vendor_id or not api_key:
            raise ValueError(f"Set {ENV_VENDOR_ID} and {ENV_API_KEY} in the environment first.")
        if os.environ.get(ENV_BASE_URL) and "base_url" not in kwargs:
            kwargs["base_url"] = os.environ[ENV_BASE_URL]
        return cls(vendor_id=vendor_id, api_key=api_key, **kwargs)

    def _get_encoded_mobile(self, mobile_no: str) -> str:
        """Encodes the mobile number to Base64."""
        return base64.b64encode(mobile_no.encode('utf-8')).decode('utf-8')

    def get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        """Constructs headers required for API requests."""
        # Note the unusual scheme: the API key travels in a header literally
        # named "Bearer", while "Authorization" carries "SessionId <id>".
        headers = {
            "VendorId": self.vendor_id,
            "Bearer": self.api_key,
            "Content-Type": "application/json"
        }
        if include_auth and self.session_id:
            headers["Authorization"] = f"SessionId {self.session_id}"
        return headers

    # ------------------------------------------------------------ transport

    def _classify_client_error(self, response: "requests.Response", endpoint: str) -> ChoiceAPIError:
        """Turns a 4xx response into the most specific exception available."""
        body = response.text or ""
        low = body.lower()
        code = response.status_code
        if any(marker in low for marker in _STATIC_IP_MARKERS):
            return StaticIPError(f"HTTP {code} from {endpoint}.",
                                 status_code=code, response=body, endpoint=endpoint)
        if code in (401, 403) or any(marker in low for marker in _AUTH_MARKERS):
            return AuthenticationError(
                "Not authenticated. The session may have expired; call login() again.",
                status_code=code, response=body, endpoint=endpoint
            )
        return APIResponseError(f"HTTP {code} from {endpoint}: {body[:300]}",
                                status_code=code, response=body, endpoint=endpoint)

    def _switch_gateway(self) -> bool:
        """Moves to the other Choice host. Only when running on a known gateway."""
        if not self.failover or self.active_base_url not in GATEWAYS:
            return False
        other = GATEWAYS[1] if self.active_base_url == GATEWAYS[0] else GATEWAYS[0]
        logger.warning("Gateway %s unreachable; switching to %s", self.active_base_url, other)
        self.active_base_url = other
        return True

    def _relogin(self, failed_session: Optional[str]) -> bool:
        """
        Re-authenticates after a rejected session. True if a fresh session is ready.

        A session that was issued only moments ago and is already being rejected is
        not an expired session - the cause is something else (a wrong vendor id, an
        undeclared IP, an endpoint the account cannot use). Logging in again would
        not fix it and would send the user another OTP for every request they make,
        so within `relogin_cooldown` of the last login this refuses and lets the
        AuthenticationError reach the caller.
        """
        if not self.auto_relogin or not self._mobile_no:
            return False
        with self._auth_lock:
            # Another thread may already have replaced the dead session.
            if self.session_id and self.session_id != failed_session:
                return True

            age = time.monotonic() - self._session_created_at
            if age < self.relogin_cooldown:
                logger.error(
                    "A session issued %.0fs ago was rejected, so logging in again will not help - not requesting another OTP. Check the vendor id, API key and that this machine's public IP is the one registered for the key.", age
                )
                return False

            logger.info("Session rejected after %.0fs; logging in again", age)
            self.login(self._mobile_no, session_file=self._session_file, force=True)
            return True

    def request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
        timeout: Optional[Union[float, Tuple[float, float]]] = None,
        retry: Optional[bool] = None,
        is_order: bool = False,
        _retry_auth: bool = True
    ) -> Dict[str, Any]:
        """
        Base method for making HTTP requests to the API.

        Args:
            retry: Whether a timeout, HTTP 429 or 5xx may be retried. Defaults to
                True for GET and False otherwise - a POST that timed out may
                still have been executed, so only read-only POSTs opt in.
            is_order: Paces the call with the order rate limit instead of the
                data one.
        """
        method = method.upper()
        path = endpoint.lstrip('/')
        is_auth_call = path.startswith(_AUTH_ENDPOINT_PREFIX)
        if retry is None:
            retry = method == "GET"
        if is_auth_call:
            # Each attempt would cost the user another OTP.
            retry = False
        attempts = (self.max_retries if retry else 0) + 1
        bucket = self._order_bucket if is_order else self._data_bucket
        last_error: Optional[Exception] = None

        for attempt in range(attempts):
            if bucket:
                bucket.acquire()

            session_used = self.session_id
            url = f"{self.active_base_url}/{path}"
            try:
                response = self._http.request(
                    method, url,
                    headers=self.get_headers(include_auth=require_auth),
                    json=data,
                    timeout=self.timeout if timeout is None else timeout
                )
            except requests.exceptions.RequestException as e:
                hint = (" The order may or may not have reached the exchange - "
                        "check the order book before sending it again.") if is_order else ""
                last_error = NetworkError(f"Request to {path} failed: {e}.{hint}", endpoint=path)
                last_error.__cause__ = e
                if retry:
                    self._switch_gateway()
            else:
                if response.status_code == 429:
                    retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                    if bucket:
                        bucket.penalise(retry_after or 1.0)
                    last_error = RateLimitError(
                        f"Rate limited by the broker on {path}",
                        retry_after=retry_after,
                        status_code=429, response=response.text, endpoint=path
                    )
                elif response.status_code >= 500:
                    last_error = APIResponseError(
                        f"HTTP {response.status_code} from {path}: {(response.text or '')[:300]}",
                        status_code=response.status_code, response=response.text, endpoint=path
                    )
                elif response.status_code >= 400:
                    error = self._classify_client_error(response, path)
                    # A dead session is recoverable exactly once: log in again and
                    # replay. A rejected request was never executed, so this is
                    # safe even for orders.
                    if (isinstance(error, AuthenticationError)
                            and not isinstance(error, StaticIPError)
                            and _retry_auth and not is_auth_call and require_auth
                            and self._relogin(session_used)):
                        return self.request(method, endpoint, data, require_auth=require_auth,
                                            timeout=timeout, retry=retry, is_order=is_order,
                                            _retry_auth=False)
                    raise error
                else:
                    try:
                        parsed = response.json()
                    except (json.JSONDecodeError, ValueError) as e:
                        raise InvalidResponseError(
                            f"Response from {path} was not valid JSON: {(response.text or '')[:200]}",
                            status_code=response.status_code,
                            response=response.text,
                            endpoint=path
                        ) from e

                    if self.raise_on_error and not is_auth_call and isinstance(parsed, dict) \
                            and "Status" in parsed and not is_success(parsed):
                        raise APIResponseError(
                            f"{path} failed: {failure_message(parsed)}",
                            status_code=response.status_code, response=parsed, endpoint=path
                        )
                    return parsed

            if attempt < attempts - 1:
                delay = min(8.0, 0.5 * (2 ** attempt)) * (0.5 + random.random())
                if isinstance(last_error, RateLimitError) and last_error.retry_after:
                    delay = max(delay, last_error.retry_after)
                logger.warning("%s failed (%s); retry %d/%d in %.1fs",
                               path, last_error, attempt + 1, attempts - 1, delay)
                time.sleep(delay)

        assert last_error is not None
        raise last_error

    # ----------------------------------------------------------------- auth

    def _login_failure(self, step: str, resp: Any) -> AuthenticationError:
        message = failure_message(resp)
        if any(marker in message.lower() for marker in _STATIC_IP_MARKERS):
            return StaticIPError(f"{step} failed: {message}.", response=resp)
        return AuthenticationError(f"{step} failed: {message}", response=resp)

    def login(
        self,
        mobile_no: Optional[str] = None,
        session_file: Optional[str] = None,
        force: bool = False
    ) -> str:
        """
        Executes the full TOTP login flow to obtain a SessionId.

        Flow:
        1. LoginTOTP
        2. GetClientLoginTOTP (Retrieve OTP)
        3. ValidateTOTP (Submit OTP to get SessionId)

        Args:
            mobile_no: Registered mobile number. Falls back to the
                CHOICE_MOBILE_NO environment variable.
            session_file: When given, a session saved there earlier today is
                reused instead of logging in again, and a fresh login is saved
                back to it. If the saved session turns out to be dead, the next
                request logs in again transparently (see auto_relogin).
            force: Request a new session even if one is already held. Every login
                sends the user an OTP, so calling login() again when this client
                already has today's session reuses it instead.

        Returns:
            The acquired SessionId.
        """
        mobile_no = mobile_no or os.environ.get(ENV_MOBILE_NO)
        if not mobile_no:
            raise ValueError(f"mobile_no is required (or set {ENV_MOBILE_NO}).")

        with self._auth_lock:
            self._mobile_no = str(mobile_no)
            self._session_file = session_file

            # Logging in again would send another OTP for a session we already have.
            if not force and self.session_id and self._login_date == datetime.date.today():
                logger.debug("Reusing the session this client already holds")
                return self.session_id

            if session_file and not force and self.load_session(session_file):
                logger.info("Reusing today's session from %s", session_file)
                return self.session_id  # type: ignore[return-value]

            # Logged at INFO so the cause of every OTP the user receives is visible:
            #   logging.basicConfig(level=logging.INFO)
            logger.info("Requesting a new session - this sends an OTP to the registered mobile.")
            encoded_mobile = self._get_encoded_mobile(self._mobile_no)

            # Step 1: Request TOTP
            resp1 = self.request("POST", "api/OpenAPIV1/LoginTOTP", {"MobileNo": encoded_mobile}, require_auth=False)
            if not is_success(resp1):
                raise self._login_failure("LoginTOTP", resp1)

            # Step 2: Get OTP generated
            resp2 = self.request("POST", "api/OpenAPIV1/GetClientLoginTOTP", {"MobileNo": encoded_mobile}, require_auth=False)
            if not is_success(resp2):
                raise self._login_failure("GetClientLoginTOTP", resp2)

            otp = resp2.get("Response")
            if otp in (None, ""):
                raise AuthenticationError("GetClientLoginTOTP returned no OTP", response=resp2)

            # Step 3: Validate TOTP. Not retried: a timed-out attempt may have consumed the OTP.
            resp3 = self.request("POST", "api/OpenAPIV1/ValidateTOTP", {"MobileNo": encoded_mobile, "OTP": str(otp)}, require_auth=False)
            if not is_success(resp3):
                raise self._login_failure("ValidateTOTP", resp3)

            # Extract Session ID and other details
            response_data = resp3.get("Response")
            session_id: Optional[str] = None
            access_token: Optional[str] = None
            if isinstance(response_data, str):
                session_id = response_data
            elif isinstance(response_data, dict):
                session_id = response_data.get("SessionId") or response_data.get("session_id")
                access_token = response_data.get("AccessToken")
                self.bcast_ip = response_data.get("OdinBcastIP")

                bcast_port_raw = response_data.get("OdinBcastPort")
                if bcast_port_raw:
                    try:
                        self.bcast_port = int(bcast_port_raw)
                    except (TypeError, ValueError):
                        self.bcast_port = None

            if not session_id:
                raise AuthenticationError(
                    "Failed to extract SessionId from ValidateTOTP response", response=resp3
                )

            self.session_id = session_id
            self._session_created_at = time.monotonic()
            self._login_date = datetime.date.today()
            # If AccessToken isn't present, fall back to the Bearer API_KEY since
            # some versions of the API allow the same JWT to be reused for the WS.
            # This must cover the plain-string response too, or the price feed
            # logs on with an empty token.
            self.access_token = access_token or self.api_key
            remember_secret(self.session_id)
            remember_secret(self.access_token)

            # Automatically fetch the daily scrip master
            if not self.scrip_master.is_loaded:
                try:
                    self.scrip_master.fetch()
                except Exception as e:
                    logger.warning("Failed to fetch scrip master during login: %s", e)

            if session_file:
                self.save_session(session_file)

            return self.session_id

    def save_session(self, filepath: str) -> bool:
        """
        Saves the current active session to a file.

        The file holds a live session id in plaintext, so it is written
        atomically and readable by the current user only (where the OS allows).
        """
        if not self.session_id:
            return False
        payload = json.dumps({
            "date": datetime.date.today().isoformat(),
            "session_id": self.session_id,
            "access_token": self.access_token,
            "bcast_ip": self.bcast_ip,
            "bcast_port": self.bcast_port
        })
        directory = os.path.dirname(os.path.abspath(filepath))
        try:
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".session-", suffix=".tmp")
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(payload)
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass
                os.replace(tmp_path, filepath)
            except BaseException:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise
            return True
        except OSError as e:
            logger.warning("Failed to save session file %s: %s", filepath, e)
            return False

    def load_session(self, filepath: str) -> bool:
        """
        Attempts to load a valid session for today from a file.

        Sessions do not survive the trading day, so a file from any other date
        is ignored: a stale session is worse than none.
        """
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("Could not load session from %s: %s", filepath, e)
            return False

        if not isinstance(data, dict) or not data.get("session_id"):
            return False
        if data.get("date") != datetime.date.today().isoformat():
            return False

        self.session_id = data["session_id"]
        self._login_date = datetime.date.today()
        # A session restored from disk is already hours old as far as the cooldown
        # is concerned: if it is dead, re-logging in is the right move.
        self._session_created_at = time.monotonic() - self.relogin_cooldown
        self.access_token = data.get("access_token") or self.api_key
        self.bcast_ip = data.get("bcast_ip")
        self.bcast_port = data.get("bcast_port")
        remember_secret(self.session_id)
        remember_secret(self.access_token)

        # Pre-load scrip master
        if not self.scrip_master.is_loaded:
            try:
                self.scrip_master.fetch()
            except Exception as e:
                logger.warning("Failed to fetch scrip master: %s", e)
        return True

    @property
    def is_authenticated(self) -> bool:
        """True once a SessionId has been acquired or restored."""
        return bool(self.session_id)

    def logoff(self) -> Dict[str, Any]:
        """Logs out the current session, and discards any saved copy of it."""
        try:
            response = self.request("GET", "api/OpenAPI/LogOff", _retry_auth=False)
        finally:
            self.session_id = None
            self._login_date = None
            # A logged-off session must never be reloaded from disk.
            if self._session_file and os.path.exists(self._session_file):
                try:
                    os.remove(self._session_file)
                except OSError as e:
                    logger.warning("Could not remove session file %s: %s", self._session_file, e)
        return response

    def close(self) -> None:
        """Releases pooled HTTP connections. The session itself stays valid."""
        self._http.close()

    def __enter__(self) -> "ChoiceClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """
        Logs off on the way out - unless the session is being persisted with
        session_file, in which case it is left alive for the next run to reuse.
        """
        try:
            if self.session_id and not self._session_file:
                try:
                    self.logoff()
                except Exception as e:
                    logger.warning("Logoff during context exit failed: %s", e)
        finally:
            self.close()

    def __repr__(self) -> str:
        state = "authenticated" if self.session_id else "not logged in"
        return f"<ChoiceClient {self.active_base_url} ({state})>"
