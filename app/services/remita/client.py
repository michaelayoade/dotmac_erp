"""
Remita API Client.

Handles HTTP communication with Remita API for RRR generation and payment status checking.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, cast

import httpx

logger = logging.getLogger(__name__)

REMITA_DEMO_URL = "https://demo.remita.net"
REMITA_LIVE_URL = "https://login.remita.net"


class RemitaError(Exception):
    """Remita API error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[str] = None,
        response_data: Optional[dict] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data


@dataclass
class RRRGenerateResponse:
    """Response from RRR generation."""

    rrr: str
    status_code: str
    status_message: str
    raw_response: dict


@dataclass
class RRRStatusResponse:
    """Response from RRR status check."""

    rrr: str
    status: str  # 00=success, 01=pending, 02=failed, etc.
    status_message: str
    amount: Optional[Decimal]
    transaction_id: Optional[str]
    payment_date: Optional[str]
    debitted_account: Optional[str]
    raw_response: dict


class RemitaClient:
    """
    HTTP client for Remita API.

    Handles RRR generation and payment status verification.
    Remita uses SHA512 hashing for authentication and returns JSONP-wrapped responses.
    """

    RRR_ENDPOINT = "/remita/exapp/api/v1/send/api/echannelsvc/merchant/api/paymentinit"
    STATUS_ENDPOINT = "/remita/exapp/api/v1/send/api/echannelsvc/{merchant_id}/{rrr}/{api_hash}/status.reg"

    def __init__(
        self,
        merchant_id: str,
        api_key: str,
        is_live: bool = False,
    ):
        """
        Initialize Remita client.

        Args:
            merchant_id: Remita merchant ID
            api_key: Remita API key
            is_live: True for production, False for demo/sandbox
        """
        self.merchant_id = merchant_id
        self.api_key = api_key
        self.base_url = REMITA_LIVE_URL if is_live else REMITA_DEMO_URL
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=30.0,
            )
        return self._client

    def _generate_hash(self, *args: str) -> str:
        """
        Generate SHA512 hash of concatenated arguments.

        Remita requires specific hash formats for different operations:
        - RRR generation: sha512(merchantId + serviceTypeId + orderId + amount + apiKey)
        - Status check: sha512(rrr + apiKey + merchantId)
        """
        concatenated = "".join(str(a) for a in args)
        return hashlib.sha512(concatenated.encode()).hexdigest()

    def _auth_header(self, api_hash: str) -> dict:
        """Build Remita authorization headers."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"remitaConsumerKey={self.merchant_id},remitaConsumerToken={api_hash}",
        }

    def _parse_jsonp_response(self, response_text: str) -> dict[str, Any]:
        """
        Parse Remita's JSONP response format.

        Remita wraps JSON in jsonp() callback:
        jsonp({"statuscode":"025","RRR":"310007769676","status":"Payment Reference generated"})

        Also handles standard JSON responses.
        """
        text = response_text.strip()

        # Try to extract JSON from jsonp wrapper
        jsonp_match = re.match(r"^jsonp\s*\((.*)\)$", text, re.DOTALL)
        if jsonp_match:
            json_str = jsonp_match.group(1)
            return cast(dict[str, Any], json.loads(json_str))

        # Try direct JSON parse
        try:
            return cast(dict[str, Any], json.loads(text))
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Remita response: {text[:200]}")
            raise RemitaError(f"Invalid response format: {str(e)}")

    def generate_rrr(
        self,
        service_type_id: str,
        amount: Decimal,
        order_id: str,
        payer_name: str,
        payer_email: str,
        payer_phone: Optional[str] = None,
        description: str = "",
    ) -> RRRGenerateResponse:
        """
        Generate RRR via Remita API.

        Args:
            service_type_id: Remita service type ID (biller-specific)
            amount: Payment amount
            order_id: Unique order ID (must be unique per request)
            payer_name: Name of the payer (company or individual)
            payer_email: Payer's email address
            payer_phone: Optional payer phone number
            description: Payment description

        Returns:
            RRRGenerateResponse with generated RRR

        Raises:
            RemitaError: If RRR generation fails
        """
        # Format amount to 2 decimal places as string
        amount_str = f"{amount:.2f}"

        # Generate hash: merchantId + serviceTypeId + orderId + amount + apiKey
        api_hash = self._generate_hash(
            self.merchant_id,
            service_type_id,
            order_id,
            amount_str,
            self.api_key,
        )

        payload = {
            "serviceTypeId": service_type_id,
            "amount": amount_str,
            "orderId": order_id,
            "payerName": payer_name,
            "payerEmail": payer_email,
            "payerPhone": payer_phone or "",
            "description": description,
        }

        client = self._get_client()
        logger.info(
            f"Generating RRR for service {service_type_id}, amount {amount_str}, order {order_id}"
        )

        try:
            response = client.post(
                self.RRR_ENDPOINT,
                headers=self._auth_header(api_hash),
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Remita RRR generation HTTP error: {e.response.text}")
            raise RemitaError(
                f"RRR generation failed: HTTP {e.response.status_code}",
                response_data={"http_status": e.response.status_code},
            )
        except httpx.RequestError as e:
            logger.error(f"Remita request error: {e}")
            raise RemitaError(f"Request failed: {str(e)}")

        data = self._parse_jsonp_response(response.text)
        logger.debug(f"Remita RRR response: {data}")

        # Remita status codes:
        # 025 = RRR generated successfully
        # 021 = Duplicate order ID
        # 027 = Invalid service type
        status_code = data.get("statuscode", "")

        if status_code == "025":
            logger.info(f"RRR generated successfully: {data.get('RRR')}")
            return RRRGenerateResponse(
                rrr=data["RRR"],
                status_code=status_code,
                status_message=data.get("status", "Success"),
                raw_response=data,
            )
        else:
            error_msg = data.get("status", "RRR generation failed")
            logger.error(f"RRR generation failed: {error_msg} (code: {status_code})")
            raise RemitaError(
                message=error_msg,
                status_code=status_code,
                response_data=data,
            )

    def check_status(self, rrr: str) -> RRRStatusResponse:
        """
        Check RRR payment status.

        Args:
            rrr: The RRR to check

        Returns:
            RRRStatusResponse with current payment status

        Raises:
            RemitaError: If status check fails
        """
        # Generate hash: rrr + apiKey + merchantId
        api_hash = self._generate_hash(rrr, self.api_key, self.merchant_id)

        url = self.STATUS_ENDPOINT.format(
            merchant_id=self.merchant_id,
            rrr=rrr,
            api_hash=api_hash,
        )

        client = self._get_client()
        logger.info(f"Checking status for RRR: {rrr}")

        try:
            response = client.get(url, headers=self._auth_header(api_hash))
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Remita status check HTTP error: {e.response.text}")
            raise RemitaError(
                f"Status check failed: HTTP {e.response.status_code}",
                response_data={"http_status": e.response.status_code},
            )
        except httpx.RequestError as e:
            logger.error(f"Remita request error: {e}")
            raise RemitaError(f"Request failed: {str(e)}")

        data = self._parse_jsonp_response(response.text)
        logger.debug(f"Remita status response: {data}")

        # Parse amount if present
        amount = None
        if data.get("amount"):
            try:
                amount = Decimal(str(data["amount"]))
            except (ValueError, TypeError):
                pass

        # Remita status codes for payment:
        # 00 = Successful payment
        # 01 = Pending (awaiting payment)
        # 02 = Payment failed
        # 021 = RRR not found
        return RRRStatusResponse(
            rrr=data.get("RRR", rrr),
            status=data.get("status", ""),
            status_message=data.get("message", data.get("status", "")),
            amount=amount,
            transaction_id=data.get("transactionId"),
            payment_date=data.get("paymentDate"),
            debitted_account=data.get("debittedAccount"),
            raw_response=data,
        )

    def close(self):
        """Close the HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
