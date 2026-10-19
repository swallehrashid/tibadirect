"""The append-only, hash-chained ledger. A database trigger (added by the migration) rejects
UPDATE and DELETE; the hashing itself lives in ``ledger_service``."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, BigInteger, CheckConstraint, ForeignKey, Identity, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.enums import LedgerAccount, LedgerEntryType, pg_enum

if TYPE_CHECKING:
    from app.models.case import Case

GENESIS_HASH = "0" * 64


class LedgerEntry(db.Model, UUIDPrimaryKeyMixin, CreatedAtMixin):
    """One signed movement of money. ``amount_minor`` is positive into an account, negative out."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_minor <> 0", name="amount_non_zero"),
        # No two entries may claim the same predecessor, so the chain cannot fork.
        # The genesis entry is the only one whose prev_hash is 64 zeros.
        Index("uq_ledger_entries_prev_hash", "prev_hash", unique=True),
        Index("ix_ledger_entries_case_seq", "case_id", "seq"),
    )

    # BY DEFAULT: the ledger service reads the next value first, because the hash covers seq.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=False), unique=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), index=True
    )
    entry_type: Mapped[LedgerEntryType] = mapped_column(pg_enum(LedgerEntryType))
    account: Mapped[LedgerAccount] = mapped_column(pg_enum(LedgerAccount))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(CHAR(3))
    ref_type: Mapped[str] = mapped_column(String(40))
    ref_id: Mapped[uuid.UUID] = mapped_column()
    memo: Mapped[str | None] = mapped_column(String(200))
    prev_hash: Mapped[str] = mapped_column(CHAR(64))
    hash: Mapped[str] = mapped_column(CHAR(64), unique=True)

    case: Mapped["Case | None"] = relationship(back_populates="ledger_entries")

    def __repr__(self) -> str:
        return f"<LedgerEntry seq={self.seq} type={self.entry_type}>"
