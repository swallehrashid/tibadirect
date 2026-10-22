"""A simulated licensed Kenyan partner: converts to KES and pays the hospital's paybill.

It needs no outside service. A payment succeeds once ``MOCK_PARTNER_DELAY_SECONDS`` have passed
since it was sent; the send time is part of the reference it returns, so any process can answer
a poll without shared state.
"""

import time

from flask import current_app

from app.models.enums import SettlementMethod
from app.models.hospital import Hospital
from app.models.money import Settlement
from app.payments.adapters.base import AdapterResult

REFERENCE_PREFIX = "MOCKPARTNER"


class MockPartnerAdapter:
    method = SettlementMethod.MOCK_PARTNER

    def check_ready(self, hospital: Hospital) -> str | None:
        return None

    def send(self, settlement: Settlement, hospital: Hospital) -> AdapterResult:
        # The same settlement always gets the same reference, so a repeated send is harmless.
        sent_at = int(settlement.created_at.timestamp())
        reference = f"{REFERENCE_PREFIX}-{settlement.id.hex[:12].upper()}-{sent_at}"
        return AdapterResult("processing", batch_id=reference, item_id=reference)

    def poll(self, settlement: Settlement) -> AdapterResult:
        reference = settlement.external_batch_id
        if not reference or not reference.startswith(REFERENCE_PREFIX):
            return AdapterResult("processing")
        sent_at = int(reference.rsplit("-", 1)[-1])
        delay = current_app.config.get("MOCK_PARTNER_DELAY_SECONDS", 5)
        if time.time() - sent_at < delay:
            return AdapterResult("processing", batch_id=reference, item_id=reference)
        return AdapterResult("succeeded", batch_id=reference, item_id=reference)
