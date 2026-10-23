"""The adapter interface and what an adapter answers with."""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.models.enums import SettlementMethod
from app.models.hospital import Hospital
from app.models.money import Settlement

# ``processing`` means the provider has the payment but has not finished it; ask again later.
AdapterStatus = Literal["succeeded", "processing", "failed"]


class NotConfigured(RuntimeError):  # noqa: N818 - reads better than NotConfiguredError
    """The adapter exists but cannot be used until it is set up."""


@dataclass(frozen=True)
class AdapterResult:
    """The state of one payment at the provider.

    ``batch_id`` and ``item_id`` are the provider's own references. ``failure`` is a short,
    personal-data-free reason code or sentence, set when ``status`` is ``failed``.
    """

    status: AdapterStatus
    batch_id: str | None = None
    item_id: str | None = None
    failure: str | None = None


class SettlementAdapter(Protocol):
    method: SettlementMethod

    def check_ready(self, hospital: Hospital) -> str | None:
        """Why this hospital cannot be paid through this adapter, or None if it can."""
        ...

    def send(self, settlement: Settlement, hospital: Hospital) -> AdapterResult:
        """Start the payment. Safe to repeat: the settlement's idempotency key makes the provider
        treat a second call as the first."""
        ...

    def poll(self, settlement: Settlement) -> AdapterResult:
        """Ask the provider where the payment stands."""
        ...
