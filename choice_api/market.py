from typing import Dict, Any

class MarketAPI:
    def __init__(self, client):
        self.client = client
        
    def get_market_status(self) -> Dict[str, Any]:
        """Retrieves current market status across segments."""
        return self.client.request("GET", "api/OpenAPI/MarketStatus")

    def get_user_profile(self) -> Dict[str, Any]:
        """Retrieves user profile information."""
        return self.client.request("GET", "api/OpenAPI/UserProfile")

    def get_multiple_touchline(self, multiple_seg_token: str) -> Dict[str, Any]:
        """
        Retrieves multiple touchlines.
        
        Args:
            multiple_seg_token: Comma-separated list of segments and tokens.
        """
        return self.client.request("POST", "api/OpenAPI/MultipleTouchline", {"MultipleSegToken": multiple_seg_token})
