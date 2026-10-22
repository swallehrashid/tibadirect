"""The treasurer agent's tools: seven that read and three that propose.

What a tool returns is what the language model sees, so it is kept to money, statuses and IDs.
There are no donor names, emails, patient references or document text in any result, and a list is
cut to ``MAX_ROWS`` rows. A tool never raises to the model: a problem comes back as
``{"error": "..."}`` so it can say so. The propose tools write a ``proposed`` action and nothing
else; only an administrator's approval (``agent_service.approve``) carries one out.
"""

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.case import Case, ReviewItem
from app.models.enums import ReviewStatus
from app.models.identity import User
from app.models.money import Donation, Settlement
from app.money import MoneyError, format_money, to_minor
from app.payments.paypal_client import get_client
from app.services import agent_service, case_service, fx_service, ledger_service, refund_service

MAX_ROWS = 50
MAX_SEARCH_DAYS = 31
SHORT = 12


class ToolError(Exception):
    """A request the tool cannot satisfy; the message is shown to the model."""


def _case(session: Session, reference: Any) -> Case:
    """A case by its ID or its public slug."""
    text = str(reference or "").strip()
    if not text:
        raise ToolError("Give a case ID or slug.")
    case = None
    try:
        case = session.get(Case, uuid.UUID(text))
    except ValueError:
        case = session.scalar(select(Case).where(Case.public_slug == text))
    if case is None:
        raise ToolError("There is no such case.")
    return case


def _case_id(session: Session, value: Any) -> uuid.UUID:
    return _case(session, value).id


def _uuid(value: Any, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ToolError(f"{what} must be an ID.") from exc


def _money(minor: int | None, currency: str) -> str | None:
    return None if minor is None else format_money(minor, currency)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _short(value: Any) -> str | None:
    return None if value is None else str(value)[:SHORT]


# --------------------------------------------------------------------------- read tools


def get_case_summary(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    case = _case(session, args.get("case"))
    open_reviews = session.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.case_id == case.id, ReviewItem.status == ReviewStatus.OPEN)
    )
    triage = case_service.latest_triage(case)
    return {
        "case_id": str(case.id),
        "slug": case.public_slug,
        "alias": case.alias,
        "status": case.status.value,
        "trust_level": case.trust_level.value,
        "close_reason": case.close_reason.value if case.close_reason else None,
        "hospital": case.hospital.display_name,
        "target_kes": _money(case.target_kes_minor, "KES"),
        "is_estimate": case.is_estimate,
        "usd_goal": _money(case.usd_goal_minor, "USD"),
        "captured_net_usd": _money(case.captured_net_usd_minor, "USD"),
        "settled_to_hospital_usd": _money(case.settled_usd_minor, "USD"),
        "settled_to_hospital_kes": _money(case.settled_kes_minor, "KES"),
        "refunded_usd": _money(case.refunded_usd_minor, "USD"),
        "donor_count": case.donor_count,
        "open_review_items": open_reviews,
        "latest_triage": (
            {
                "risk_band": triage.risk_band.value,
                "risk_score": triage.risk_score,
                "route": triage.route.value,
                "signals": [s.get("code") for s in triage.signals if isinstance(s, dict)],
            }
            if triage
            else None
        ),
        "created_at": _iso(case.created_at),
        "live_at": _iso(case.live_at),
    }


def list_donations(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    case_id = _case_id(session, args.get("case_id"))
    rows = session.scalars(
        select(Donation)
        .where(Donation.case_id == case_id)
        .order_by(Donation.created_at.desc(), Donation.id)
        .limit(MAX_ROWS + 1)
    ).all()
    return {
        "truncated": len(rows) > MAX_ROWS,
        "donations": [
            {
                "donation_id": str(d.id),
                "status": d.status.value,
                "amount_usd_minor": d.amount_usd_minor,
                "fee_usd_minor": d.paypal_fee_usd_minor,
                "net_usd_minor": d.net_usd_minor,
                "paid_to_hospital_usd_minor": d.allocated_usd_minor,
                "refunded_usd_minor": d.refunded_usd_minor,
                "pooled_usd_minor": d.pooled_usd_minor,
                "still_held_usd_minor": refund_service.refundable_usd_minor(d),
                "excess_choice": d.excess_choice.value,
                "paypal_capture_id": d.paypal_capture_id,
                "captured_at": _iso(d.captured_at),
            }
            for d in rows[:MAX_ROWS]
        ],
    }


def get_ledger(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    case_id = _case_id(session, args.get("case_id"))
    entries = session.scalars(
        ledger_service.case_entries(case_id)
        .order_by(None)
        .order_by(ledger_service.LedgerEntry.seq.desc())
        .limit(MAX_ROWS + 1)
    ).all()
    return {
        "truncated": len(entries) > MAX_ROWS,
        "note": "Newest first.",
        "entries": [
            {
                "seq": e.seq,
                "type": e.entry_type.value,
                "account": e.account.value,
                "amount_minor": e.amount_minor,
                "currency": e.currency,
                "ref_type": e.ref_type,
                "ref_id": _short(e.ref_id),
                "hash": _short(e.hash),
                "created_at": _iso(e.created_at),
            }
            for e in entries[:MAX_ROWS]
        ],
    }


def verify_ledger(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    case_id = _case_id(session, args.get("case_id"))
    status = ledger_service.verify_chain(session, case_id)
    return {
        "valid": status.valid,
        "entries_checked": status.entries_checked,
        "first_bad_seq": status.first_bad_seq,
    }


def list_settlements(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    case_id = _case_id(session, args.get("case_id"))
    rows = session.scalars(
        select(Settlement)
        .where(Settlement.case_id == case_id)
        .order_by(Settlement.tranche_no)
        .limit(MAX_ROWS + 1)
    ).all()
    return {
        "truncated": len(rows) > MAX_ROWS,
        "settlements": [
            {
                "settlement_id": str(s.id),
                "tranche_no": s.tranche_no,
                "status": s.status.value,
                "adapter": s.adapter.value,
                "amount_usd_minor": s.amount_usd_minor,
                "amount_kes_minor": s.amount_kes_minor,
                "paypal_batch_id": s.external_batch_id,
                "failure_reason": s.failure_reason,
                "requested_at": _iso(s.requested_at),
                "completed_at": _iso(s.completed_at),
            }
            for s in rows[:MAX_ROWS]
        ],
    }


def _date(value: Any, what: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(
            f"{what} must be a date or time like 2026-10-23 or 2026-10-23T09:00:00Z."
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def paypal_transaction_search(
    session: Session, admin: User, args: dict[str, Any]
) -> dict[str, Any]:
    start, end = _date(args.get("start"), "start"), _date(args.get("end"), "end")
    if end <= start:
        raise ToolError("end must be after start.")
    if end - start > timedelta(days=MAX_SEARCH_DAYS):
        raise ToolError(f"PayPal searches at most {MAX_SEARCH_DAYS} days at a time.")
    found = get_client().search_transactions(start=start, end=end)
    rows = []
    for detail in found.get("transaction_details", []):
        info = detail.get("transaction_info", {}) if isinstance(detail, dict) else {}
        rows.append(
            {
                "transaction_id": info.get("transaction_id"),
                "event_code": info.get("transaction_event_code"),
                "initiated": info.get("transaction_initiation_date"),
                "status": info.get("transaction_status"),
                "amount": (info.get("transaction_amount") or {}).get("value"),
                "currency": (info.get("transaction_amount") or {}).get("currency_code"),
                "fee": (info.get("fee_amount") or {}).get("value"),
                "reference": info.get("paypal_reference_id") or info.get("custom_field"),
            }
        )
    return {
        "truncated": len(rows) > MAX_ROWS,
        "total_items": found.get("total_items", len(rows)),
        "note": "PayPal's sandbox report can lag by several hours behind a payment.",
        "transactions": rows[:MAX_ROWS],
    }


def get_fx_rate(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    rate = fx_service.current_rate(session)
    return {
        "usd_to_kes": str(rate.rate),
        "source": rate.source,
        "fetched_at": _iso(rate.fetched_at),
        "is_live": rate.is_live,
        "is_stale": rate.is_stale(),
    }


# --------------------------------------------------------------------------- propose tools


def _proposal(action: Any) -> dict[str, Any]:
    return {
        "action_id": str(action.id),
        "status": action.status.value,
        "note": "Written down for an administrator to approve or reject. Nothing has been done.",
    }


def propose_refund(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    try:
        amount = Decimal(str(args.get("amount_usd")))
        minor = to_minor(amount)
    except (InvalidOperation, MoneyError) as exc:
        raise ToolError("amount_usd must be a number of dollars, like 12.50.") from exc
    if amount != amount.quantize(Decimal("0.01")):
        raise ToolError("amount_usd can have at most two decimal places.")
    action = agent_service.propose_refund(
        session,
        admin,
        donation_id=_uuid(args.get("donation_id"), "donation_id"),
        amount_usd_minor=minor,
        reason=str(args.get("reason") or ""),
    )
    return _proposal(action)


def propose_payout(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    action = agent_service.propose_payout(
        session,
        admin,
        case_id=_case_id(session, args.get("case_id")),
        reason=str(args.get("reason") or ""),
    )
    return _proposal(action)


def propose_freeze(session: Session, admin: User, args: dict[str, Any]) -> dict[str, Any]:
    action = agent_service.propose_freeze(
        session,
        admin,
        case_id=_case_id(session, args.get("case_id")),
        reason=str(args.get("reason") or ""),
    )
    return _proposal(action)


# --------------------------------------------------------------------------- the registry


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]
    run: Callable[[Session, User, dict[str, Any]], dict[str, Any]]
    proposes: bool = False

    def spec(self) -> dict[str, Any]:
        """The tool as the model provider describes it."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


_CASE_ID = {"type": "string", "description": "The case's ID or its public slug"}
_REASON = {
    "type": "string",
    "description": "Why, in one or two plain sentences (10 to 500 characters)",
}

TOOLS: tuple[Tool, ...] = (
    Tool(
        "get_case_summary",
        "A case's status, hospital, target, money totals and latest automatic check.",
        {"case": _CASE_ID},
        ("case",),
        get_case_summary,
    ),
    Tool(
        "list_donations",
        "The donations to a case with what is paid, refunded, pooled and still held (newest 50).",
        {"case_id": _CASE_ID},
        ("case_id",),
        list_donations,
    ),
    Tool(
        "get_ledger",
        "The case's ledger entries, newest first (50 at most).",
        {"case_id": _CASE_ID},
        ("case_id",),
        get_ledger,
    ),
    Tool(
        "verify_ledger",
        "Recompute the hash chain and say whether the ledger is intact.",
        {"case_id": _CASE_ID},
        ("case_id",),
        verify_ledger,
    ),
    Tool(
        "list_settlements",
        "The tranches paid or being paid to the hospital for a case.",
        {"case_id": _CASE_ID},
        ("case_id",),
        list_settlements,
    ),
    Tool(
        "paypal_transaction_search",
        "PayPal's own record of transactions between two dates (at most 31 days). The sandbox "
        "report can lag by hours.",
        {
            "start": {
                "type": "string",
                "description": "Start, like 2026-10-01 or 2026-10-01T00:00:00Z",
            },
            "end": {"type": "string", "description": "End, in the same form"},
        },
        ("start", "end"),
        paypal_transaction_search,
    ),
    Tool(
        "get_fx_rate",
        "The current US dollar to Kenya shilling rate and where it came from.",
        {},
        (),
        get_fx_rate,
    ),
    Tool(
        "propose_refund",
        "Propose refunding part or all of what the case still holds of a donation. Waits for an "
        "administrator.",
        {
            "donation_id": {"type": "string", "description": "The donation's ID"},
            "amount_usd": {"type": "string", "description": "Dollars to refund, like 12.50"},
            "reason": _REASON,
        },
        ("donation_id", "amount_usd", "reason"),
        propose_refund,
        proposes=True,
    ),
    Tool(
        "propose_payout",
        "Propose checking now whether a tranche is due for a case and paying it. Waits for an "
        "administrator.",
        {"case_id": _CASE_ID, "reason": _REASON},
        ("case_id", "reason"),
        propose_payout,
        proposes=True,
    ),
    Tool(
        "propose_freeze",
        "Propose freezing a case: it stops taking money and every donor is refunded. Waits for "
        "an administrator.",
        {"case_id": _CASE_ID, "reason": _REASON},
        ("case_id", "reason"),
        propose_freeze,
        proposes=True,
    ),
)
BY_NAME = {tool.name: tool for tool in TOOLS}


def specs() -> list[dict[str, Any]]:
    return [tool.spec() for tool in TOOLS]


def call(session: Session, admin: User, name: str, arguments: Any) -> tuple[dict[str, Any], bool]:
    """Run a tool. Returns what to show the model and whether it worked. Never raises."""
    tool = BY_NAME.get(name)
    if tool is None:
        return {"error": f"There is no tool called {name}."}, False
    if not isinstance(arguments, dict):
        return {"error": "The tool input must be an object."}, False
    unknown = set(arguments) - set(tool.properties)
    if unknown:
        return {"error": f"Unknown input: {', '.join(sorted(unknown))}."}, False
    missing = [key for key in tool.required if arguments.get(key) in (None, "")]
    if missing:
        return {"error": f"Missing input: {', '.join(missing)}."}, False
    try:
        return tool.run(session, admin, arguments), True
    except ToolError as error:
        return {"error": str(error)}, False
    except AppError as error:
        return {"error": error.message}, False


def render(result: dict[str, Any], limit: int = 12_000) -> str:
    """The result as the JSON text the model reads, cut if it is huge."""
    text = json.dumps(result, default=str, separators=(",", ":"))
    if len(text) <= limit:
        return text
    return json.dumps({"error": "The result was too large to show; ask for less."})
