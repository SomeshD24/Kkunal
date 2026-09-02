from typing import Dict, Any, Optional, Union, List, Tuple

class FundsAPI:
    def __init__(self, client):
        self.client = client
        
    def get_funds_view(self) -> Dict[str, Any]:
        """Retrieves funds summary."""
        return self.client.request("GET", "api/OpenAPI/FundsView")

    def get_funds_view_new(self) -> Dict[str, Any]:
        """Retrieves the new format funds summary."""
        return self.client.request("GET", "api/OpenAPI/FundsViewNew")

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

        Args:
            segment_id: Exchange segment ID (e.g., 1 = NSE Cash, 2 = NSE F&O, 3 = BSE Cash).
            token_qty: Pipe-separated Token and QTY (e.g., "48552|425").
                       For multiple contracts, pass tilde-separated string ("48552|425~48553|100"),
                       or a list of tuples/dicts/strings:
                       - [("48552", 425), ("48553", 100)]
                       - [{"token": 48552, "qty": 425}, {"token": 48553, "qty": 100}]
            mode: Mode integer (default 1).
            device_id: Device identifier (default "MAC").
            token: Optional single instrument token (if token_qty is not provided).
            qty: Optional single quantity (if token_qty is not provided).

        Returns:
            Dict containing API response: {"Status": "...", "Response": {...}, "Reason": "..."}
        """
        if token is not None and qty is not None:
            token_qty_str = f"{token}|{qty}"
        elif isinstance(token_qty, str):
            token_qty_str = token_qty
        elif isinstance(token_qty, (list, tuple)):
            parts = []
            for item in token_qty:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    parts.append(f"{item[0]}|{item[1]}")
                elif isinstance(item, dict):
                    t = item.get("token") or item.get("Token")
                    q = item.get("qty") or item.get("Qty") or item.get("quantity") or item.get("Quantity")
                    if t is not None and q is not None:
                        parts.append(f"{t}|{q}")
                    else:
                        raise ValueError(f"Invalid contract dictionary: {item}. Must contain token and qty.")
                elif isinstance(item, str):
                    parts.append(item)
                else:
                    raise ValueError(f"Unsupported contract item: {item}")
            token_qty_str = "~".join(parts)
        else:
            raise ValueError("Must provide either token_qty (e.g. '48552|425') or both token and qty arguments.")

        payload = {
            "SegmentId": segment_id,
            "Token_Qty": token_qty_str,
            "Mode": mode,
            "DeviceId": device_id
        }
        return self.client.request("POST", "api/OpenAPI/GetMargin", payload)

    calculate_margin = get_margin

    def process_payout(self, amount: float, bank_acc_no: str, product_type: int = 0) -> Dict[str, Any]:
        """Initiates a fund payout request."""
        payload = {
            "Amount": amount,
            "ProductType": product_type,
            "BankAccNo": bank_acc_no
        }
        return self.client.request("POST", "api/OpenAPI/ProcessPayout", payload)
        
    def payment_via_netbanking(self, amount: float, bank_acc_no: str, bank_ifsc_code: str, return_url: str,
                               segment_id: int, product_type: int = 0) -> Dict[str, Any]:
        """Initiates payment via net banking."""
        payload = {
            "Amount": amount,
            "BankAccNo": bank_acc_no,
            "BankIFSCCode": bank_ifsc_code,
            "ReturnUrl": return_url,
            "ProductType": product_type,
            "SegmentId": segment_id
        }
        return self.client.request("POST", "api/OpenAPI/PaymentViaNB", payload)

    def payment_via_hdfc_upi(self, amount: float, bank_acc_no: str, user_vpa: str, 
                             segment_id: int, product_type: int = 0) -> Dict[str, Any]:
        """Initiates payment via HDFC UPI."""
        payload = {
            "Amount": amount,
            "BankAccNo": bank_acc_no,
            "ProductType": product_type,
            "SegmentId": segment_id,
            "UserVPA": user_vpa
        }
        return self.client.request("POST", "api/OpenAPI/PaymentViaHDFCUPI", payload)

    def check_vpa(self, user_vpa: str) -> Dict[str, Any]:
        """Checks if a VPA is valid."""
        return self.client.request("POST", "api/OpenAPI/CheckVPA", {"UserVPA": user_vpa})

    def payment_via_razorpay(self, amount: float, bank_acc_no: str, bank_ifsc_code: str, upi_id: str,
                             segment_id: int, payment_type: int = 0, product_type: int = 0) -> Dict[str, Any]:
        """Initiates payment via RazorPay."""
        payload = {
            "Amount": amount,
            "BankAccNo": bank_acc_no,
            "BankIFSCCode": bank_ifsc_code,
            "ProductType": product_type,
            "PaymentType": payment_type,
            "UPIId": upi_id,
            "SegmentId": segment_id
        }
        return self.client.request("POST", "api/OpenAPI/PaymentViaRazorPay", payload)

    def payment_ack_response(self, transaction_id: str) -> Dict[str, Any]:
        """Acknowledges a payment response."""
        return self.client.request("POST", "api/OpenAPI/PaymentAckResponse", {"TransactionId": transaction_id})
