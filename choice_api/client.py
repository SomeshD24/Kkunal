import requests
import base64
import os
import json
import datetime
import logging
from typing import Dict, Any, Optional

from .exceptions import (
    AuthenticationError,
    APIResponseError,
    InvalidResponseError,
    NetworkError,
)

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

logger = logging.getLogger(__name__)

class ChoiceClient:
    """
    Main client for interacting with the Choice API.
    Handles authentication, session management, and provides access to other API modules.

    Available base URLs (use the module-level constants for convenience):
      - BASE_URL_OMNE  ->  "https://finxomne.choiceindia.com"  (default)
      - BASE_URL_FINX  ->  "https://finx.choiceindia.com"
    """
    def __init__(
        self,
        vendor_id: str,
        api_key: str,
        base_url: str = BASE_URL_OMNE,
        timeout: float = 30.0
    ):
        self.vendor_id = vendor_id
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        
        self.session_id: Optional[str] = None
        self.access_token: Optional[str] = None
        self.bcast_ip: Optional[str] = None
        self.bcast_port: Optional[int] = None
        
        # Initialize sub-modules
        self.orders = OrdersAPI(self)
        self.portfolio = PortfolioAPI(self)
        self.funds = FundsAPI(self)
        self.market = MarketAPI(self)
        self.historical = HistoricalAPI(self)
        self.indicators = IndicatorsAPI(self)
        self.scrip_master = ScripMaster()


    def _get_encoded_mobile(self, mobile_no: str) -> str:
        """Encodes the mobile number to Base64."""
        return base64.b64encode(mobile_no.encode('utf-8')).decode('utf-8')

    def get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        """Constructs headers required for API requests."""
        headers = {
            "VendorId": self.vendor_id,
            "Bearer": self.api_key,
            "Content-Type": "application/json"
        }
        if include_auth and self.session_id:
            headers["Authorization"] = f"SessionId {self.session_id}"
        return headers

    def request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
        timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        """Base method for making HTTP requests to the API."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self.get_headers(include_auth=require_auth)

        try:
            response = requests.request(
                method, url, headers=headers, json=data,
                timeout=self.timeout if timeout is None else timeout
            )
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Request to {endpoint} failed: {e}", endpoint=endpoint) from e

        if response.status_code in (401, 403):
            raise AuthenticationError(
                "Not authenticated. The session may have expired; call login() again.",
                status_code=response.status_code,
                response=response.text,
                endpoint=endpoint
            )

        if not response.ok:
            raise APIResponseError(
                f"HTTP {response.status_code} from {endpoint}",
                status_code=response.status_code,
                response=response.text,
                endpoint=endpoint
            )

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as e:
            raise InvalidResponseError(
                f"Response from {endpoint} was not valid JSON: {response.text[:200]}",
                status_code=response.status_code,
                response=response.text,
                endpoint=endpoint
            ) from e

    def login(self, mobile_no: str, session_file: Optional[str] = None) -> str:
        """
        Executes the full TOTP login flow to obtain a SessionId.

        Flow:
        1. LoginTOTP
        2. GetClientLoginTOTP (Retrieve OTP)
        3. ValidateTOTP (Submit OTP to get SessionId)

        Args:
            mobile_no: Registered mobile number.
            session_file: When given, a session saved there earlier today is
                reused instead of logging in again, and a fresh login is saved
                back to it. Sessions expire daily, so this is safe to leave on.

        Returns:
            The acquired SessionId.
        """
        if session_file and self.load_session(session_file):
            logger.info("Reusing today's session from %s", session_file)
            return self.session_id

        encoded_mobile = self._get_encoded_mobile(mobile_no)
        
        # Step 1: Request TOTP
        resp1 = self.request("POST", "api/OpenAPIV1/LoginTOTP", {"MobileNo": encoded_mobile}, require_auth=False)
        if resp1.get("Status") != "Success":
            raise AuthenticationError(
                f"LoginTOTP failed: {resp1.get('Reason') or resp1}", response=resp1
            )
            
        # Step 2: Get OTP generated
        resp2 = self.request("POST", "api/OpenAPIV1/GetClientLoginTOTP", {"MobileNo": encoded_mobile}, require_auth=False)
        if resp2.get("Status") != "Success":
            raise AuthenticationError(
                f"GetClientLoginTOTP failed: {resp2.get('Reason') or resp2}", response=resp2
            )

        otp = resp2.get("Response")
        if not otp:
            raise AuthenticationError(f"OTP not found in response: {resp2}", response=resp2)
            
        # Step 3: Validate TOTP
        resp3 = self.request("POST", "api/OpenAPIV1/ValidateTOTP", {"MobileNo": encoded_mobile, "OTP": str(otp)}, require_auth=False)
        if resp3.get("Status") != "Success":
            raise AuthenticationError(
                f"ValidateTOTP failed: {resp3.get('Reason') or resp3}", response=resp3
            )
            
        # Extract Session ID and other details
        response_data = resp3.get("Response", {})
        if isinstance(response_data, str):
            self.session_id = response_data
        elif isinstance(response_data, dict):
            self.session_id = response_data.get("SessionId") or response_data.get("session_id")
            self.access_token = response_data.get("AccessToken")
            self.bcast_ip = response_data.get("OdinBcastIP")
            
            bcast_port_raw = response_data.get("OdinBcastPort")
            if bcast_port_raw:
                try:
                    self.bcast_port = int(bcast_port_raw)
                except ValueError:
                    self.bcast_port = None
            
            # If AccessToken isn't present, fallback to the Bearer API_KEY since 
            # some versions of the API allow the same JWT to be reused for the WS.
            if not self.access_token:
                self.access_token = self.api_key
            
        if not self.session_id:
            raise AuthenticationError(
                "Failed to extract SessionId from ValidateTOTP response", response=resp3
            )

        # Automatically fetch the daily scrip master
        try:
            self.scrip_master.fetch()
        except Exception as e:
            logger.warning("Failed to fetch scrip master during login: %s", e)

        if session_file:
            self.save_session(session_file)

        return self.session_id

    def save_session(self, filepath: str) -> bool:
        """Saves the current active session to a file."""
        if not self.session_id:
            return False
        try:
            with open(filepath, 'w') as f:
                json.dump({
                    "date": datetime.date.today().isoformat(),
                    "session_id": self.session_id,
                    "access_token": self.access_token,
                    "bcast_ip": self.bcast_ip,
                    "bcast_port": self.bcast_port
                }, f)
            return True
        except OSError as e:
            logger.warning("Failed to save session file %s: %s", filepath, e)
            return False

    def load_session(self, filepath: str) -> bool:
        """Attempts to load a valid session for today from a file."""
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            if data.get("date") == datetime.date.today().isoformat():
                self.session_id = data.get("session_id")
                self.access_token = data.get("access_token")
                self.bcast_ip = data.get("bcast_ip")
                self.bcast_port = data.get("bcast_port")
                if not self.access_token:
                    self.access_token = self.api_key
                    
                # Pre-load scrip master
                try:
                    self.scrip_master.fetch()
                except Exception as e:
                    logger.warning("Failed to fetch scrip master: %s", e)
                return True
            return False
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("Could not load session from %s: %s", filepath, e)
            return False
        
    @property
    def is_authenticated(self) -> bool:
        """True once a SessionId has been acquired or restored."""
        return bool(self.session_id)

    def logoff(self) -> Dict[str, Any]:
        """Logs out the current session."""
        response = self.request("GET", "api/OpenAPI/LogOff")
        self.session_id = None
        return response

    def __enter__(self) -> "ChoiceClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Logs off on the way out, so a dropped session never lingers."""
        if self.session_id:
            try:
                self.logoff()
            except Exception as e:
                logger.warning("Logoff during context exit failed: %s", e)

    def __repr__(self) -> str:
        state = "authenticated" if self.session_id else "not logged in"
        return f"<ChoiceClient {self.base_url} ({state})>"
