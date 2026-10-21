"""The append-only, hash-chained ledger.

Every entry's hash covers its own fields and the previous entry's hash, so changing or removing
any row breaks verification from that point on. Appends take a PostgreSQL advisory lock, which
makes the chain strictly serial. This is the core of the service; the public verification
endpoints are added with the ledger API (P2-F10).
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.enums import LedgerAccount, LedgerEntryType
from app.models.ledger import GENESIS_HASH, LedgerEntry

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
    valid: bool
    entries_checked: int
    last_hash: str
    first_bad_seq: int | None = None


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

    The chain is global, so a case is only trusted when the whole chain is intact.
    """
    expected_prev = GENESIS_HASH
    checked = 0
    last_hash = GENESIS_HASH
    rows = session.execute(select(LedgerEntry).order_by(LedgerEntry.seq)).scalars()
    for entry in rows:
        if entry.prev_hash != expected_prev or entry_hash(entry) != entry.hash:
            return ChainStatus(False, checked, last_hash, first_bad_seq=entry.seq)
        if case_id is None or entry.case_id == case_id:
            checked += 1
        expected_prev = last_hash = entry.hash
    return ChainStatus(True, checked, last_hash)
