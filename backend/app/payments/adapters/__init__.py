"""Settlement adapters: how a tranche reaches the hospital.

The settlement engine decides *what* to pay; an adapter does the paying. The sandbox adapter uses
PayPal Payouts to the hospital's sandbox account, standing in for a licensed Kenyan partner; a
mock partner simulates that partner offline; the Jenga adapter is a documented stub for the
roadmap. ``adapter_for`` picks by the hospital's ``settlement_method``.
"""

from app.models.enums import SettlementMethod
from app.payments.adapters.base import AdapterResult, NotConfigured, SettlementAdapter
from app.payments.adapters.jenga_stub import JengaAdapter
from app.payments.adapters.mock_partner import MockPartnerAdapter
from app.payments.adapters.paypal_payouts import PayPalPayoutsAdapter

__all__ = [
    "AdapterResult",
    "JengaAdapter",
    "MockPartnerAdapter",
    "NotConfigured",
    "PayPalPayoutsAdapter",
    "SettlementAdapter",
    "adapter_for",
]


def adapter_for(method: SettlementMethod) -> SettlementAdapter:
    """A fresh adapter for ``method``."""
    if method == SettlementMethod.PAYPAL_PAYOUTS:
        return PayPalPayoutsAdapter()
    if method == SettlementMethod.MOCK_PARTNER:
        return MockPartnerAdapter()
    if method == SettlementMethod.JENGA:
        return JengaAdapter()
    raise ValueError(f"No settlement adapter for {method!r}")
