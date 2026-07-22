from typing import Dict, Any, Optional

class FundsAPI:
    def __init__(self, client):
        self.client = client
        
    def get_funds_view(self) -> Dict[str, Any]:
        """Retrieves funds summary."""
        return self.client.request("GET", "api/OpenAPI/FundsView")

    def get_funds_view_new(self) -> Dict[str, Any]:
        """Retrieves the new format funds summary."""
        return self.client.request("GET", "api/OpenAPI/FundsViewNew")

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
