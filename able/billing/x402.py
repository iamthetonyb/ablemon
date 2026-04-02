"""
x402 Payment Protocol Integration for ABLE

Implements the x402 standard (https://x402.org) for internet-native payments.
When clients access ABLE API endpoints, the server can require payment via
the HTTP 402 status code + X-PAYMENT header flow.

Flow:
1. Client requests a paid resource (e.g., POST /api/chat)
2. Server returns 402 with PaymentRequirements JSON
3. Client signs a payment payload and retries with X-PAYMENT header
4. Server verifies payment via the x402 facilitator
5. Server serves the response and includes X-PAYMENT-RESPONSE header

Integration with ABLE billing:
- Costs tracked by the existing BillingTracker
- Client rates applied as markup over provider costs
- Usage logged per-session for invoice generation
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from aiohttp import web
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


# ── Data Models ──────────────────────────────────────────────


@dataclass
class PaymentRequirement:
    """What the server requires for payment (returned in 402 response)."""
    scheme: str = "exact"  # "exact" for fixed-price
    network: str = "base"  # Blockchain network (base, base-sepolia, etc.)
    max_amount_required: str = "0"  # Amount in smallest unit (e.g., 1000000 = $1 USDC)
    asset: str = ""  # Token contract address (USDC on Base)
    pay_to: str = ""  # Recipient wallet address
    resource: str = ""  # The resource URL being accessed
    description: str = ""  # Human-readable description
    mime_type: str = "application/json"
    max_timeout_seconds: int = 60
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "scheme": self.scheme,
            "network": self.network,
            "maxAmountRequired": self.max_amount_required,
            "asset": self.asset,
            "payTo": self.pay_to,
            "resource": self.resource,
            "description": self.description,
            "mimeType": self.mime_type,
            "maxTimeoutSeconds": self.max_timeout_seconds,
            "extra": self.extra,
        }


@dataclass
class PaymentConfig:
    """x402 configuration for ABLE."""
    enabled: bool = False
    # Wallet address to receive payments
    pay_to_address: str = ""
    # Blockchain network
    network: str = "base"  # "base" for mainnet, "base-sepolia" for testnet
    # USDC contract address on Base
    usdc_address: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # Base mainnet USDC
    # x402 facilitator URL (verifies payments)
    facilitator_url: str = "https://x402.org/facilitator"
    # Pricing (in USDC micro-units: 1 USDC = 1,000,000)
    price_per_request: int = 10000  # $0.01 per request
    price_per_1k_tokens: int = 50000  # $0.05 per 1K tokens
    # Rate limiting
    max_requests_per_minute: int = 60

    @classmethod
    def from_env(cls) -> "PaymentConfig":
        """Load config from environment variables."""
        return cls(
            enabled=os.environ.get("X402_ENABLED", "false").lower() == "true",
            pay_to_address=os.environ.get("X402_PAY_TO_ADDRESS", ""),
            network=os.environ.get("X402_NETWORK", "base"),
            usdc_address=os.environ.get(
                "X402_USDC_ADDRESS",
                "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
            ),
            facilitator_url=os.environ.get(
                "X402_FACILITATOR_URL",
                "https://x402.org/facilitator"
            ),
            price_per_request=int(os.environ.get("X402_PRICE_PER_REQUEST", "10000")),
            price_per_1k_tokens=int(os.environ.get("X402_PRICE_PER_1K_TOKENS", "50000")),
        )


# ── x402 Middleware ──────────────────────────────────────────


class X402PaymentGate:
    """
    Middleware that enforces x402 payment requirements on API endpoints.

    Checks for X-PAYMENT header, verifies payment via the facilitator,
    and tracks usage in the billing system.
    """

    def __init__(self, config: PaymentConfig, billing_tracker=None):
        self.config = config
        self.billing_tracker = billing_tracker
        self._session: Optional[aiohttp.ClientSession] = None
        # Simple in-memory rate limiter
        self._request_counts: Dict[str, List[float]] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    def build_payment_requirements(
        self,
        resource_url: str,
        amount: Optional[int] = None,
        description: str = "ABLE API access",
    ) -> Dict:
        """
        Build the 402 response body with payment requirements.

        Args:
            resource_url: The URL the client is trying to access
            amount: Override amount in USDC micro-units (default: config price)
            description: Human-readable description

        Returns:
            PaymentRequirements JSON dict
        """
        req = PaymentRequirement(
            scheme="exact",
            network=self.config.network,
            max_amount_required=str(amount or self.config.price_per_request),
            asset=self.config.usdc_address,
            pay_to=self.config.pay_to_address,
            resource=resource_url,
            description=description,
            max_timeout_seconds=60,
            extra={
                "name": "USDC",
                "version": "2",
            },
        )

        return {
            "x402Version": 1,
            "error": "X-PAYMENT header is required",
            "accepts": [req.to_dict()],
        }

    async def verify_payment(self, x_payment_header: str, resource_url: str) -> Dict:
        """
        Verify a payment via the x402 facilitator.

        Args:
            x_payment_header: The base64-encoded X-PAYMENT header value
            resource_url: The resource being accessed

        Returns:
            Dict with verification result: {"valid": bool, "transaction": str, ...}
        """
        try:
            # Decode the payment payload
            payment_payload = json.loads(base64.b64decode(x_payment_header))
        except (json.JSONDecodeError, Exception) as e:
            return {"valid": False, "error": f"Invalid payment payload: {e}"}

        # Verify via facilitator
        session = await self._get_session()
        try:
            async with session.post(
                f"{self.config.facilitator_url}/verify",
                json={
                    "payload": payment_payload,
                    "requirements": {
                        "scheme": "exact",
                        "network": self.config.network,
                        "maxAmountRequired": str(self.config.price_per_request),
                        "asset": self.config.usdc_address,
                        "payTo": self.config.pay_to_address,
                        "resource": resource_url,
                    },
                },
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return {
                        "valid": result.get("valid", False),
                        "transaction": result.get("transaction", ""),
                        "network": result.get("network", self.config.network),
                        "payer": result.get("payer", ""),
                        "amount": result.get("amount", "0"),
                    }
                else:
                    error_text = await resp.text()
                    return {"valid": False, "error": f"Facilitator error: {resp.status}: {error_text}"}

        except Exception as e:
            logger.error(f"x402 facilitator verification failed: {e}")
            return {"valid": False, "error": str(e)}

    def check_rate_limit(self, client_id: str) -> bool:
        """Check if client is within rate limits."""
        now = time.time()
        window = 60.0  # 1 minute window

        if client_id not in self._request_counts:
            self._request_counts[client_id] = []

        # Clean old entries
        self._request_counts[client_id] = [
            t for t in self._request_counts[client_id] if now - t < window
        ]

        if len(self._request_counts[client_id]) >= self.config.max_requests_per_minute:
            return False

        self._request_counts[client_id].append(now)
        return True

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── aiohttp Middleware ────────────────────────────────────────


def x402_middleware(payment_gate: X402PaymentGate, protected_paths: List[str] = None):
    """
    aiohttp middleware that enforces x402 payments on specified paths.

    Usage:
        gate = X402PaymentGate(config)
        app = web.Application(middlewares=[x402_middleware(gate, ["/api/chat"])])
    """
    protected = set(protected_paths or ["/api/chat", "/api/completion"])

    @web.middleware
    async def middleware(request: web.Request, handler):
        # Only enforce on protected paths
        if request.path not in protected:
            return await handler(request)

        # Check for X-PAYMENT header
        x_payment = request.headers.get("X-PAYMENT")

        if not x_payment:
            # Return 402 with payment requirements
            requirements = payment_gate.build_payment_requirements(
                resource_url=str(request.url),
                description=f"ABLE API: {request.path}",
            )
            return web.json_response(
                requirements,
                status=402,
                headers={"X-PAYMENT-VERSION": "1"},
            )

        # Verify the payment
        verification = await payment_gate.verify_payment(
            x_payment, str(request.url)
        )

        if not verification.get("valid"):
            return web.json_response(
                {
                    "error": "Payment verification failed",
                    "details": verification.get("error", "Unknown error"),
                },
                status=402,
            )

        # Rate limit check
        payer = verification.get("payer", "unknown")
        if not payment_gate.check_rate_limit(payer):
            return web.json_response(
                {"error": "Rate limit exceeded"},
                status=429,
            )

        # Payment verified — process the request
        response = await handler(request)

        # Add payment response header
        payment_response = base64.b64encode(
            json.dumps({
                "success": True,
                "transaction": verification.get("transaction", ""),
                "network": verification.get("network", ""),
                "payer": payer,
            }).encode()
        ).decode()

        response.headers["X-PAYMENT-RESPONSE"] = payment_response

        # Track in billing
        if payment_gate.billing_tracker:
            try:
                from billing.tracker import UsageRecord
                payment_gate.billing_tracker.log_usage(UsageRecord(
                    timestamp=datetime.utcnow(),
                    client_id=payer,
                    provider="x402",
                    model="api",
                    input_tokens=0,
                    output_tokens=0,
                    cost=int(verification.get("amount", "0")) / 1_000_000,  # Convert micro-USDC to USD
                    session_id=verification.get("transaction", ""),
                    task_description=f"x402 payment for {request.path}",
                ))
            except Exception as e:
                logger.warning(f"Failed to log x402 payment to billing: {e}")

        return response

    return middleware


# ── Convenience Functions ─────────────────────────────────────


def setup_x402(app: "web.Application", billing_tracker=None) -> Optional[X402PaymentGate]:
    """
    Set up x402 payment gate on an aiohttp app.

    Reads config from environment variables. Returns None if x402 is disabled.
    """
    config = PaymentConfig.from_env()

    if not config.enabled:
        logger.info("x402 payments disabled (set X402_ENABLED=true to enable)")
        return None

    if not config.pay_to_address:
        logger.warning("x402 enabled but X402_PAY_TO_ADDRESS not set — skipping")
        return None

    gate = X402PaymentGate(config, billing_tracker=billing_tracker)

    # Add middleware to protected endpoints
    app.middlewares.append(
        x402_middleware(gate, protected_paths=["/api/chat", "/api/completion"])
    )

    logger.info(
        f"x402 payments enabled: {config.network}, "
        f"${config.price_per_request / 1_000_000:.4f}/request, "
        f"${config.price_per_1k_tokens / 1_000_000:.4f}/1K tokens"
    )

    return gate
