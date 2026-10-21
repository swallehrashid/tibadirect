"""The append-only, hash-chained ledger.

Every entry's hash covers its own fields and the previous entry's hash, so changing or removing
any row breaks verification from that point on. Appends take a PostgreSQL advisory lock, which
makes the chain strictly serial. This is the core of the service; the public view and
verification endpoints are built on top of it.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.models.enums import LedgerAccount, LedgerEntryType
from app.models.ledger import GENESIS_HASH, LedgerEntry
from app.models.money import Donation, Refund, Settlement
from app.money import format_amount

LEDGER_LOCK_KEY = 7340001


@dataclass(frozen=True)
class NewEntry:
    """One movement to record. ``amount_minor`` is positive into the account, negative out of it."""

    case_id: uuid.UUID | None
    entry_type: LedgerEntryType
    account: LedgerAccount
    amount_minor: int
    currency: str
    ref_type: str
    ref_id: uuid.UUID
    memo: str | None = None


@dataclass(frozen=True)
class ChainStatus:
    """The result of walking the chain.

    ``last_hash`` is the end of the chain verified so far; ``case_last_hash`` is the hash of the
    requested case's newest entry (``None`` for a whole-chain check or a case with no entries).
    ``first_bad_seq`` names the first entry that does not fit, for admins only.
    """

    valid: bool
    entries_checked: int
    last_hash: str
    first_bad_seq: int | None = None
    case_last_hash: str | None = None


def canonical_payload(
    *,
    seq: int,
    case_id: uuid.UUID | None,
    entry_type: LedgerEntryType,
    account: LedgerAccount,
    amount_minor: int,
    currency: str,
    ref_type: str,
    ref_id: uuid.UUID,
    created_at: datetime,
    prev_hash: str,
) -> str:
    """Sorted-key JSON without spaces: the exact text that is hashed."""
    return json.dumps(
        {
            "seq": seq,
            "case_id": str(case_id) if case_id else None,
            "entry_type": str(entry_type),
            "account": str(account),
            "amount_minor": amount_minor,
            "currency": currency,
            "ref_type": ref_type,
            "ref_id": str(ref_id),
            "created_at_iso": created_at.astimezone(UTC).isoformat(timespec="microseconds"),
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def entry_hash(entry: LedgerEntry) -> str:
    """Recompute the hash of a stored entry from its own fields."""
    payload = canonical_payload(
        seq=entry.seq,
        case_id=entry.case_id,
        entry_type=entry.entry_type,
        account=entry.account,
        amount_minor=entry.amount_minor,
        currency=entry.currency,
        ref_type=entry.ref_type,
        ref_id=entry.ref_id,
        created_at=entry.created_at,
        prev_hash=entry.prev_hash,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def append(
    session: Session, entries: Sequence[NewEntry], *, created_at: datetime | None = None
) -> list[LedgerEntry]:
    """Add entries to the chain inside the caller's transaction and return the stored rows.

    ``created_at`` is shared by the entries of one call; it defaults to now. The caller commits.
    """
    if not entries:
        return []
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LEDGER_LOCK_KEY})
    stamp = (created_at or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=UTC)

    previous = session.execute(
        select(LedgerEntry.hash).order_by(LedgerEntry.seq.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = previous or GENESIS_HASH

    stored = []
    for new in entries:
        seq = session.execute(
            text("SELECT nextval(pg_get_serial_sequence('ledger_entries', 'seq'))")
        ).scalar_one()
        payload = canonical_payload(
            seq=seq,
            case_id=new.case_id,
            entry_type=new.entry_type,
            account=new.account,
            amount_minor=new.amount_minor,
            currency=new.currency,
            ref_type=new.ref_type,
            ref_id=new.ref_id,
            created_at=stamp,
            prev_hash=prev_hash,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        row = LedgerEntry(
            seq=seq,
            case_id=new.case_id,
            entry_type=new.entry_type,
            account=new.account,
            amount_minor=new.amount_minor,
            currency=new.currency,
            ref_type=new.ref_type,
            ref_id=new.ref_id,
            memo=new.memo,
            created_at=stamp,
            prev_hash=prev_hash,
            hash=digest,
        )
        session.add(row)
        session.flush()
        stored.append(row)
        prev_hash = digest
    return stored


def verify_chain(session: Session, case_id: uuid.UUID | None = None) -> ChainStatus:
    """Recompute every hash from the first entry. With ``case_id``, count only that case's entries.

    The chain is global, so a case is only trusted when the whole chain is intact: an entry that
    does not fit makes every case's check fail, not just its own.
    """
    expected_prev = GENESIS_HASH
    checked = 0
    last_hash = GENESIS_HASH
    case_last_hash: str | None = None
    rows = session.execute(
        select(LedgerEntry).order_by(LedgerEntry.seq).execution_options(yield_per=1000)
    ).scalars()
    for entry in rows:
        if entry.prev_hash != expected_prev or entry_hash(entry) != entry.hash:
            return ChainStatus(False, checked, last_hash, first_bad_seq=entry.seq)
        if case_id is not None and entry.case_id == case_id:
            case_last_hash = entry.hash
        if case_id is None or entry.case_id == case_id:
            checked += 1
        expected_prev = last_hash = entry.hash
    return ChainStatus(True, checked, last_hash, case_last_hash=case_last_hash)


# --------------------------------------------------------------------------- the public view

SHORT_HASH_LENGTH = 12


def short_reference(value: str | None) -> str | None:
    """A PayPal or payout reference cut down for public display: ``8GH2…91XQ``."""
    if not value:
        return None
    return value if len(value) <= 10 else f"{value[:4]}…{value[-4:]}"


def case_entries(case_id: uuid.UUID) -> Select[tuple[LedgerEntry]]:
    """A case's entries in the order they were written, for paging."""
    return select(LedgerEntry).where(LedgerEntry.case_id == case_id).order_by(LedgerEntry.seq)


def public_rows(session: Session, entries: Sequence[LedgerEntry]) -> list[dict[str, Any]]:
    """What anyone may see of ledger entries: time, type, amount, short hash and a short reference.

    The donor's name appears only on the entry that records their donation, and only if they gave
    a name and did not ask to be anonymous. Memos and full hashes are not shown.
    """
    donation_ids = {e.ref_id for e in entries if e.ref_type == "donation"}
    settlement_ids = {e.ref_id for e in entries if e.ref_type == "settlement"}
    refund_ids = {e.ref_id for e in entries if e.ref_type == "refund"}
    donations = _by_id(session, Donation, donation_ids)
    settlements = _by_id(session, Settlement, settlement_ids)
    refunds = _by_id(session, Refund, refund_ids)

    rows = []
    for entry in entries:
        reference = donor = None
        if entry.ref_type == "donation" and (donation := donations.get(entry.ref_id)):
            reference = short_reference(donation.paypal_capture_id)
            if entry.entry_type == LedgerEntryType.DONATION_CAPTURED and not donation.is_anonymous:
                donor = donation.donor_display_name
        elif entry.ref_type == "settlement" and (settlement := settlements.get(entry.ref_id)):
            reference = short_reference(settlement.external_item_id or settlement.external_batch_id)
        elif entry.ref_type == "refund" and (refund := refunds.get(entry.ref_id)):
            reference = short_reference(refund.paypal_refund_id)
        rows.append(
            {
                "created_at": entry.created_at,
                "entry_type": entry.entry_type.value,
                "account": entry.account.value,
                "amount_minor": entry.amount_minor,
                "amount": format_amount(entry.amount_minor),
                "currency": entry.currency,
                "hash_short": entry.hash[:SHORT_HASH_LENGTH],
                "reference": reference,
                "donor_name": donor,
            }
        )
    return rows


def _by_id(session: Session, model: type[Any], ids: set[uuid.UUID]) -> dict[uuid.UUID, Any]:
    if not ids:
        return {}
    return {
        row.id: row for row in session.execute(select(model).where(model.id.in_(ids))).scalars()
    }
