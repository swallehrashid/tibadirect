"""Roadmap stub for a licensed Kenyan settlement partner (Jenga API).

Not built for the hackathon. A real adapter would, for each tranche:

1. Convert the tranche's USD to KES through the partner's FX service at a quoted rate.
2. Look up the hospital's settlement account (a bank account or an M-PESA paybill) from
   ``Hospital.settlement_account_ref_enc`` and check it against the facility register.
3. Send the payment with the settlement's idempotency key as the partner's reference, and poll or
   receive the partner's callback for the final status.
4. Return the partner's transaction reference, which appears on the public ledger.

The partner would also carry the KYC of the receiving institution, which is why the donor's money
never sits with TibaDirect or with a family. Until then every call raises ``NotConfigured``.
"""

from app.models.enums import SettlementMethod
from app.models.hospital import Hospital
from app.models.money import Settlement
from app.payments.adapters.base import AdapterResult, NotConfigured


class JengaAdapter:
    method = SettlementMethod.JENGA

    def check_ready(self, hospital: Hospital) -> str | None:
        return "The Jenga partner adapter is not configured"

    def send(self, settlement: Settlement, hospital: Hospital) -> AdapterResult:
        raise NotConfigured("The Jenga partner adapter is not configured")

    def poll(self, settlement: Settlement) -> AdapterResult:
        raise NotConfigured("The Jenga partner adapter is not configured")
