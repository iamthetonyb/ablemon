"""
Stripe Payment Integration for ABLE

Handles credit card payments for client API access and subscriptions.
Works alongside x402 (crypto) — clients choose their payment method.

Flow (Checkout):
1. Client requests access or hits /api/billing/checkout
2. Server creates a Stripe Checkout Session with pricing
3. Client completes payment on Stripe-hosted page
4. Stripe sends webhook (checkout.session.completed)
5. Server adds credits to client's account
6. Subsequent API requests deduct credits

Flow (Subscription):
1. Client subscribes via /api/billing/subscribe
2. Stripe handles recurring billing
3. Webhook confirms each payment cycle
4. Credits replenish monthly

Env vars:
    STRIPE_SECRET_KEY       — Stripe API secret key (sk_live_... or sk_test_...)
    STRIPE_WEBHOOK_SECRET   — Webhook endpoint signing secret (whsec_...)
    STRIPE_PRICE_ID         — Default price ID for credit packs
    STRIPE_ENABLED          — "true" to enable (default: false)
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import stripe
    STRIPE_AVAILABLE = True
except ImportError:
    STRIPE_AVAILABLE = False
    stripe = None


# ── Configuration ──────────────────────────────────────────


@dataclass
class StripeConfig:
    """Stripe configuration loaded from environment."""
    enabled: bool = False
    secret_key: str = ""
    webhook_secret: str = ""
    # Default product/price for credit packs
    default_price_id: str = ""
    # Credits per dollar (1 USD = 100 credits by default)
    credits_per_usd: int = 100
    # Default credit pack sizes
    credit_packs: Dict[str, int] = field(default_factory=lambda: {
        "starter": 25_00,     # $25 = 2,500 credits
        "standard": 100_00,   # $100 = 10,000 credits
        "premium": 500_00,    # $500 = 50,000 credits
    })
    # Monthly subscription tiers (amount in cents)
    subscription_tiers: Dict[str, int] = field(default_factory=lambda: {
        "basic": 99_00,       # $99/mo
        "professional": 299_00,  # $299/mo
        "enterprise": 999_00,    # $999/mo
    })
    success_url: str = ""
    cancel_url: str = ""

    @classmethod
    def from_env(cls) -> "StripeConfig":
        """Load config from environment variables."""
        return cls(
            enabled=os.environ.get("STRIPE_ENABLED", "false").lower() == "true",
            secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
            webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
            default_price_id=os.environ.get("STRIPE_PRICE_ID", ""),
            credits_per_usd=int(os.environ.get("STRIPE_CREDITS_PER_USD", "100")),
            success_url=os.environ.get(
                "STRIPE_SUCCESS_URL",
                "https://able.local/billing/success?session_id={CHECKOUT_SESSION_ID}",
            ),
            cancel_url=os.environ.get(
                "STRIPE_CANCEL_URL",
                "https://able.local/billing/cancel",
            ),
        )


# ── Credit Ledger (SQLite) ─────────────────────────────────


class CreditLedger:
    """
    SQLite-backed credit balance tracker.

    Tracks credits per client — credits are added on payment,
    deducted on API usage. Thread-safe via SQLite WAL mode.
    """

    def __init__(self, db_path: str = "data/billing_credits.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS credit_balances (
                client_id TEXT PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0,
                lifetime_purchased INTEGER NOT NULL DEFAULT 0,
                lifetime_used INTEGER NOT NULL DEFAULT 0,
                stripe_customer_id TEXT,
                subscription_id TEXT,
                subscription_status TEXT DEFAULT 'none',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS credit_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id TEXT NOT NULL,
                amount INTEGER NOT NULL,
                type TEXT NOT NULL,
                description TEXT,
                stripe_session_id TEXT,
                stripe_invoice_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_txn_client
                ON credit_transactions(client_id, created_at);
        """)
        conn.commit()
        conn.close()

    def get_balance(self, client_id: str) -> int:
        """Get current credit balance for a client."""
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT balance FROM credit_balances WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    def add_credits(
        self,
        client_id: str,
        amount: int,
        description: str = "",
        stripe_session_id: str = "",
        stripe_invoice_id: str = "",
    ) -> int:
        """Add credits to a client's balance. Returns new balance."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                INSERT INTO credit_balances (client_id, balance, lifetime_purchased, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    balance = balance + excluded.balance,
                    lifetime_purchased = lifetime_purchased + excluded.balance,
                    updated_at = excluded.updated_at
            """, (client_id, amount, amount, now))

            conn.execute("""
                INSERT INTO credit_transactions
                    (client_id, amount, type, description, stripe_session_id, stripe_invoice_id, created_at)
                VALUES (?, ?, 'credit', ?, ?, ?, ?)
            """, (client_id, amount, description, stripe_session_id, stripe_invoice_id, now))

            conn.commit()
            new_balance = conn.execute(
                "SELECT balance FROM credit_balances WHERE client_id = ?",
                (client_id,),
            ).fetchone()[0]
            return new_balance
        finally:
            conn.close()

    def deduct_credits(
        self,
        client_id: str,
        amount: int,
        description: str = "",
    ) -> tuple:
        """
        Deduct credits from a client's balance.

        Returns (success: bool, new_balance: int).
        Fails if insufficient balance.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT balance FROM credit_balances WHERE client_id = ?",
                (client_id,),
            ).fetchone()

            if not row or row[0] < amount:
                return False, row[0] if row else 0

            conn.execute("""
                UPDATE credit_balances
                SET balance = balance - ?,
                    lifetime_used = lifetime_used + ?,
                    updated_at = ?
                WHERE client_id = ?
            """, (amount, amount, now, client_id))

            conn.execute("""
                INSERT INTO credit_transactions
                    (client_id, amount, type, description, created_at)
                VALUES (?, ?, 'debit', ?, ?)
            """, (client_id, -amount, description, now))

            conn.commit()
            new_balance = row[0] - amount
            return True, new_balance
        finally:
            conn.close()

    def set_stripe_customer(
        self, client_id: str, stripe_customer_id: str
    ):
        """Link a Stripe customer ID to a client."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            INSERT INTO credit_balances (client_id, balance, lifetime_purchased, stripe_customer_id, updated_at)
            VALUES (?, 0, 0, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                stripe_customer_id = excluded.stripe_customer_id,
                updated_at = excluded.updated_at
        """, (client_id, stripe_customer_id, now))
        conn.commit()
        conn.close()

    def set_subscription(
        self, client_id: str, subscription_id: str, status: str
    ):
        """Update subscription status for a client."""
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            UPDATE credit_balances
            SET subscription_id = ?, subscription_status = ?, updated_at = ?
            WHERE client_id = ?
        """, (subscription_id, status, now, client_id))
        conn.commit()
        conn.close()

    def get_client_info(self, client_id: str) -> Optional[Dict]:
        """Get full client billing info."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM credit_balances WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None
        return dict(row)

    def get_transactions(
        self, client_id: str, limit: int = 50
    ) -> List[Dict]:
        """Get recent transactions for a client."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM credit_transactions WHERE client_id = ? ORDER BY created_at DESC LIMIT ?",
            (client_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ── Stripe Payment Gate ────────────────────────────────────


class StripePaymentGate:
    """
    Manages Stripe payment operations for ABLE.

    Handles checkout sessions, subscriptions, webhook verification,
    and credit management. Integrates with BillingTracker for usage logging.
    """

    def __init__(
        self,
        config: StripeConfig,
        ledger: Optional[CreditLedger] = None,
        billing_tracker=None,
    ):
        if not STRIPE_AVAILABLE:
            raise ImportError("stripe package required: pip install stripe")

        self.config = config
        self.ledger = ledger or CreditLedger()
        self.billing_tracker = billing_tracker

        # Initialize Stripe SDK
        stripe.api_key = config.secret_key

    async def create_checkout_session(
        self,
        client_id: str,
        amount_cents: int = 100_00,
        description: str = "ABLE API Credits",
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """
        Create a Stripe Checkout Session for one-time credit purchase.

        Args:
            client_id: Your internal client identifier
            amount_cents: Amount in cents (e.g., 10000 = $100)
            description: Line item description
            metadata: Extra metadata to attach

        Returns:
            Dict with checkout_url and session_id
        """
        credits = (amount_cents // 100) * self.config.credits_per_usd

        session_metadata = {
            "client_id": client_id,
            "credits": str(credits),
            "type": "credit_purchase",
            **(metadata or {}),
        }

        # Get or create Stripe customer
        customer_id = await self._get_or_create_customer(client_id)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": description,
                        "description": f"{credits:,} ABLE API credits",
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=self.config.success_url,
            cancel_url=self.config.cancel_url,
            metadata=session_metadata,
        )

        logger.info(
            f"Checkout session created for {client_id}: "
            f"${amount_cents/100:.2f} → {credits:,} credits"
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "amount_usd": amount_cents / 100,
            "credits": credits,
        }

    async def create_subscription(
        self,
        client_id: str,
        tier: str = "basic",
        metadata: Optional[Dict] = None,
    ) -> Dict:
        """
        Create a Stripe Checkout Session for a monthly subscription.

        Args:
            client_id: Your internal client identifier
            tier: Subscription tier (basic, professional, enterprise)
            metadata: Extra metadata

        Returns:
            Dict with checkout_url and session_id
        """
        amount_cents = self.config.subscription_tiers.get(tier, 99_00)
        credits_monthly = (amount_cents // 100) * self.config.credits_per_usd

        customer_id = await self._get_or_create_customer(client_id)

        session_metadata = {
            "client_id": client_id,
            "credits_monthly": str(credits_monthly),
            "tier": tier,
            "type": "subscription",
            **(metadata or {}),
        }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"ABLE {tier.title()} Plan",
                        "description": f"{credits_monthly:,} credits/month",
                    },
                    "unit_amount": amount_cents,
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            mode="subscription",
            success_url=self.config.success_url,
            cancel_url=self.config.cancel_url,
            metadata=session_metadata,
        )

        logger.info(
            f"Subscription checkout created for {client_id}: "
            f"{tier} (${amount_cents/100:.2f}/mo → {credits_monthly:,} credits)"
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
            "tier": tier,
            "amount_usd_monthly": amount_cents / 100,
            "credits_monthly": credits_monthly,
        }

    def verify_webhook(self, payload: bytes, signature: str) -> Optional[Dict]:
        """
        Verify and parse a Stripe webhook event.

        Args:
            payload: Raw request body bytes
            signature: Stripe-Signature header value

        Returns:
            Parsed event dict, or None if verification fails
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.config.webhook_secret
            )
            return event
        except stripe.error.SignatureVerificationError:
            logger.warning("Stripe webhook signature verification failed")
            return None
        except Exception as e:
            logger.error(f"Stripe webhook parse error: {e}")
            return None

    async def handle_webhook_event(self, event: Dict) -> Dict:
        """
        Process a verified Stripe webhook event.

        Handles:
            checkout.session.completed — Add credits for one-time purchase
            invoice.payment_succeeded — Add credits for subscription renewal
            customer.subscription.updated — Track subscription status changes
            customer.subscription.deleted — Mark subscription cancelled

        Returns:
            Dict with processing result
        """
        event_type = event.get("type", "")
        data = event.get("data", {}).get("object", {})

        if event_type == "checkout.session.completed":
            return await self._handle_checkout_completed(data)

        elif event_type == "invoice.payment_succeeded":
            return await self._handle_invoice_paid(data)

        elif event_type == "customer.subscription.updated":
            return await self._handle_subscription_updated(data)

        elif event_type == "customer.subscription.deleted":
            return await self._handle_subscription_deleted(data)

        else:
            logger.debug(f"Unhandled Stripe event: {event_type}")
            return {"handled": False, "event_type": event_type}

    async def get_client_billing_status(self, client_id: str) -> Dict:
        """Get billing status for a client (balance, subscription, history)."""
        info = self.ledger.get_client_info(client_id)
        transactions = self.ledger.get_transactions(client_id, limit=10)

        if not info:
            return {
                "client_id": client_id,
                "balance": 0,
                "subscription": "none",
                "has_access": False,
            }

        return {
            "client_id": client_id,
            "balance": info["balance"],
            "lifetime_purchased": info["lifetime_purchased"],
            "lifetime_used": info["lifetime_used"],
            "subscription_status": info["subscription_status"],
            "stripe_customer_id": info["stripe_customer_id"],
            "has_access": info["balance"] > 0 or info["subscription_status"] == "active",
            "recent_transactions": transactions,
        }

    def check_access(self, client_id: str, credits_required: int = 1) -> bool:
        """Check if a client has enough credits for an API request."""
        balance = self.ledger.get_balance(client_id)
        return balance >= credits_required

    def deduct_for_request(
        self, client_id: str, credits: int = 1, description: str = ""
    ) -> bool:
        """Deduct credits for an API request. Returns True if successful."""
        success, _ = self.ledger.deduct_credits(client_id, credits, description)
        return success

    # ── Internal helpers ──────────────────────────────────────

    async def _get_or_create_customer(self, client_id: str) -> str:
        """Get existing Stripe customer or create one."""
        info = self.ledger.get_client_info(client_id)
        if info and info.get("stripe_customer_id"):
            return info["stripe_customer_id"]

        customer = stripe.Customer.create(
            metadata={"able_client_id": client_id},
        )
        self.ledger.set_stripe_customer(client_id, customer.id)
        return customer.id

    async def _handle_checkout_completed(self, session: Dict) -> Dict:
        """Handle successful one-time checkout."""
        metadata = session.get("metadata", {})
        client_id = metadata.get("client_id")
        payment_type = metadata.get("type", "credit_purchase")

        if not client_id:
            logger.warning("Checkout completed but no client_id in metadata")
            return {"handled": False, "error": "missing client_id"}

        if payment_type == "subscription":
            # Subscription checkout — credits added on invoice.payment_succeeded
            sub_id = session.get("subscription", "")
            if sub_id:
                self.ledger.set_subscription(client_id, sub_id, "active")
            return {"handled": True, "type": "subscription_started", "client_id": client_id}

        # One-time credit purchase
        credits = int(metadata.get("credits", "0"))
        if credits <= 0:
            amount = session.get("amount_total", 0)
            credits = (amount // 100) * self.config.credits_per_usd

        new_balance = self.ledger.add_credits(
            client_id=client_id,
            amount=credits,
            description=f"Stripe checkout: ${session.get('amount_total', 0)/100:.2f}",
            stripe_session_id=session.get("id", ""),
        )

        logger.info(f"Credits added for {client_id}: +{credits:,} (balance: {new_balance:,})")

        # Log to billing tracker
        if self.billing_tracker:
            try:
                from billing.tracker import UsageRecord
                self.billing_tracker.log_usage(UsageRecord(
                    timestamp=datetime.now(timezone.utc),
                    client_id=client_id,
                    provider="stripe",
                    model="checkout",
                    input_tokens=0,
                    output_tokens=0,
                    cost=-(session.get("amount_total", 0) / 100),  # Negative = income
                    session_id=session.get("id", ""),
                    task_description=f"Credit purchase: {credits:,} credits",
                ))
            except Exception as e:
                logger.warning(f"Failed to log Stripe payment to billing: {e}")

        return {
            "handled": True,
            "type": "credit_purchase",
            "client_id": client_id,
            "credits_added": credits,
            "new_balance": new_balance,
        }

    async def _handle_invoice_paid(self, invoice: Dict) -> Dict:
        """Handle successful subscription invoice payment."""
        customer_id = invoice.get("customer", "")
        subscription_id = invoice.get("subscription", "")

        # Look up client by Stripe customer ID
        client_id = self._find_client_by_customer(customer_id)
        if not client_id:
            logger.warning(f"Invoice paid but can't find client for customer {customer_id}")
            return {"handled": False, "error": "unknown customer"}

        # Determine credits from subscription metadata or amount
        amount_cents = invoice.get("amount_paid", 0)
        credits = (amount_cents // 100) * self.config.credits_per_usd

        new_balance = self.ledger.add_credits(
            client_id=client_id,
            amount=credits,
            description=f"Subscription renewal: ${amount_cents/100:.2f}",
            stripe_invoice_id=invoice.get("id", ""),
        )

        logger.info(
            f"Subscription credits for {client_id}: +{credits:,} (balance: {new_balance:,})"
        )

        return {
            "handled": True,
            "type": "subscription_renewal",
            "client_id": client_id,
            "credits_added": credits,
            "new_balance": new_balance,
        }

    async def _handle_subscription_updated(self, subscription: Dict) -> Dict:
        """Handle subscription status change."""
        customer_id = subscription.get("customer", "")
        client_id = self._find_client_by_customer(customer_id)
        if not client_id:
            return {"handled": False, "error": "unknown customer"}

        status = subscription.get("status", "unknown")
        self.ledger.set_subscription(client_id, subscription.get("id", ""), status)

        logger.info(f"Subscription updated for {client_id}: {status}")
        return {"handled": True, "type": "subscription_updated", "status": status}

    async def _handle_subscription_deleted(self, subscription: Dict) -> Dict:
        """Handle subscription cancellation."""
        customer_id = subscription.get("customer", "")
        client_id = self._find_client_by_customer(customer_id)
        if not client_id:
            return {"handled": False, "error": "unknown customer"}

        self.ledger.set_subscription(client_id, subscription.get("id", ""), "cancelled")

        logger.info(f"Subscription cancelled for {client_id}")
        return {"handled": True, "type": "subscription_cancelled", "client_id": client_id}

    def _find_client_by_customer(self, stripe_customer_id: str) -> Optional[str]:
        """Look up internal client_id from Stripe customer ID."""
        conn = sqlite3.connect(str(self.ledger.db_path))
        row = conn.execute(
            "SELECT client_id FROM credit_balances WHERE stripe_customer_id = ?",
            (stripe_customer_id,),
        ).fetchone()
        conn.close()
        return row[0] if row else None


# ── aiohttp Middleware ──────────────────────────────────────


def stripe_credit_middleware(
    payment_gate: StripePaymentGate,
    protected_paths: Optional[List[str]] = None,
    credits_per_request: int = 1,
):
    """
    aiohttp middleware that checks credit balance before serving protected endpoints.

    If client has insufficient credits, returns 402 with a checkout URL.

    Usage:
        gate = StripePaymentGate(config)
        app = web.Application(middlewares=[stripe_credit_middleware(gate)])
    """
    from aiohttp import web

    protected = set(protected_paths or ["/api/chat", "/api/completion"])

    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.path not in protected:
            return await handler(request)

        # Identify client from API key or header
        client_id = (
            request.headers.get("X-Client-ID")
            or request.headers.get("X-API-Key")
            or request.query.get("client_id")
        )

        if not client_id:
            return web.json_response(
                {"error": "X-Client-ID or X-API-Key header required"},
                status=401,
            )

        # Check credit balance
        if not payment_gate.check_access(client_id, credits_per_request):
            # Return 402 with checkout link
            checkout = await payment_gate.create_checkout_session(
                client_id=client_id,
                amount_cents=25_00,  # $25 starter pack
                description="ABLE API Credits — Starter Pack",
            )
            return web.json_response(
                {
                    "error": "Insufficient credits",
                    "balance": payment_gate.ledger.get_balance(client_id),
                    "credits_required": credits_per_request,
                    "checkout_url": checkout["checkout_url"],
                    "message": "Purchase credits to continue using the API.",
                },
                status=402,
            )

        # Serve the request
        response = await handler(request)

        # Deduct credits after successful response
        payment_gate.deduct_for_request(
            client_id, credits_per_request,
            description=f"API request: {request.path}",
        )

        return response

    return middleware


# ── Server Integration ──────────────────────────────────────


def setup_stripe(
    app,
    billing_tracker=None,
) -> Optional[StripePaymentGate]:
    """
    Set up Stripe payment gate on an aiohttp app.

    Reads config from environment. Returns None if Stripe is disabled.

    Adds:
        - Credit check middleware on protected paths
        - POST /webhook/stripe — webhook receiver
        - POST /api/billing/checkout — create checkout session
        - POST /api/billing/subscribe — create subscription
        - GET  /api/billing/status — check client billing status
    """
    from aiohttp import web

    config = StripeConfig.from_env()

    if not config.enabled:
        logger.info("Stripe payments disabled (set STRIPE_ENABLED=true to enable)")
        return None

    if not config.secret_key:
        logger.warning("Stripe enabled but STRIPE_SECRET_KEY not set — skipping")
        return None

    if not STRIPE_AVAILABLE:
        logger.warning("Stripe enabled but `stripe` package not installed — pip install stripe")
        return None

    gate = StripePaymentGate(config, billing_tracker=billing_tracker)

    # Add credit-check middleware for protected API endpoints
    app.middlewares.append(
        stripe_credit_middleware(gate, protected_paths=["/api/chat", "/api/completion"])
    )

    # ── Webhook handler ──────────────────────────────────────

    async def handle_stripe_webhook(request: web.Request) -> web.Response:
        payload = await request.read()
        signature = request.headers.get("Stripe-Signature", "")

        event = gate.verify_webhook(payload, signature)
        if not event:
            return web.json_response({"error": "Invalid signature"}, status=400)

        result = await gate.handle_webhook_event(event)
        return web.json_response(result)

    # ── Checkout endpoint ────────────────────────────────────

    async def handle_checkout(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        client_id = body.get("client_id")
        if not client_id:
            return web.json_response({"error": "client_id required"}, status=400)

        amount = body.get("amount_cents", 25_00)
        description = body.get("description", "ABLE API Credits")

        result = await gate.create_checkout_session(
            client_id=client_id,
            amount_cents=amount,
            description=description,
        )
        return web.json_response(result)

    # ── Subscribe endpoint ───────────────────────────────────

    async def handle_subscribe(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        client_id = body.get("client_id")
        if not client_id:
            return web.json_response({"error": "client_id required"}, status=400)

        tier = body.get("tier", "basic")
        if tier not in config.subscription_tiers:
            return web.json_response(
                {"error": f"Invalid tier. Options: {list(config.subscription_tiers.keys())}"},
                status=400,
            )

        result = await gate.create_subscription(client_id=client_id, tier=tier)
        return web.json_response(result)

    # ── Billing status endpoint ──────────────────────────────

    async def handle_billing_status(request: web.Request) -> web.Response:
        client_id = request.query.get("client_id")
        if not client_id:
            return web.json_response({"error": "client_id query param required"}, status=400)

        result = await gate.get_client_billing_status(client_id)
        return web.json_response(result)

    # ── Register routes ──────────────────────────────────────

    app.router.add_post("/webhook/stripe", handle_stripe_webhook)
    app.router.add_post("/api/billing/checkout", handle_checkout)
    app.router.add_post("/api/billing/subscribe", handle_subscribe)
    app.router.add_get("/api/billing/status", handle_billing_status)

    logger.info(
        f"Stripe payments enabled: "
        f"{config.credits_per_usd} credits/$1, "
        f"tiers: {list(config.subscription_tiers.keys())}"
    )

    return gate
