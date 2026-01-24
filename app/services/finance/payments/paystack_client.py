"""
Paystack API Client.

Handles all HTTP communication with Paystack API.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = "https://api.paystack.co"


class PaystackError(Exception):
    """Paystack API error."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class PaystackConfig:
    """Configuration for Paystack API."""

    secret_key: str
    public_key: str
    webhook_secret: str


@dataclass
class InitializeResponse:
    """Response from transaction initialization."""

    authorization_url: str
    access_code: str
    reference: str


@dataclass
class VerifyResponse:
    """Response from transaction verification."""

    status: str  # success, failed, abandoned
    reference: str
    amount: int  # in kobo (smallest currency unit)
    currency: str
    transaction_id: str
    paid_at: Optional[str]
    channel: str  # card, bank, ussd, etc.
    gateway_response: str
    customer_email: str
    metadata: dict


@dataclass
class ResolveAccountResponse:
    """Response from account number resolution."""

    account_number: str
    account_name: str
    bank_id: int


@dataclass
class CreateRecipientResponse:
    """Response from transfer recipient creation."""

    recipient_code: str
    name: str
    type: str  # nuban, mobile_money, basa
    bank_code: str
    account_number: str


@dataclass
class InitiateTransferResponse:
    """Response from transfer initiation."""

    transfer_code: str
    reference: str
    status: str  # pending, success, failed
    amount: int
    currency: str


@dataclass
class VerifyTransferResponse:
    """Response from transfer verification."""

    status: str  # pending, success, failed, reversed
    reference: str
    amount: int
    currency: str
    transfer_code: str
    recipient_code: str
    completed_at: Optional[str]
    reason: Optional[str]  # failure reason if any
    fee: Optional[int] = None  # Fee in kobo charged for this transfer


@dataclass
class Bank:
    """Bank information."""

    name: str
    code: str
    country: str
    currency: str
    type: str  # nuban, mobile_money


class PaystackClient:
    """
    HTTP client for Paystack API.

    Handles transaction initialization, verification, and webhook signature
    verification.
    """

    def __init__(self, config: PaystackConfig):
        self.config = config
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=PAYSTACK_BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.config.secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    def initialize_transaction(
        self,
        email: str,
        amount: int,  # Amount in kobo (NGN * 100)
        reference: str,
        callback_url: str,
        metadata: Optional[dict] = None,
        currency: str = "NGN",
    ) -> InitializeResponse:
        """
        Initialize a payment transaction.

        Args:
            email: Customer email address
            amount: Amount in kobo (smallest currency unit)
            reference: Unique transaction reference
            callback_url: URL to redirect after payment
            metadata: Optional custom metadata
            currency: Currency code (default: NGN)

        Returns:
            InitializeResponse with authorization URL for customer redirect

        Raises:
            PaystackError: If initialization fails
        """
        payload: dict = {
            "email": email,
            "amount": amount,
            "reference": reference,
            "callback_url": callback_url,
            "currency": currency,
        }
        if metadata:
            payload["metadata"] = metadata

        client = self._get_client()
        try:
            response = client.post("/transaction/initialize", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack initialize failed: {e.response.text}")
            raise PaystackError(
                f"Failed to initialize transaction: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()

        if not data.get("status"):
            raise PaystackError(data.get("message", "Initialize failed"))

        return InitializeResponse(
            authorization_url=data["data"]["authorization_url"],
            access_code=data["data"]["access_code"],
            reference=data["data"]["reference"],
        )

    def verify_transaction(self, reference: str) -> VerifyResponse:
        """
        Verify a transaction by reference.

        Args:
            reference: Transaction reference

        Returns:
            VerifyResponse with transaction details

        Raises:
            PaystackError: If verification fails
        """
        client = self._get_client()
        try:
            response = client.get(f"/transaction/verify/{reference}")
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack verify failed: {e.response.text}")
            raise PaystackError(
                f"Failed to verify transaction: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()

        if not data.get("status"):
            raise PaystackError(data.get("message", "Verify failed"))

        d = data["data"]
        return VerifyResponse(
            status=d["status"],
            reference=d["reference"],
            amount=d["amount"],
            currency=d["currency"],
            transaction_id=str(d["id"]),
            paid_at=d.get("paid_at"),
            channel=d.get("channel", "unknown"),
            gateway_response=d.get("gateway_response", ""),
            customer_email=d["customer"]["email"],
            metadata=d.get("metadata") or {},
        )

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify Paystack webhook signature.

        Paystack signs webhooks with HMAC-SHA512 using the secret key.

        Args:
            payload: Raw request body bytes
            signature: X-Paystack-Signature header value

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.config.webhook_secret:
            logger.warning("Webhook secret not configured, skipping verification")
            return True

        expected = hmac.new(
            self.config.webhook_secret.encode("utf-8"),
            payload,
            hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    # =========================================================================
    # Transfer API (for expense reimbursements / payouts)
    # =========================================================================

    def list_banks(self, country: str = "nigeria", currency: str = "NGN") -> list[Bank]:
        """
        Get list of banks supported by Paystack.

        Args:
            country: Country to get banks for (default: nigeria)
            currency: Currency code (default: NGN)

        Returns:
            List of Bank objects
        """
        client = self._get_client()
        try:
            response = client.get(
                "/bank",
                params={"country": country, "currency": currency},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack list banks failed: {e.response.text}")
            raise PaystackError(
                f"Failed to list banks: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()
        if not data.get("status"):
            raise PaystackError(data.get("message", "List banks failed"))

        return [
            Bank(
                name=b["name"],
                code=b["code"],
                country=b.get("country", country),
                currency=b.get("currency", currency),
                type=b.get("type", "nuban"),
            )
            for b in data["data"]
        ]

    def resolve_account(
        self,
        account_number: str,
        bank_code: str,
    ) -> ResolveAccountResponse:
        """
        Verify an account number and get the account name.

        Args:
            account_number: Bank account number
            bank_code: Bank code (from list_banks)

        Returns:
            ResolveAccountResponse with verified account details
        """
        client = self._get_client()
        try:
            response = client.get(
                "/bank/resolve",
                params={
                    "account_number": account_number,
                    "bank_code": bank_code,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack resolve account failed: {e.response.text}")
            raise PaystackError(
                f"Failed to resolve account: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()
        if not data.get("status"):
            raise PaystackError(data.get("message", "Resolve account failed"))

        d = data["data"]
        return ResolveAccountResponse(
            account_number=d["account_number"],
            account_name=d["account_name"],
            bank_id=d.get("bank_id", 0),
        )

    def create_transfer_recipient(
        self,
        name: str,
        account_number: str,
        bank_code: str,
        currency: str = "NGN",
        recipient_type: str = "nuban",
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> CreateRecipientResponse:
        """
        Create a transfer recipient for payouts.

        Args:
            name: Recipient name (should match bank account name)
            account_number: Recipient bank account number
            bank_code: Bank code (from list_banks)
            currency: Currency code (default: NGN)
            recipient_type: Type of recipient (nuban, mobile_money, basa)
            description: Optional description
            metadata: Optional metadata

        Returns:
            CreateRecipientResponse with recipient_code for transfers
        """
        payload: dict = {
            "type": recipient_type,
            "name": name,
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": currency,
        }
        if description:
            payload["description"] = description
        if metadata:
            payload["metadata"] = metadata

        client = self._get_client()
        try:
            response = client.post("/transferrecipient", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack create recipient failed: {e.response.text}")
            raise PaystackError(
                f"Failed to create recipient: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()
        if not data.get("status"):
            raise PaystackError(data.get("message", "Create recipient failed"))

        d = data["data"]
        return CreateRecipientResponse(
            recipient_code=d["recipient_code"],
            name=d["name"],
            type=d["type"],
            bank_code=d["details"]["bank_code"],
            account_number=d["details"]["account_number"],
        )

    def initiate_transfer(
        self,
        amount: int,  # Amount in kobo
        recipient_code: str,
        reference: str,
        reason: Optional[str] = None,
        currency: str = "NGN",
    ) -> InitiateTransferResponse:
        """
        Initiate a transfer to a recipient.

        Args:
            amount: Amount in kobo (smallest currency unit)
            recipient_code: Recipient code from create_transfer_recipient
            reference: Unique transfer reference
            reason: Optional reason for transfer
            currency: Currency code (default: NGN)

        Returns:
            InitiateTransferResponse with transfer_code
        """
        payload: dict = {
            "source": "balance",  # Transfer from Paystack balance
            "amount": amount,
            "recipient": recipient_code,
            "reference": reference,
            "currency": currency,
        }
        if reason:
            payload["reason"] = reason

        client = self._get_client()
        try:
            response = client.post("/transfer", json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack initiate transfer failed: {e.response.text}")
            raise PaystackError(
                f"Failed to initiate transfer: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()
        if not data.get("status"):
            raise PaystackError(data.get("message", "Initiate transfer failed"))

        d = data["data"]
        return InitiateTransferResponse(
            transfer_code=d["transfer_code"],
            reference=d["reference"],
            status=d["status"],
            amount=d["amount"],
            currency=d["currency"],
        )

    def verify_transfer(self, reference: str) -> VerifyTransferResponse:
        """
        Verify a transfer by reference.

        Args:
            reference: Transfer reference

        Returns:
            VerifyTransferResponse with transfer details
        """
        client = self._get_client()
        try:
            response = client.get(f"/transfer/verify/{reference}")
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Paystack verify transfer failed: {e.response.text}")
            raise PaystackError(
                f"Failed to verify transfer: {e.response.text}",
                status_code=e.response.status_code,
            )
        except httpx.RequestError as e:
            logger.error(f"Paystack request error: {e}")
            raise PaystackError(f"Request failed: {str(e)}")

        data = response.json()
        if not data.get("status"):
            raise PaystackError(data.get("message", "Verify transfer failed"))

        d = data["data"]
        return VerifyTransferResponse(
            status=d["status"],
            reference=d["reference"],
            amount=d["amount"],
            currency=d["currency"],
            transfer_code=d["transfer_code"],
            recipient_code=d["recipient"]["recipient_code"],
            completed_at=d.get("completed_at"),
            reason=d.get("reason"),
            fee=d.get("fee") or d.get("fees"),  # Paystack uses 'fee' or 'fees'
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
