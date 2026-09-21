import threading
import time
import logging
from typing import Dict, Any, Optional, Union, List, Tuple

from .exceptions import OrderValidationError

logger = logging.getLogger(__name__)

_ORDER_NO_LOCK = threading.Lock()
_LAST_ORDER_NO = 0


def _next_client_order_no() -> int:
    """
    Generates a unique, increasing ClientOrderNo that stays inside a 32-bit int.
    Steps forward by one if several orders are placed within the same millisecond.
    """
    global _LAST_ORDER_NO
    with _ORDER_NO_LOCK:
        candidate = int(time.time() * 1000) % 2_000_000_000
        if candidate <= _LAST_ORDER_NO:
            candidate = _LAST_ORDER_NO + 1
        _LAST_ORDER_NO = candidate
        return candidate


def _validate_order(bs: int, qty: int, price: float, trigger_price: float, disclosed_qty: int = 0) -> None:
    """Rejects an obviously malformed order locally, before it can reach the exchange."""
    if int(bs) not in (1, 2):
        raise OrderValidationError(f"bs must be 1 (Buy) or 2 (Sell), got {bs!r}")
    if isinstance(qty, bool) or int(qty) != qty or qty <= 0:
        raise OrderValidationError(f"qty must be a positive whole number, got {qty!r}")
    if price is None or price < 0:
        raise OrderValidationError(f"price must be zero or positive (in paisa), got {price!r}")
    if trigger_price is None or trigger_price < 0:
        raise OrderValidationError(f"trigger_price must be zero or positive (in paisa), got {trigger_price!r}")
    if disclosed_qty < 0 or disclosed_qty > qty:
        raise OrderValidationError(f"disclosed_qty must be between 0 and qty, got {disclosed_qty!r}")
    # Prices travel in paisa, which are whole numbers. A fractional value is
    # the classic sign that rupees were passed by mistake (1300.5 vs 130050).
    if float(price) != int(price):
        logger.warning(
            "Order price %r is not a whole number. Prices are in PAISA, not rupees - "
            "use to_paisa() if this was meant as rupees.", price
        )


class OrdersAPI:
    def __init__(self, client):
        self.client = client
        
    def place_order(self, segment_id: int, token: int, order_type: str, bs: int, qty: int,
                    price: float, trigger_price: float, validity: int, product_type: str, 
                    disclosed_qty: int = 0, is_edis_req: bool = False,
                    client_order_no: Optional[int] = None) -> Dict[str, Any]:
        """
        Places a new order.

        Args:
            client_order_no: Your own reference number for this order, used later by
                modify_order/cancel_order. Left as None, a unique one is generated.
        """
        _validate_order(bs, qty, price, trigger_price, disclosed_qty)
        if client_order_no is None:
            client_order_no = _next_client_order_no()

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
            "ClientOrderNo": client_order_no
        }
        # Never retried: a request that timed out may still have reached the exchange.
        return self.client.request("POST", "api/OpenAPI/V2/NewOrder", payload, retry=False, is_order=True)

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
        _validate_order(bs, qty, price, trigger_price, disclosed_qty)
        return self.client.request("POST", "api/OpenAPI/ModifyOrder", payload, retry=False, is_order=True)

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
        return self.client.request("POST", "api/OpenAPI/CancelOrder", payload, retry=False, is_order=True)

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
        return self.client.request("POST", "api/OpenAPI/OrderMessages", {"ReqId": req_id}, retry=True)

    def get_margin(
        self,
        segment_id: int,
        token_qty: Optional[Union[str, List[Union[Tuple[Any, Any], Dict[str, Any], str]]]] = None,
        mode: int = 1,
        device_id: str = "MAC",
        token: Optional[Union[int, str]] = None,
        qty: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Calculates required margin for single or multiple contracts.
        Convenience wrapper delegating to FundsAPI.get_margin.
        """
        return self.client.funds.get_margin(
            segment_id=segment_id,
            token_qty=token_qty,
            mode=mode,
            device_id=device_id,
            token=token,
            qty=qty
        )

    calculate_margin = get_margin
