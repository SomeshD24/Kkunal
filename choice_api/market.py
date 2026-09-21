from typing import Dict, Any, Iterable, Tuple, Union


class MarketAPI:
    def __init__(self, client):
        self.client = client

    def get_market_status(self) -> Dict[str, Any]:
        """Retrieves current market status across segments."""
        return self.client.request("GET", "api/OpenAPI/MarketStatus")

    def get_user_profile(self) -> Dict[str, Any]:
        """Retrieves user profile information."""
        return self.client.request("GET", "api/OpenAPI/UserProfile")

    def get_multiple_touchline(
        self,
        multiple_seg_token: Union[str, Iterable[Union[Tuple[int, int], Dict[str, Any]]]]
    ) -> Dict[str, Any]:
        """
        Retrieves touchlines (last price, best bid/ask) for several instruments at once.

        Args:
            multiple_seg_token: Either the raw string the API expects -
                "SegmentId@Token" pairs joined by commas, e.g. "1@2885,1@11536" -
                or a list of (segment_id, token) pairs / find_future()-style dicts,
                which is formatted for you:

                >>> client.market.get_multiple_touchline([(1, 2885), (1, 11536)])
                >>> fut = client.scrip_master.find_future("NIFTY")
                >>> client.market.get_multiple_touchline([fut])

                A string is passed through untouched.
        """
        if not isinstance(multiple_seg_token, str):
            pairs = []
            for item in multiple_seg_token:
                if isinstance(item, dict):
                    segment = item.get("Segment", item.get("segment_id"))
                    token = item.get("Token", item.get("token"))
                else:
                    segment, token = item
                if segment is None or token is None:
                    raise ValueError(f"Each instrument needs a segment and a token, got {item!r}")
                pairs.append(f"{int(segment)}@{int(token)}")
            if not pairs:
                raise ValueError("At least one instrument is required")
            multiple_seg_token = ",".join(pairs)

        return self.client.request(
            "POST", "api/OpenAPI/MultipleTouchline",
            {"MultipleSegToken": multiple_seg_token}, retry=True
        )
