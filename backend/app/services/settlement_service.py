"""Paying the hospital in tranches.

``run_settlement_check`` decides whether a tranche is due and, if so, writes it down: a
``pending`` settlement and the first-in-first-out allocation of donations that fund it, all in one
short transaction. ``execute_settlement`` then sends it through the hospital's adapter, and
``poll_settlement`` follows it until the provider says it is paid or failed. Every step locks the
case first, repeats safely, and changes the ledger and totals in the same transaction as the
status it changes.

The rule for when a tranche is due (specification 8.6):

* The case must be hospital confirmed, live or funded, with a confirmed bill.
* Money available is the net of captured donations not yet paid out or refunded.
* While the bill is an estimate, no more than ``ESTIMATE_SETTLEMENT_CAP_BPS`` of it may be
  paid (counting tranches still in flight, so two quick checks cannot overshoot the cap).
* The first tranche waits for the deposit the hospital asked for, if it asked for one; every
  other tranche waits for the larger of the minimum amount and a quarter of the bill. The last
  tranche is paid whatever its size.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING

from flask import current_app
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import constants
from app.jobs import queue as jobs
from app.logging import scrub
from app.models.case import Case
from app.models.enums import (
    ActorType,
    CaseStatus,
    ConfirmationStatus,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    ReviewSource,
    SettlementStatus,
    TrustLevel,
)
from app.models.money import Donation, Settlement, SettlementAllocation
from app.models.verification import Confirmation
from app.money import apply_bps, format_money, kes_minor_to_usd_minor, usd_minor_to_kes_minor
from app.payments.adapters import AdapterResult, NotConfigured, adapter_for
from app.payments.errors import PayPalError
from app.services import (
    audit_service,
    case_service,
    excess_policy_service,
    fx_service,
    invoicing_service,
    ledger_service,
    mail_service,
    notification_service,
    refund_service,
    review_service,
)

logger = logging.getLogger(__name__)

SETTLEABLE_STATUSES = frozenset({CaseStatus.LIVE, CaseStatus.FUNDED})
OPEN_STATUSES = (SettlementStatus.PENDING, SettlementStatus.PROCESSING, SettlementStatus.SUCCEEDED)
HOLDING_STATUSES = (DonationStatus.CAPTURED, DonationStatus.PARTIALLY_REFUNDED)
# Provider problems that need a person, not a retry: the settlement stays unsent until fixed.
CONFIGURATION_ERRORS = frozenset({"paypal_auth_failed", "paypal_bad_response"})
PENDING_RESUME_AFTER = timedelta(minutes=5)
STUCK_AFTER = timedelta(days=3)
MAX_REASON_CHARS = 300


@dataclass(frozen=True)
class CheckResult:
    """The outcome of a tranche check. ``reason`` is ``created`` or why no tranche was made."""

    settlement: Settlement | None
    reason: str


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- reading the books


def latest_confirmation(case: Case) -> Confirmation | None:
    """The newest confirmed bill, which sets the target."""
    confirmed = [c for c in case.confirmations if c.status == ConfirmationStatus.CONFIRMED]
    return max(
        confirmed, key=lambda c: (c.finalized_at or c.created_at, c.created_at), default=None
    )


def committed_kes_minor(session: Session, case: Case) -> int:
    """KES already paid or on its way: succeeded, processing and still-unsent tranches."""
    return int(
        session.execute(
            select(func.coalesce(func.sum(Settlement.amount_kes_minor), 0)).where(
                Settlement.case_id == case.id, Settlement.status.in_(OPEN_STATUSES)
            )
        ).scalar_one()
    )


def holdable(donation: Donation, pending_refunds: int = 0) -> int:
    """What a donation can still give a tranche: what the case holds of it (net minus paid out,
    refunded and pooled) less any refund already promised to the donor."""
    return max(0, refund_service.refundable_usd_minor(donation) - pending_refunds)


def settlement_cap_kes_minor(case: Case) -> int:
    """The most that may be paid so far: the whole bill, or the estimate cap while it is one."""
    target = case.target_kes_minor or 0
    if case.is_estimate:
        return apply_bps(target, constants.policy().estimate_cap_bps)
    return target


# --------------------------------------------------------------------------- creating a tranche


def run_settlement_check(
    session: Session, case_id: uuid.UUID, *, request_id: str | None = None
) -> CheckResult:
    """Create the next tranche if one is due, then queue it for sending. Commits when it acts."""
    case = case_service.lock_case(session, case_id)
    result = _create_tranche(session, case, request_id=request_id)
    if result.settlement is None:
        session.rollback()
        return result
    session.commit()
    enqueue_execute(result.settlement.id)
    return result


def _create_tranche(session: Session, case: Case, *, request_id: str | None) -> CheckResult:
    if case.status not in SETTLEABLE_STATUSES or case.trust_level != TrustLevel.HOSPITAL_CONFIRMED:
        return CheckResult(None, "case_not_settleable")
    confirmation = latest_confirmation(case)
    if confirmation is None or case.target_kes_minor is None:
        return CheckResult(None, "no_confirmation")
    hospital = case.hospital
    if not hospital.is_active:
        return CheckResult(None, "hospital_inactive")
    if adapter_for(hospital.settlement_method).check_ready(hospital) is not None:
        return CheckResult(None, "hospital_not_ready")

    policy = constants.policy()
    rate = fx_service.latest(session)
    donations = list(
        session.execute(
            select(Donation)
            .where(Donation.case_id == case.id, Donation.status.in_(HOLDING_STATUSES))
            .order_by(Donation.captured_at, Donation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalars()
    )
    promised = refund_service.pending_by_donation(session, case.id)
    available_usd = sum(holdable(d, promised.get(d.id, 0)) for d in donations)
    remaining_kes = settlement_cap_kes_minor(case) - committed_kes_minor(session, case)
    if remaining_kes <= 0:
        return CheckResult(None, "cap_reached")
    if available_usd <= 0:
        return CheckResult(None, "nothing_available")

    first = (
        session.execute(
            select(func.count())
            .select_from(Settlement)
            .where(Settlement.case_id == case.id, Settlement.status.in_(OPEN_STATUSES))
        ).scalar_one()
        == 0
    )
    deposit = confirmation.deposit_kes_minor or 0
    if first and deposit > 0:
        threshold = deposit
    else:
        threshold = max(
            policy.tranche_min_kes_minor,
            apply_bps(case.target_kes_minor, policy.tranche_fraction_bps),
        )
    threshold = min(threshold, remaining_kes)
    if usd_minor_to_kes_minor(available_usd, rate.rate) < threshold:
        return CheckResult(None, "below_threshold")

    # Round the room left up, so the last tranche can reach the bill to the cent.
    room_usd = kes_minor_to_usd_minor(remaining_kes, rate.rate, rounding=ROUND_CEILING)
    amount_usd = min(available_usd, room_usd)
    amount_kes = min(usd_minor_to_kes_minor(amount_usd, rate.rate), remaining_kes)
    if amount_usd <= 0 or amount_kes <= 0:
        return CheckResult(None, "nothing_available")

    latest_tranche = session.execute(
        select(func.coalesce(func.max(Settlement.tranche_no), 0)).where(
            Settlement.case_id == case.id
        )
    ).scalar_one()
    settlement_id = uuid.uuid4()
    settlement = Settlement(
        id=settlement_id,
        case=case,
        confirmation=confirmation,
        tranche_no=latest_tranche + 1,
        adapter=hospital.settlement_method,
        status=SettlementStatus.PENDING,
        amount_usd_minor=amount_usd,
        amount_kes_minor=amount_kes,
        fx_rate=rate,
        idempotency_key=f"tiba-settle-{settlement_id.hex}",
    )
    session.add(settlement)
    session.flush()

    left = amount_usd
    for donation in donations:
        take = min(left, holdable(donation, promised.get(donation.id, 0)))
        if take <= 0:
            continue
        donation.allocated_usd_minor += take
        session.add(
            SettlementAllocation(settlement=settlement, donation=donation, amount_usd_minor=take)
        )
        left -= take
        if left == 0:
            break
    if left:  # cannot happen: amount_usd never exceeds what the donations hold
        raise RuntimeError("Settlement allocation fell short")
    session.flush()
    audit_service.record(
        "settlement.created",
        settlement,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={
            "case_id": case.id,
            "tranche_no": settlement.tranche_no,
            "amount_usd_minor": amount_usd,
            "amount_kes_minor": amount_kes,
            "adapter": settlement.adapter,
        },
    )
    return CheckResult(settlement, "created")


# --------------------------------------------------------------------------- sending and polling


def _lock_settlement(session: Session, settlement_id: uuid.UUID) -> tuple[Case, Settlement] | None:
    """The case and the settlement, locked in that order, or None if there is no such settlement."""
    case_id = session.execute(
        select(Settlement.case_id).where(Settlement.id == settlement_id)
    ).scalar_one_or_none()
    if case_id is None:
        return None
    case = case_service.lock_case(session, case_id)
    settlement = session.execute(
        select(Settlement)
        .where(Settlement.id == settlement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return case, settlement


def execute_settlement(
    session: Session, settlement_id: uuid.UUID, *, request_id: str | None = None
) -> SettlementStatus | None:
    """Send a ``pending`` tranche. Safe to repeat: the provider sees the same idempotency key.

    A tranche whose case has since been frozen or cancelled is cancelled instead of sent. A
    provider outage rolls back and raises, so the queue retries; a refusal fails the tranche.
    """
    locked = _lock_settlement(session, settlement_id)
    if locked is None:
        return None
    case, settlement = locked
    if settlement.status != SettlementStatus.PENDING:
        session.rollback()
        return settlement.status

    if case.status not in SETTLEABLE_STATUSES or case.trust_level != TrustLevel.HOSPITAL_CONFIRMED:
        _fail(
            session,
            case,
            settlement,
            "Cancelled: the case no longer takes settlements",
            request_id,
            review=False,
        )
        session.commit()
        return SettlementStatus.FAILED
    hospital = case.hospital
    adapter = adapter_for(settlement.adapter)
    if problem := adapter.check_ready(hospital):
        _fail(session, case, settlement, problem, request_id)
        session.commit()
        return SettlementStatus.FAILED
    try:
        result = adapter.send(settlement, hospital)
    except NotConfigured as exc:
        _fail(session, case, settlement, str(exc), request_id)
        session.commit()
        return SettlementStatus.FAILED
    except PayPalError as error:
        if error.retryable or error.code in CONFIGURATION_ERRORS:
            session.rollback()
            raise
        _fail(session, case, settlement, error.code, request_id)
        session.commit()
        return SettlementStatus.FAILED

    open_before = case.status != CaseStatus.CLOSED
    status = _apply(session, case, settlement, result, request_id)
    session.commit()
    _notify_if_paid(session, settlement.id, SettlementStatus.PENDING, status, open_before)
    return status


def poll_settlement(
    session: Session,
    settlement_id: uuid.UUID,
    *,
    include_finished: bool = False,
    request_id: str | None = None,
) -> SettlementStatus | None:
    """Ask the provider where a tranche stands and apply the answer.

    Only ``processing`` tranches are asked about, unless ``include_finished`` is set (a webhook
    that reports a change to a tranche we already closed, which needs a person to look).
    """
    locked = _lock_settlement(session, settlement_id)
    if locked is None:
        return None
    case, settlement = locked
    wanted = (SettlementStatus.PROCESSING,)
    if include_finished:
        wanted = (SettlementStatus.PROCESSING, SettlementStatus.SUCCEEDED, SettlementStatus.FAILED)
    if settlement.status not in wanted:
        session.rollback()
        return settlement.status
    before = settlement.status
    open_before = case.status != CaseStatus.CLOSED
    result = adapter_for(settlement.adapter).poll(settlement)
    status = _apply(session, case, settlement, result, request_id)
    session.commit()
    _notify_if_paid(session, settlement.id, before, status, open_before)
    return status


def _apply(
    session: Session,
    case: Case,
    settlement: Settlement,
    result: AdapterResult,
    request_id: str | None,
) -> SettlementStatus:
    """Record what the provider said. A final answer that contradicts ours only opens a review."""
    if result.batch_id:
        settlement.external_batch_id = result.batch_id
    if result.item_id:
        settlement.external_item_id = result.item_id

    if settlement.status in (SettlementStatus.SUCCEEDED, SettlementStatus.FAILED):
        contradicts = (settlement.status == SettlementStatus.SUCCEEDED) != (
            result.status == "succeeded"
        )
        if result.status != "processing" and contradicts:
            review_service.create_item(
                session,
                case,
                ReviewSource.ADMIN,
                "The payment provider's report of a tranche differs from our record",
                payload={
                    "settlement_id": str(settlement.id),
                    "our_status": settlement.status.value,
                    "provider_status": result.status,
                    "provider_reason": scrub(result.failure or "")[:100],
                },
            )
        return settlement.status

    if result.status == "succeeded":
        _succeed(session, case, settlement, request_id)
        return SettlementStatus.SUCCEEDED
    if result.status == "failed":
        _fail(session, case, settlement, result.failure or "failed", request_id)
        return SettlementStatus.FAILED
    settlement.status = SettlementStatus.PROCESSING
    settlement.updated_at = _now()  # marks the time of this check for the polling back-off
    return SettlementStatus.PROCESSING


def _succeed(session: Session, case: Case, settlement: Settlement, request_id: str | None) -> None:
    settlement.status = SettlementStatus.SUCCEEDED
    settlement.completed_at = _now()
    settlement.failure_reason = None
    session.flush()
    ledger_service.append(
        session,
        [
            ledger_service.NewEntry(
                case.id,
                LedgerEntryType.SETTLEMENT_SENT,
                LedgerAccount.CASE_FUNDS,
                -settlement.amount_usd_minor,
                "USD",
                "settlement",
                settlement.id,
            ),
            ledger_service.NewEntry(
                case.id,
                LedgerEntryType.SETTLEMENT_SENT,
                LedgerAccount.HOSPITAL_SETTLED,
                settlement.amount_kes_minor,
                "KES",
                "settlement",
                settlement.id,
            ),
        ],
    )
    session.expire(case, ["donations", "settlements"])
    case_service.recompute_totals(case)
    audit_service.record(
        "settlement.succeeded",
        settlement,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={"case_id": case.id, "tranche_no": settlement.tranche_no},
    )
    case_service.close_if_fully_settled(case)


def _fail(
    session: Session,
    case: Case,
    settlement: Settlement,
    reason: str,
    request_id: str | None,
    *,
    review: bool = True,
) -> None:
    """Close a tranche as failed and give its money back to the donations it came from.

    A failure the provider caused opens a review item; a tranche cancelled because the case was
    frozen or cancelled does not, since that is the expected result of the admin's own action.
    """
    settlement.status = SettlementStatus.FAILED
    settlement.completed_at = _now()
    settlement.failure_reason = scrub(reason)[:MAX_REASON_CHARS]
    allocations = list(
        session.execute(
            select(SettlementAllocation).where(SettlementAllocation.settlement_id == settlement.id)
        ).scalars()
    )
    for allocation in allocations:
        allocation.donation.allocated_usd_minor -= allocation.amount_usd_minor
        session.delete(allocation)
    session.flush()
    session.expire(case, ["donations", "settlements"])
    case_service.recompute_totals(case)
    audit_service.record(
        "settlement.failed",
        settlement,
        actor=ActorType.SYSTEM,
        request_id=request_id,
        details={"case_id": case.id, "tranche_no": settlement.tranche_no},
    )
    if review:
        review_service.create_item(
            session,
            case,
            ReviewSource.ADMIN,
            "A payment to the hospital failed",
            payload={
                "settlement_id": str(settlement.id),
                "tranche_no": settlement.tranche_no,
                "reason": settlement.failure_reason,
            },
        )


def cancel_unsent(
    session: Session, case: Case, reason: str, *, request_id: str | None = None
) -> int:
    """Cancel every tranche not yet sent and free its money. Tranches already at the provider
    are left to finish. The caller holds the case lock and commits. Returns how many."""
    unsent = list(
        session.execute(
            select(Settlement).where(
                Settlement.case_id == case.id, Settlement.status == SettlementStatus.PENDING
            )
        ).scalars()
    )
    for settlement in unsent:
        _fail(session, case, settlement, f"Cancelled: {reason}", request_id, review=False)
    return len(unsent)


# --------------------------------------------------------------------------- after commit


def _notify_if_paid(
    session: Session,
    settlement_id: uuid.UUID,
    before: SettlementStatus,
    after: SettlementStatus,
    case_was_open: bool,
) -> None:
    """Email about a tranche that has just been paid, once, and about the case closing if that
    payment was the last. Never undoes the payment."""
    settlement = session.get(Settlement, settlement_id)
    if settlement is not None and settlement.case.status == CaseStatus.FROZEN:
        _enqueue("app.jobs.tasks.finish_frozen_case", str(settlement.case_id))
    if before == SettlementStatus.SUCCEEDED or after != SettlementStatus.SUCCEEDED:
        return
    if settlement is None:
        return
    notify_settled(settlement)
    invoicing_service.enqueue_payment(settlement)
    if case_was_open and settlement.case.status == CaseStatus.CLOSED:
        notification_service.notify_closed(settlement.case)
        excess_policy_service.enqueue_run(settlement.case_id)


def notify_settled(settlement: Settlement) -> None:
    """Tell the family and the hospital that a tranche has been paid."""
    case = settlement.case
    hospital = case.hospital
    reference = ledger_service.short_reference(
        settlement.external_item_id or settlement.external_batch_id
    )
    context = {
        "tranche": settlement.tranche_no,
        "amount_kes": format_money(settlement.amount_kes_minor, "KES"),
        "amount_usd": format_money(settlement.amount_usd_minor, "USD"),
        "reference": reference or "",
        "invoice": settlement.confirmation.invoice_number or "",
        "simulated": settlement.adapter.value != "jenga",
        "case_url": f"{current_app.config['APP_BASE_URL'].rstrip('/')}/c/{case.public_slug}",
    }
    notification_service.notify_family(case, "tranche_settled", audience="family", **context)
    try:
        mail_service.send(
            "tranche_settled",
            hospital.official_email,
            language=mail_service.DEFAULT_LANGUAGE,
            alias=case.alias,
            audience="hospital",
            **context,
        )
    except mail_service.MailError as exc:
        logger.error("hospital settlement email failed", extra={"reason": str(exc)})


# --------------------------------------------------------------------------- queueing and sweeps


def enqueue_check(case_id: uuid.UUID) -> None:
    """Queue a tranche check for a case. Call it after the transaction that made money available
    has committed. A queue outage is logged; the periodic sweep makes up for it."""
    _enqueue("app.jobs.tasks.run_settlement_check", str(case_id))


def enqueue_execute(settlement_id: uuid.UUID) -> None:
    _enqueue("app.jobs.tasks.execute_settlement", str(settlement_id))


def _enqueue(func: str, argument: str) -> None:
    try:
        jobs.enqueue(jobs.QUEUE_PAYMENTS, func, argument)
    except RedisError:
        logger.exception("could not queue a settlement job; the sweep will retry")


def poll_interval(age: timedelta) -> timedelta:
    """How long to wait between asking the provider: 2 minutes at first, then less often."""
    if age < timedelta(minutes=10):
        return timedelta(minutes=2)
    if age < timedelta(hours=2):
        return timedelta(minutes=10)
    return timedelta(minutes=30)


def poll_open_settlements(session: Session) -> int:
    """Ask the provider about every tranche that is due a check. Returns how many were asked.

    One tranche failing to answer never stops the others.
    """
    now = _now()
    rows = session.execute(
        select(Settlement.id, Settlement.requested_at, Settlement.updated_at).where(
            Settlement.status == SettlementStatus.PROCESSING
        )
    ).all()
    asked = 0
    for settlement_id, requested_at, updated_at in rows:
        if now - updated_at < poll_interval(now - requested_at):
            continue
        try:
            poll_settlement(session, settlement_id)
            asked += 1
        except Exception:
            session.rollback()
            logger.exception("polling a settlement failed")
        else:
            _flag_if_stuck(session, settlement_id, now)
    return asked


def _flag_if_stuck(session: Session, settlement_id: uuid.UUID, now: datetime) -> None:
    settlement = session.get(Settlement, settlement_id)
    if (
        settlement is None
        or settlement.status != SettlementStatus.PROCESSING
        or settlement.failure_reason
        or now - settlement.requested_at < STUCK_AFTER
    ):
        return
    settlement.failure_reason = "Still processing after three days"
    review_service.create_item(
        session,
        settlement.case,
        ReviewSource.ADMIN,
        "A payment to the hospital has not finished after three days",
        payload={"settlement_id": str(settlement.id), "tranche_no": settlement.tranche_no},
    )
    session.commit()


def sweep(session: Session) -> int:
    """The periodic safety net: re-send tranches left unsent, and check every open case.

    Returns how many jobs were queued.
    """
    queued = 0
    cutoff = _now() - PENDING_RESUME_AFTER
    for settlement_id in session.execute(
        select(Settlement.id).where(
            Settlement.status == SettlementStatus.PENDING, Settlement.created_at < cutoff
        )
    ).scalars():
        enqueue_execute(settlement_id)
        queued += 1
    for case_id in session.execute(
        select(Case.id).where(
            Case.status.in_(SETTLEABLE_STATUSES),
            Case.trust_level == TrustLevel.HOSPITAL_CONFIRMED,
            Case.captured_net_usd_minor > Case.settled_usd_minor,
        )
    ).scalars():
        enqueue_check(case_id)
        queued += 1
    return queued
