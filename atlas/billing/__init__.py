"""
ATLAS v2 Billing System

Usage tracking, cost calculation, invoice generation, and payment processing.
Supports both Stripe (credit cards) and x402 (crypto/USDC) payment methods.
"""

from .tracker import BillingTracker, UsageRecord, BillingSession
from .invoice import InvoiceGenerator, Invoice
from .reports import BillingReports

__all__ = [
    'BillingTracker',
    'UsageRecord',
    'BillingSession',
    'InvoiceGenerator',
    'Invoice',
    'BillingReports',
]

# Lazy imports for optional payment integrations
def get_stripe_gate():
    from .stripe_billing import StripePaymentGate, StripeConfig, CreditLedger, setup_stripe
    return StripePaymentGate, StripeConfig, CreditLedger, setup_stripe

def get_x402_gate():
    from .x402 import X402PaymentGate, PaymentConfig, setup_x402
    return X402PaymentGate, PaymentConfig, setup_x402
