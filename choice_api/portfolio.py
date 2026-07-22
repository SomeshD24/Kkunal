from typing import Dict, Any, List

class PortfolioAPI:
    def __init__(self, client):
        self.client = client
        
    def get_holdings(self) -> Dict[str, Any]:
        """Retrieves user's holdings."""
        return self.client.request("GET", "api/OpenAPI/Holdings")

    def get_net_position(self) -> Dict[str, Any]:
        """Retrieves the net position of the user."""
        return self.client.request("GET", "api/OpenAPI/NetPosition")

    def position_conversion(self, segment_id: int, token: int, client_order_no: int, buy_sell: int,
                            quantity: int, product_type: str, source_product_type: str, is_edis_req: bool = True) -> Dict[str, Any]:
        """Converts an open position from one product type to another."""
        payload = {
            "SegmentId": segment_id,
            "Token": token,
            "ClientOrderNo": client_order_no,
            "BuySell": buy_sell,
            "Quantity": quantity,
            "ProductType": product_type,
            "SourceProductType": source_product_type,
            "IsEDISReq": is_edis_req
        }
        return self.client.request("POST", "api/OpenAPI/PositionConversion", payload)

    def verify_dis(self, boid: str, ctr_boid: str, ex_id: int, channel: int, edis_stocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Verifies eDIS."""
        payload = {
            "BOID": boid,
            "CtrBOID": ctr_boid,
            "ExId": ex_id,
            "channel": channel,
            "eDISStocks": edis_stocks
        }
        return self.client.request("POST", "api/OpenAPI/VerifyDIS", payload)

    def get_dis_status(self) -> Dict[str, Any]:
        """Retrieves the DIS status."""
        return self.client.request("POST", "api/OpenAPI/GetDISStatus", {})
