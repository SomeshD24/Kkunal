from typing import Dict, Any, Optional

class OrdersAPI:
    def __init__(self, client):
        self.client = client
        
    def place_order(self, segment_id: int, token: int, order_type: str, bs: int, qty: int,
                    price: float, trigger_price: float, validity: int, product_type: str, 
                    disclosed_qty: int = 0, is_edis_req: bool = False) -> Dict[str, Any]:
        """Places a new order."""
        payload = {
            "SegmentId": segment_id,
            "Token": token,
            "OrderType": order_type,
            "BS": bs,
            "Qty": qty,
            "DisclosedQty": disclosed_qty,
            "Price": price,
            "TriggerPrice": trigger_price,
            "Validity": validity,
            "ProductType": product_type,
            "IsEdisReq": is_edis_req,
            "Remarks": "API",
            "ModeTyp": "WEBAPI",
            "Mode": 1,
            "DeviceId": "MAC",
            "ClientOrderNo": 123456
        }
        return self.client.request("POST", "api/OpenAPI/V2/NewOrder", payload)

    def modify_order(self, client_order_no: int, exchange_order_no: str, gateway_order_no: str,
                     segment_id: int, token: int, order_type: str, bs: int, qty: int, price: float,
                     trigger_price: float, validity: int, product_type: str, disclosed_qty: int = 0) -> Dict[str, Any]:
        """Modifies an existing order."""
        payload = {
            "ClientOrderNo": client_order_no,
            "ExchangeOrderNo": exchange_order_no,
            "GatewayOrderNo": gateway_order_no,
            "SegmentId": segment_id,
            "Token": token,
            "OrderType": order_type,
            "BS": bs,
            "Qty": qty,
            "DisclosedQty": disclosed_qty,
            "Price": price,
            "TriggerPrice": trigger_price,
            "Validity": validity,
            "ProductType": product_type
        }
        return self.client.request("POST", "api/OpenAPI/ModifyOrder", payload)

    def cancel_order(self, client_order_no: int, exchange_order_no: str, gateway_order_no: str,
                     segment_id: int, token: int, order_type: str, bs: int, qty: int, price: float,
                     trigger_price: float, validity: int, product_type: str, exchange_order_time: str = "",
                     disclosed_qty: int = 0) -> Dict[str, Any]:
        """Cancels an order."""
        payload = {
            "ExchangeOrderTime": exchange_order_time,
            "ClientOrderNo": client_order_no,
            "ExchangeOrderNo": exchange_order_no,
            "GatewayOrderNo": gateway_order_no,
            "SegmentId": segment_id,
            "Token": token,
            "OrderType": order_type,
            "BS": bs,
            "Qty": qty,
            "DisclosedQty": disclosed_qty,
            "Price": price,
            "TriggerPrice": trigger_price,
            "Validity": validity,
            "ProductType": product_type
        }
        return self.client.request("POST", "api/OpenAPI/CancelOrder", payload)

    def get_order_book(self) -> Dict[str, Any]:
        """Retrieves the full order book."""
        return self.client.request("GET", "api/OpenAPI/OrderBook")

    def get_order_book_v2(self) -> Dict[str, Any]:
        """Retrieves order book version 2."""
        return self.client.request("GET", "api/OpenAPI/OrderBookV2")

    def get_order_by_no(self, order_no: int) -> Dict[str, Any]:
        """Retrieves a specific order by its order number."""
        return self.client.request("GET", f"api/OpenAPI/OrderBookByOrderNo/{order_no}")

    def get_trade_book(self) -> Dict[str, Any]:
        """Retrieves the trade book."""
        return self.client.request("GET", "api/OpenAPI/TradeBook")

    def get_order_messages(self, req_id: str) -> Dict[str, Any]:
        """Retrieves order messages."""
        return self.client.request("POST", "api/OpenAPI/OrderMessages", {"ReqId": req_id})
