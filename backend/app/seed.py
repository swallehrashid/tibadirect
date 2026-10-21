"""Demo data: ``flask seed`` and ``flask reset-demo``.

Everything is fictional. The seed builds one small world: an admin, the facility register, three
onboarded hospitals with billing staff, four families and eight cases that together cover every
stage of the lifecycle, including real-looking donations, settlements, refunds, a solidarity pool
transfer and a valid ledger chain. Seeded PayPal IDs start with ``SEED-`` and are never sent to
PayPal.

The money helpers here write the same ledger entries the payment services will write later, and
the cached case totals are recomputed from the rows at the end. P2-F07 replaces the local
recompute with ``case_service.recompute_totals``.
"""

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.case import (
    Case,
    CaseDocument,
    Consent,
    ReviewItem,
    TriageResult,
    hash_invoice_fingerprint,
)
from app.models.enums import (
    ActorType,
    CaseStatus,
    CloseReason,
    ConfirmationRequestStatus,
    ConfirmationStatus,
    ConsentActor,
    ConsentScope,
    DocumentKind,
    DocumentStatus,
    DonationStatus,
    ExcessChoice,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    RefundStatus,
    ReviewResolution,
    ReviewSource,
    ReviewStatus,
    RiskBand,
    SettlementMethod,
    SettlementStatus,
    StaffRole,
    TriageRoute,
    TrustLevel,
    UserRole,
)
from app.models.hospital import (
    FacilityRegisterEntry,
    Hospital,
    HospitalStaff,
    normalize_facility_name,
)
from app.models.identity import User
from app.models.ledger import LedgerEntry
from app.models.money import Donation, FxRate, Refund, Settlement, SettlementAllocation
from app.models.platform import AuditLog
from app.models.verification import Confirmation, ConfirmationRequest
from app.money import apply_bps, kes_minor_to_usd_minor, usd_goal_minor, usd_minor_to_kes_minor
from app.security import crypto
from app.security.masking import mask_email
from app.security.uploads import ALLOWED_TYPES
from app.services import ledger_service
from app.services.ledger_service import NewEntry
from app.services.storage_service import DEMO_PREFIX, document_key
from app.synthetic_data import DEFAULT_DATA_DIR, DatasetDocument, load_documents, load_register

ADMIN_EMAIL = "admin@tibadirect.test"
FAMILY_EMAILS = [f"family{n}@example.test" for n in range(1, 5)]
SEED_SOURCE = "seed"

# The first three register entries that print invoices are onboarded as demo hospitals.
ONBOARDED = {
    "kilele": ("FR-NBI-0101", SettlementMethod.PAYPAL_PAYOUTS),
    "mtomwema": ("FR-MSA-0202", SettlementMethod.PAYPAL_PAYOUTS),
    "ziwabluu": ("FR-KSM-0303", SettlementMethod.MOCK_PARTNER),
}

# Fixed public slugs so demo links survive a reset (12 characters of a-z and 2-7).
SLUGS = {
    "draft": "demodraft234",
    "pending_triage": "demotriage23",
    "in_review": "demoreview23",
    "pending_confirmation": "democonfirm2",
    "live": "demolive2345",
    "funded": "demofunded23",
    "closed": "democlosed23",
    "cancelled": "democancel23",
}

PAYPAL_FEE_BPS = 290  # 2.9 percent
PAYPAL_FEE_FIXED_USD_MINOR = 30  # plus $0.30
ESTIMATE_CAP_BPS = 8000
TRANCHE_FRACTION_BPS = 2500
STORY = "Our family is raising money to clear a hospital bill. Every shilling goes to the hospital."
REASON_OK_EN = "Automatic checks found nothing unusual."
REASON_OK_SW = "Ukaguzi wa kiotomatiki haukupata kitu cha ajabu."  # needs native review


@dataclass
class SeedSummary:
    counts: dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        return ", ".join(f"{count} {name}" for name, count in self.counts.items())


@dataclass
class _Doc:
    """What the seed needs to know about a bill file."""

    key: str
    facility: str
    invoice_number: str
    total_kes: int
    balance_kes: int
    sha256: str
    phash: str | None
    size_bytes: int
    mime_type: str
    kind: DocumentKind


class _Docs:
    """Bill files for the seed: from the synthetic dataset when present, else fabricated."""

    def __init__(self, data_dir: Path, register_names: dict[str, str]) -> None:
        self.names = register_names
        self.by_hospital: dict[str, list[DatasetDocument]] = {}
        try:
            for doc in load_documents(data_dir):
                if doc.label == "genuine":
                    self.by_hospital.setdefault(doc.register_code, []).append(doc)
        except FileNotFoundError:
            self.by_hospital = {}
        self.used: dict[str, int] = {}

    def next(self, register_code: str, key: str, kind: DocumentKind = DocumentKind.INVOICE) -> _Doc:
        index = self.used.get(register_code, 0)
        self.used[register_code] = index + 1
        files = self.by_hospital.get(register_code, [])
        if index < len(files):
            doc = files[index]
            return _Doc(
                key,
                doc.facility,
                doc.invoice_number,
                doc.total_kes,
                doc.balance_kes,
                doc.sha256,
                doc.phash,
                doc.size_bytes,
                doc.mime_type,
                kind,
            )
        digest = hashlib.sha256(f"seed-doc-{register_code}-{index}".encode()).hexdigest()
        total = 90_000 + 17_000 * index
        return _Doc(
            key,
            self.names[register_code],
            f"SEED/{index + 1:05d}",
            total,
            total,
            digest,
            None,
            100_000,
            "application/pdf",
            kind,
        )


@dataclass
class _World:
    session: Session
    now: datetime
    docs: _Docs
    rate: FxRate
    hospitals: dict[str, Hospital] = field(default_factory=dict)
    entries: dict[str, FacilityRegisterEntry] = field(default_factory=dict)
    clerks: dict[str, HospitalStaff] = field(default_factory=dict)
    supervisors: dict[str, HospitalStaff] = field(default_factory=dict)
    families: list[User] = field(default_factory=list)
    admin: User | None = None
    counter: int = 0

    def next_id(self) -> int:
        self.counter += 1
        return self.counter

    def ago(self, **delta: float) -> datetime:
        return self.now - timedelta(**delta)


# --------------------------------------------------------------------------- entry points
def is_seeded(session: Session) -> bool:
    return session.execute(select(User.id).where(User.email == ADMIN_EMAIL)).first() is not None


def seed_all(
    session: Session,
    *,
    now: datetime | None = None,
    data_dir: Path | None = None,
    payout_emails: dict[str, str] | None = None,
) -> SeedSummary:
    """Create the whole demo world in ``session``. The caller commits."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    data_dir = data_dir or DEFAULT_DATA_DIR
    register = load_register(data_dir)

    rate = FxRate(
        base="USD", quote="KES", rate=Decimal("129.00000000"), source=SEED_SOURCE, fetched_at=now
    )
    docs = _Docs(data_dir, {row.register_code: row.name for row in register})
    world = _World(session=session, now=now, docs=docs, rate=rate)
    session.add(rate)

    _create_register(world, register)
    _create_hospitals(world, payout_emails or {})
    _create_users(world)
    session.flush()

    _case_draft(world)
    _case_pending_triage(world)
    live_doc = _case_live(world)
    _case_in_review(world, live_doc)
    _case_pending_confirmation(world)
    _case_funded(world)
    _case_closed(world)
    _case_cancelled(world)
    session.flush()

    for case in session.execute(select(Case)).scalars():
        recompute_cached_totals(session, case)
    session.flush()
    return _summary(session)


def reset_database(session: Session) -> None:
    """Empty every table (keeping the migration history) and restart identities.

    ``TRUNCATE`` is allowed on the ledger even though its rows cannot be updated or deleted.
    """
    names = [table.name for table in reversed(db.metadata.sorted_tables)]
    quoted = ", ".join(f'"{name}"' for name in names)
    session.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


def _summary(session: Session) -> SeedSummary:
    def count(model: type) -> int:
        return session.execute(text(f"SELECT count(*) FROM {model.__tablename__}")).scalar_one()

    return SeedSummary(
        {
            "users": count(User),
            "facilities": count(FacilityRegisterEntry),
            "hospitals": count(Hospital),
            "cases": count(Case),
            "donations": count(Donation),
            "settlements": count(Settlement),
            "refunds": count(Refund),
            "ledger entries": count(LedgerEntry),
        }
    )


# --------------------------------------------------------------------------- people and places
def _create_register(world: _World, register: list) -> None:
    for row in register:
        entry = FacilityRegisterEntry(
            register_code=row.register_code,
            name=row.name,
            normalized_name=normalize_facility_name(row.name),
            county=row.county,
            facility_level=row.facility_level,
            ownership=row.ownership,
            official_email=row.official_email,
            official_phone=row.official_phone,
            source=row.source,
        )
        world.session.add(entry)
        world.entries[row.register_code] = entry


def _create_hospitals(world: _World, payout_emails: dict[str, str]) -> None:
    for key, (code, method) in ONBOARDED.items():
        entry = world.entries[code]
        domain = entry.official_email.split("@")[1]
        hospital = Hospital(
            register_entry=entry,
            display_name=entry.name,
            county=entry.county,
            settlement_method=method,
            paypal_payout_email=payout_emails.get(code, f"payouts@{domain}"),
        )
        world.session.add(hospital)
        world.hospitals[key] = hospital


def _create_users(world: _World) -> None:
    session = world.session
    world.admin = User(
        email=ADMIN_EMAIL,
        display_name="Demo Admin",
        role=UserRole.ADMIN,
        last_login_at=world.ago(hours=2),
    )
    session.add(world.admin)
    for key, hospital in world.hospitals.items():
        domain = hospital.register_entry.official_email.split("@")[1]
        for role, store, label in (
            (StaffRole.BILLING_CLERK, world.clerks, "clerk"),
            (StaffRole.BILLING_SUPERVISOR, world.supervisors, "supervisor"),
        ):
            user = User(
                email=f"{label}@{domain}",
                display_name=f"{hospital.display_name} {label}",
                role=UserRole.HOSPITAL_STAFF,
            )
            staff = HospitalStaff(user=user, hospital=hospital, staff_role=role)
            session.add_all([user, staff])
            store[key] = staff
    for index, email in enumerate(FAMILY_EMAILS, start=1):
        user = User(
            email=email,
            display_name=f"Demo Family {index}",
            role=UserRole.FAMILY,
            last_login_at=world.ago(hours=index),
        )
        session.add(user)
        world.families.append(user)


# --------------------------------------------------------------------------- building blocks
def _new_case(
    world: _World,
    key: str,
    *,
    owner: User,
    hospital_key: str,
    alias: str,
    status: CaseStatus,
    trust: TrustLevel,
    serial: int,
) -> Case:
    hospital = world.hospitals[hospital_key]
    case = Case(
        public_slug=SLUGS[key],
        created_by=owner,
        hospital=hospital,
        alias=alias,
        region=hospital.county,
        story=STORY,
        status=status,
        trust_level=trust,
    )
    code = hospital.register_entry.register_code.split("-")[1]
    case.set_patient_ref(f"ADM-{code}-{serial:04d}")
    world.session.add(case)
    world.session.flush()
    return case


def _add_consents(world: _World, case: Case, scopes: list[ConsentScope], when: datetime) -> None:
    for scope in scopes:
        world.session.add(
            Consent(
                case=case,
                scope=scope,
                actor=ConsentActor.SELF,
                granted_by_id=case.created_by_id,
                wording_version="v1",
                language="en",
                granted_at=when,
            )
        )


def _add_document(
    world: _World,
    case: Case,
    doc: _Doc,
    *,
    status: DocumentStatus,
    raw_kept: bool,
    uploaded: datetime,
) -> CaseDocument:
    document = CaseDocument(
        case=case,
        uploaded_by_id=case.created_by_id,
        kind=doc.kind,
        status=status,
        storage_key=None,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        page_count=1 if doc.mime_type == "application/pdf" else None,
        sha256=doc.sha256,
        phash=doc.phash,
        created_at=uploaded,
    )
    world.session.add(document)
    world.session.flush()
    if raw_kept:
        extension = ALLOWED_TYPES.get(doc.mime_type, ("bin", frozenset()))[0]
        document.storage_key = DEMO_PREFIX + document_key(case.id, document.id, extension)
    else:
        document.raw_deleted_at = uploaded + timedelta(hours=24)
    if status is DocumentStatus.TRIAGED:
        document.fingerprint = hash_invoice_fingerprint(
            doc.facility, doc.invoice_number, doc.total_kes
        )
        document.redacted_text = (
            f"{doc.facility}\nInvoice {doc.invoice_number}\n"
            f"Total KES {doc.total_kes:,}\nBalance KES {doc.balance_kes:,}\n"
            "Patient reference [PATIENT_REF]"
        )
    return document


def _add_triage(
    world: _World,
    case: Case,
    document: CaseDocument,
    doc: _Doc,
    *,
    when: datetime,
    score: int = 8,
    band: RiskBand = RiskBand.LOW,
    route: TriageRoute = TriageRoute.HOSPITAL_CONFIRMATION,
    signals: list[dict] | None = None,
    reasons_en: list[str] | None = None,
    reasons_sw: list[str] | None = None,
) -> TriageResult:
    result = TriageResult(
        case=case,
        document=document,
        extracted={
            "facility_name": doc.facility,
            "invoice_number": doc.invoice_number,
            "document_type": doc.kind.value,
            "total_kes": str(doc.total_kes),
            "currency": "KES",
            "confidence": 0.93,
        },
        signals=signals or [],
        risk_score=score,
        risk_band=band,
        route=route,
        reasons_en=reasons_en or [REASON_OK_EN],
        reasons_sw=reasons_sw or [REASON_OK_SW],
        used_llm=False,
        model_versions={"extractor": "seed", "source": SEED_SOURCE},
        duration_ms=1800,
        created_at=when,
    )
    world.session.add(result)
    return result


def _confirm(
    world: _World,
    case: Case,
    document: CaseDocument,
    hospital_key: str,
    *,
    amount_kes_minor: int,
    when: datetime,
    invoice_number: str,
    is_final: bool = False,
    deposit_kes_minor: int | None = None,
) -> Confirmation:
    """Record a confirmed bill; above the hospital's threshold a supervisor approves it too."""
    hospital = world.hospitals[hospital_key]
    clerk, supervisor = world.clerks[hospital_key], world.supervisors[hospital_key]
    masked = mask_email(hospital.official_email)

    def request(staff: HospitalStaff, purpose: str, at: datetime) -> ConfirmationRequest:
        item = ConfirmationRequest(
            case=case,
            hospital_id=hospital.id,
            requested_by=staff,
            purpose=purpose,
            destination_masked=masked,
            otp_hmac=crypto.lookup_hash(secrets.token_hex(4), context="otp"),
            expires_at=at + timedelta(minutes=10),
            status=ConfirmationRequestStatus.VERIFIED,
            verified_at=at + timedelta(minutes=2),
            created_at=at,
        )
        world.session.add(item)
        return item

    first = request(clerk, "decision", when - timedelta(minutes=5))
    needs_second = amount_kes_minor >= hospital.two_person_threshold_kes_minor
    second = (
        request(supervisor, "second_approval", when - timedelta(minutes=2))
        if needs_second
        else None
    )
    confirmation = Confirmation(
        case=case,
        hospital_id=hospital.id,
        document=document,
        confirmation_request=first,
        confirmed_by=clerk,
        second_request=second,
        second_approver=supervisor if needs_second else None,
        status=ConfirmationStatus.CONFIRMED,
        amount_kes_minor=amount_kes_minor,
        invoice_number=invoice_number,
        is_final_bill=is_final,
        deposit_kes_minor=deposit_kes_minor,
        finalized_at=when,
        created_at=when,
    )
    world.session.add(confirmation)
    world.session.flush()
    _audit(
        world,
        "confirmation.confirmed",
        "case",
        case.id,
        when,
        actor=clerk.user,
        amount_band="above_threshold" if needs_second else "below_threshold",
    )
    return confirmation


def _go_live(world: _World, case: Case, confirmation: Confirmation, when: datetime) -> None:
    case.status = CaseStatus.LIVE
    case.trust_level = TrustLevel.HOSPITAL_CONFIRMED
    case.target_kes_minor = confirmation.amount_kes_minor
    case.is_estimate = not confirmation.is_final_bill
    case.usd_goal_minor = usd_goal_minor(
        confirmation.amount_kes_minor, case.buffer_bps, world.rate.rate
    )
    case.live_at = when
    _audit(world, "case.live", "case", case.id, when, actor=None)


def _donate(
    world: _World,
    case: Case,
    gross_usd_minor: int,
    when: datetime,
    *,
    choice: ExcessChoice = ExcessChoice.REFUND,
    name: str | None = None,
    email: str | None = None,
) -> Donation:
    n = world.next_id()
    fee = apply_bps(gross_usd_minor, PAYPAL_FEE_BPS) + PAYPAL_FEE_FIXED_USD_MINOR
    net = gross_usd_minor - fee
    donation = Donation(
        case=case,
        paypal_order_id=f"SEED-ORDER-{n:04d}",
        paypal_capture_id=f"SEED-CAPTURE-{n:04d}",
        status=DonationStatus.CAPTURED,
        amount_usd_minor=gross_usd_minor,
        paypal_fee_usd_minor=fee,
        net_usd_minor=net,
        fx_rate=world.rate,
        kes_equivalent_minor=usd_minor_to_kes_minor(net, world.rate.rate),
        excess_choice=choice,
        donor_display_name=name,
        is_anonymous=name is None,
        donor_email_enc=email.encode() if email else None,
        donor_email_hmac=crypto.lookup_hash(email, context="donor_email") if email else None,
        captured_at=when,
        created_at=when,
    )
    world.session.add(donation)
    world.session.flush()
    ledger_service.append(
        world.session,
        [
            NewEntry(
                case.id,
                LedgerEntryType.DONATION_CAPTURED,
                LedgerAccount.CASE_FUNDS,
                gross_usd_minor,
                "USD",
                "donation",
                donation.id,
            ),
            NewEntry(
                case.id,
                LedgerEntryType.PAYPAL_FEE,
                LedgerAccount.CASE_FUNDS,
                -fee,
                "USD",
                "donation",
                donation.id,
                "PayPal fee",
            ),
            NewEntry(
                case.id,
                LedgerEntryType.PAYPAL_FEE,
                LedgerAccount.PAYPAL_FEES,
                fee,
                "USD",
                "donation",
                donation.id,
                "PayPal fee",
            ),
        ],
        created_at=when,
    )
    return donation


def _available(donation: Donation) -> int:
    return donation.net_usd_minor - donation.allocated_usd_minor - donation.refunded_usd_minor


def _settle(
    world: _World,
    case: Case,
    confirmation: Confirmation,
    tranche_no: int,
    *,
    amount_usd_minor: int,
    amount_kes_minor: int,
    when: datetime,
    note: str,
) -> Settlement:
    """Pay one tranche to the hospital, drawing first-in first-out on captured donations."""
    hospital = case.hospital
    n = world.next_id()
    settlement = Settlement(
        case=case,
        confirmation=confirmation,
        tranche_no=tranche_no,
        adapter=hospital.settlement_method,
        status=SettlementStatus.SUCCEEDED,
        amount_usd_minor=amount_usd_minor,
        amount_kes_minor=amount_kes_minor,
        fx_rate=world.rate,
        idempotency_key=f"SEED-SETTLE-{n:04d}",
        external_batch_id=f"SEED-BATCH-{n:04d}",
        external_item_id=f"SEED-ITEM-{n:04d}",
        requested_at=when,
        completed_at=when + timedelta(minutes=3),
        created_at=when,
    )
    world.session.add(settlement)
    world.session.flush()

    remaining = amount_usd_minor
    donations = sorted(
        (d for d in case.donations if d.status is not DonationStatus.CREATED),
        key=lambda d: d.captured_at,
    )
    for donation in donations:
        take = min(remaining, _available(donation))
        if take <= 0:
            continue
        world.session.add(
            SettlementAllocation(
                settlement=settlement, donation=donation, amount_usd_minor=take, created_at=when
            )
        )
        donation.allocated_usd_minor += take
        remaining -= take
        if remaining == 0:
            break
    if remaining:
        raise ValueError("Seed settlement is larger than the funds available")

    ledger_service.append(
        world.session,
        [
            NewEntry(
                case.id,
                LedgerEntryType.SETTLEMENT_SENT,
                LedgerAccount.CASE_FUNDS,
                -amount_usd_minor,
                "USD",
                "settlement",
                settlement.id,
                note,
            ),
            NewEntry(
                case.id,
                LedgerEntryType.SETTLEMENT_SENT,
                LedgerAccount.HOSPITAL_SETTLED,
                amount_kes_minor,
                "KES",
                "settlement",
                settlement.id,
                note,
            ),
        ],
        created_at=when + timedelta(minutes=3),
    )
    _audit(
        world, "settlement.sent", "settlement", settlement.id, when, actor=None, tranche=tranche_no
    )
    world.session.flush()
    return settlement


def _refund(
    world: _World, donation: Donation, amount_usd_minor: int, reason: RefundReason, when: datetime
) -> Refund:
    n = world.next_id()
    refund = Refund(
        donation=donation,
        amount_usd_minor=amount_usd_minor,
        reason=reason,
        status=RefundStatus.SUCCEEDED,
        paypal_refund_id=f"SEED-REFUND-{n:04d}",
        idempotency_key=f"SEED-REFUND-KEY-{n:04d}",
        requested_by=ActorType.SYSTEM,
        completed_at=when,
        created_at=when,
    )
    world.session.add(refund)
    donation.refunded_usd_minor += amount_usd_minor
    if donation.refunded_usd_minor >= donation.net_usd_minor:
        donation.status = DonationStatus.REFUNDED
    else:
        donation.status = DonationStatus.PARTIALLY_REFUNDED
    world.session.flush()
    ledger_service.append(
        world.session,
        [
            NewEntry(
                donation.case_id,
                LedgerEntryType.REFUND_ISSUED,
                LedgerAccount.CASE_FUNDS,
                -amount_usd_minor,
                "USD",
                "refund",
                refund.id,
                reason.value,
            ),
            NewEntry(
                donation.case_id,
                LedgerEntryType.REFUND_ISSUED,
                LedgerAccount.DONOR_REFUNDS,
                amount_usd_minor,
                "USD",
                "refund",
                refund.id,
                reason.value,
            ),
        ],
        created_at=when,
    )
    return refund


def _pool(world: _World, donation: Donation, amount_usd_minor: int, when: datetime) -> None:
    ledger_service.append(
        world.session,
        [
            NewEntry(
                donation.case_id,
                LedgerEntryType.POOL_TRANSFER,
                LedgerAccount.CASE_FUNDS,
                -amount_usd_minor,
                "USD",
                "donation",
                donation.id,
                "Excess to solidarity pool",
            ),
            NewEntry(
                donation.case_id,
                LedgerEntryType.POOL_TRANSFER,
                LedgerAccount.SOLIDARITY_POOL,
                amount_usd_minor,
                "USD",
                "donation",
                donation.id,
                "Excess to solidarity pool",
            ),
        ],
        created_at=when,
    )


def _audit(
    world: _World,
    action: str,
    target_type: str,
    target_id,
    when: datetime,
    *,
    actor: User | None,
    **details,
) -> None:
    world.session.add(
        AuditLog(
            actor_type=ActorType.USER if actor else ActorType.SYSTEM,
            actor_user_id=actor.id if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
            created_at=when,
        )
    )


def _gross(world: _World, target_kes_minor: int, share: float) -> int:
    """A donation of ``share`` of the USD value of the target, in whole dollars (at least $5)."""
    usd = kes_minor_to_usd_minor(target_kes_minor, world.rate.rate)
    return max(500, round(usd * share / 100) * 100)


def recompute_cached_totals(session: Session, case: Case) -> None:
    """Set the cached money columns from the donations and settlements that back them."""
    counted = {DonationStatus.CAPTURED, DonationStatus.PARTIALLY_REFUNDED, DonationStatus.REFUNDED}
    donations = [d for d in case.donations if d.status in counted]
    case.captured_net_usd_minor = sum(d.net_usd_minor for d in donations)
    case.refunded_usd_minor = sum(d.refunded_usd_minor for d in donations)
    case.pledged_kes_minor = sum(
        usd_minor_to_kes_minor(d.net_usd_minor - d.refunded_usd_minor, d.fx_rate.rate)
        for d in donations
    )
    done = [s for s in case.settlements if s.status is SettlementStatus.SUCCEEDED]
    case.settled_usd_minor = sum(s.amount_usd_minor for s in done)
    case.settled_kes_minor = sum(s.amount_kes_minor for s in done)
    case.donor_count = len(donations)


# --------------------------------------------------------------------------- the eight cases
def _case_draft(world: _World) -> None:
    case = _new_case(
        world,
        "draft",
        owner=world.families[0],
        hospital_key="kilele",
        alias="Imani",
        status=CaseStatus.DRAFT,
        trust=TrustLevel.UNVERIFIED,
        serial=1,
    )
    _add_consents(world, case, [ConsentScope.HOSPITAL_VERIFICATION], world.ago(days=1))


def _case_pending_triage(world: _World) -> None:
    case = _new_case(
        world,
        "pending_triage",
        owner=world.families[1],
        hospital_key="mtomwema",
        alias="Zuri",
        status=CaseStatus.PENDING_TRIAGE,
        trust=TrustLevel.UNVERIFIED,
        serial=2,
    )
    case.submitted_at = world.ago(minutes=20)
    _add_consents(
        world,
        case,
        [ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE],
        world.ago(minutes=25),
    )
    doc = world.docs.next("FR-MSA-0202", "pending_triage")
    _add_document(
        world,
        case,
        doc,
        status=DocumentStatus.PROCESSING,
        raw_kept=True,
        uploaded=world.ago(minutes=22),
    )
    _audit(world, "case.submitted", "case", case.id, world.ago(minutes=20), actor=case.created_by)


def _case_live(world: _World) -> _Doc:
    """Live at Kilele: a bill above the two-person threshold, six donations and one tranche."""
    case = _new_case(
        world,
        "live",
        owner=world.families[3],
        hospital_key="kilele",
        alias="Baraka",
        status=CaseStatus.PENDING_CONFIRMATION,
        trust=TrustLevel.PENDING_CONFIRMATION,
        serial=3,
    )
    started = world.ago(days=7)
    case.submitted_at = started
    _add_consents(
        world,
        case,
        [
            ConsentScope.HOSPITAL_VERIFICATION,
            ConsentScope.PUBLIC_PAGE,
            ConsentScope.RECEIPT_SHARING,
        ],
        started - timedelta(hours=1),
    )
    doc = world.docs.next("FR-NBI-0101", "live")
    # Two-person approval needs a bill of at least KES 200,000; pick a target above it.
    doc.balance_kes = doc.total_kes = max(doc.balance_kes, 240_000)
    document = _add_document(
        world, case, doc, status=DocumentStatus.TRIAGED, raw_kept=False, uploaded=started
    )
    _add_triage(world, case, document, doc, when=started + timedelta(minutes=2))
    confirmation = _confirm(
        world,
        case,
        document,
        "kilele",
        amount_kes_minor=doc.balance_kes * 100,
        when=started + timedelta(days=1),
        invoice_number=doc.invoice_number,
    )
    _go_live(world, case, confirmation, started + timedelta(days=1, minutes=1))
    world.session.flush()

    target = confirmation.amount_kes_minor
    plan = [
        (0.07, "Wanjiru K.", "donor1@example.test"),
        (0.06, None, None),
        (0.05, "Otieno", None),
        (0.06, None, "donor4@example.test"),
        (0.05, "Aisha M.", None),
        (0.02, None, None),
    ]
    donations = []
    for step, (share, name, email) in enumerate(plan):
        donations.append(
            _donate(
                world,
                case,
                _gross(world, target, share),
                started + timedelta(days=2, hours=step * 7),
                name=name,
                email=email,
            )
        )
    world.session.flush()
    first_five = donations[:5]
    usd = sum(_available(d) for d in first_five)
    _settle(
        world,
        case,
        confirmation,
        1,
        amount_usd_minor=usd,
        amount_kes_minor=usd_minor_to_kes_minor(usd, world.rate.rate),
        when=started + timedelta(days=2, hours=4 * 7 + 1),
        note="Tranche 1",
    )
    return doc


def _case_in_review(world: _World, live_doc: _Doc) -> None:
    """A second case that uploaded the same bill file as the live case: an AI flag for a human."""
    case = _new_case(
        world,
        "in_review",
        owner=world.families[2],
        hospital_key="kilele",
        alias="Tumaini",
        status=CaseStatus.IN_REVIEW,
        trust=TrustLevel.UNVERIFIED,
        serial=4,
    )
    when = world.ago(hours=6)
    case.submitted_at = when
    _add_consents(
        world,
        case,
        [ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE],
        when - timedelta(minutes=30),
    )
    copy = _Doc(
        "in_review",
        live_doc.facility,
        live_doc.invoice_number,
        live_doc.total_kes,
        live_doc.balance_kes,
        live_doc.sha256,
        live_doc.phash,
        live_doc.size_bytes,
        live_doc.mime_type,
        DocumentKind.INVOICE,
    )
    document = _add_document(
        world, case, copy, status=DocumentStatus.TRIAGED, raw_kept=True, uploaded=when
    )
    signals = [
        {
            "code": "duplicate_exact",
            "weight": 60,
            "detail": "Same file as a document on another case",
        },
        {"code": "patient_active_elsewhere", "weight": 30, "detail": "Same patient reference"},
    ]
    _add_triage(
        world,
        case,
        document,
        copy,
        when=when + timedelta(minutes=2),
        score=90,
        band=RiskBand.HIGH,
        route=TriageRoute.MANUAL_REVIEW,
        signals=signals,
        reasons_en=["This bill file matches one already used on another case."],
        reasons_sw=["Faili hii ya bili inalingana na iliyotumika kwenye kesi nyingine."],
    )
    world.session.add(
        ReviewItem(
            case=case,
            source=ReviewSource.AI_FLAG,
            reason="Possible duplicate bill file",
            payload={"signals": ["duplicate_exact", "patient_active_elsewhere"]},
            created_at=when + timedelta(minutes=2),
        )
    )


def _case_pending_confirmation(world: _World) -> None:
    case = _new_case(
        world,
        "pending_confirmation",
        owner=world.families[0],
        hospital_key="ziwabluu",
        alias="Neema",
        status=CaseStatus.PENDING_CONFIRMATION,
        trust=TrustLevel.PENDING_CONFIRMATION,
        serial=5,
    )
    when = world.ago(hours=30)
    case.submitted_at = when
    _add_consents(
        world,
        case,
        [ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE],
        when - timedelta(minutes=30),
    )
    doc = world.docs.next("FR-KSM-0303", "pending_confirmation")
    document = _add_document(
        world, case, doc, status=DocumentStatus.TRIAGED, raw_kept=True, uploaded=when
    )
    _add_triage(world, case, document, doc, when=when + timedelta(minutes=2))
    # The family asked for a human look; an admin resolved it with a written reason (s.35).
    world.session.add(
        ReviewItem(
            case=case,
            source=ReviewSource.FAMILY_REQUEST,
            reason="Family asked for a review",
            status=ReviewStatus.RESOLVED,
            resolution=ReviewResolution.APPROVED_FOR_CONFIRMATION,
            resolution_note="Checked the bill against the register; sent on for confirmation.",
            resolved_by_id=world.admin.id,
            resolved_at=when + timedelta(hours=3),
            created_at=when + timedelta(hours=1),
        )
    )


def _case_funded(world: _World) -> None:
    """Funded at Mto Mwema: the goal is met, and the first tranche is capped at 80 percent."""
    case = _new_case(
        world,
        "funded",
        owner=world.families[1],
        hospital_key="mtomwema",
        alias="Amani",
        status=CaseStatus.PENDING_CONFIRMATION,
        trust=TrustLevel.PENDING_CONFIRMATION,
        serial=6,
    )
    started = world.ago(days=5)
    case.submitted_at = started
    _add_consents(
        world,
        case,
        [ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE],
        started - timedelta(hours=1),
    )
    doc = world.docs.next("FR-MSA-0202", "funded")
    document = _add_document(
        world, case, doc, status=DocumentStatus.TRIAGED, raw_kept=False, uploaded=started
    )
    _add_triage(world, case, document, doc, when=started + timedelta(minutes=2))
    confirmation = _confirm(
        world,
        case,
        document,
        "mtomwema",
        amount_kes_minor=doc.balance_kes * 100,
        when=started + timedelta(hours=20),
        invoice_number=doc.invoice_number,
    )
    _go_live(world, case, confirmation, started + timedelta(hours=20, minutes=1))
    world.session.flush()

    target = confirmation.amount_kes_minor
    for step, share in enumerate([0.35, 0.30, 0.25, 0.20]):
        _donate(
            world,
            case,
            _gross(world, target, share),
            started + timedelta(days=1, hours=step * 5),
            name="Anonymous friend" if step == 0 else None,
        )
    world.session.flush()
    cap_kes = apply_bps(target, ESTIMATE_CAP_BPS)
    _settle(
        world,
        case,
        confirmation,
        1,
        amount_usd_minor=kes_minor_to_usd_minor(cap_kes, world.rate.rate),
        amount_kes_minor=cap_kes,
        when=started + timedelta(days=2),
        note="Tranche 1 (estimate cap)",
    )
    case.status = CaseStatus.FUNDED
    case.funded_at = started + timedelta(days=1, hours=16)


def _case_closed(world: _World) -> None:
    """Closed at Ziwa Bluu: estimate, lower final bill, two tranches, refunds and a pool."""
    case = _new_case(
        world,
        "closed",
        owner=world.families[2],
        hospital_key="ziwabluu",
        alias="Tabitha",
        status=CaseStatus.PENDING_CONFIRMATION,
        trust=TrustLevel.PENDING_CONFIRMATION,
        serial=7,
    )
    started = world.ago(days=20)
    case.submitted_at = started
    _add_consents(
        world,
        case,
        [ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE],
        started - timedelta(hours=1),
    )
    estimate = world.docs.next("FR-KSM-0303", "closed_estimate", DocumentKind.ESTIMATE)
    final = world.docs.next("FR-KSM-0303", "closed_final", DocumentKind.FINAL_BILL)
    estimate_doc = _add_document(
        world, case, estimate, status=DocumentStatus.TRIAGED, raw_kept=False, uploaded=started
    )
    _add_triage(world, case, estimate_doc, estimate, when=started + timedelta(minutes=2))
    estimate_conf = _confirm(
        world,
        case,
        estimate_doc,
        "ziwabluu",
        amount_kes_minor=estimate.balance_kes * 100,
        when=started + timedelta(hours=18),
        invoice_number=estimate.invoice_number,
    )
    _go_live(world, case, estimate_conf, started + timedelta(hours=18, minutes=1))
    world.session.flush()

    # The final bill is 85 percent of the estimate, rounded to KES 1,000.
    final_kes_minor = round(estimate.balance_kes * 0.85 / 1000) * 1000 * 100
    final.balance_kes = final.total_kes = final_kes_minor // 100
    shares = [
        (0.55, ExcessChoice.REFUND),
        (0.30, ExcessChoice.FOLLOW_UP_CARE),
        (0.25, ExcessChoice.REFUND),
        (0.15, ExcessChoice.SOLIDARITY_POOL),
    ]
    donations = [
        _donate(
            world,
            case,
            _gross(world, final_kes_minor, share),
            started + timedelta(days=1 + step),
            choice=choice,
            name="Grace W." if step == 1 else None,
        )
        for step, (share, choice) in enumerate(shares)
    ]
    world.session.flush()

    final_doc = _add_document(
        world,
        case,
        final,
        status=DocumentStatus.TRIAGED,
        raw_kept=False,
        uploaded=started + timedelta(days=6),
    )
    _add_triage(world, case, final_doc, final, when=started + timedelta(days=6, minutes=2))
    final_conf = _confirm(
        world,
        case,
        final_doc,
        "ziwabluu",
        amount_kes_minor=final_kes_minor,
        when=started + timedelta(days=7),
        invoice_number=final.invoice_number,
        is_final=True,
    )
    case.target_kes_minor, case.is_estimate = final_kes_minor, False
    case.usd_goal_minor = usd_goal_minor(final_kes_minor, case.buffer_bps, world.rate.rate)

    first_half = final_kes_minor // 2
    for number, kes in ((1, first_half), (2, final_kes_minor - first_half)):
        _settle(
            world,
            case,
            final_conf,
            number,
            amount_usd_minor=kes_minor_to_usd_minor(kes, world.rate.rate),
            amount_kes_minor=kes,
            when=started + timedelta(days=7, hours=number),
            note=f"Tranche {number}",
        )

    closed_at = started + timedelta(days=9)
    for donation, outcome in zip(donations, ("refund", None, "refund", "pool"), strict=True):
        leftover = _available(donation)
        if outcome is None or leftover <= 0:
            continue
        if outcome == "refund":
            _refund(world, donation, leftover, RefundReason.EXCESS_POLICY, closed_at)
        else:
            _pool(world, donation, leftover, closed_at)
    case.status, case.close_reason = CaseStatus.CLOSED, CloseReason.FINAL_BILL_LOWER
    case.closed_at = closed_at
    _audit(world, "case.closed", "case", case.id, closed_at, actor=None, reason="final_bill_lower")


def _case_cancelled(world: _World) -> None:
    """Cancelled after an admin froze it: every donation was refunded."""
    case = _new_case(
        world,
        "cancelled",
        owner=world.families[3],
        hospital_key="mtomwema",
        alias="Juma",
        status=CaseStatus.PENDING_CONFIRMATION,
        trust=TrustLevel.PENDING_CONFIRMATION,
        serial=8,
    )
    started = world.ago(days=12)
    case.submitted_at = started
    _add_consents(
        world,
        case,
        [ConsentScope.HOSPITAL_VERIFICATION, ConsentScope.PUBLIC_PAGE],
        started - timedelta(hours=1),
    )
    doc = world.docs.next("FR-MSA-0202", "cancelled")
    document = _add_document(
        world, case, doc, status=DocumentStatus.TRIAGED, raw_kept=False, uploaded=started
    )
    _add_triage(world, case, document, doc, when=started + timedelta(minutes=2))
    confirmation = _confirm(
        world,
        case,
        document,
        "mtomwema",
        amount_kes_minor=doc.balance_kes * 100,
        when=started + timedelta(hours=20),
        invoice_number=doc.invoice_number,
    )
    _go_live(world, case, confirmation, started + timedelta(hours=20, minutes=1))
    world.session.flush()

    donations = [
        _donate(
            world,
            case,
            _gross(world, confirmation.amount_kes_minor, share),
            started + timedelta(days=1, hours=step * 4),
        )
        for step, share in enumerate([0.08, 0.05])
    ]
    frozen_at = started + timedelta(days=3)
    case.status, case.frozen_at, case.trust_level = (
        CaseStatus.FROZEN,
        frozen_at,
        TrustLevel.UNVERIFIED,
    )
    case.freeze_reason = "Frozen for review: the bill could not be confirmed with the hospital."
    _audit(
        world,
        "case.frozen",
        "case",
        case.id,
        frozen_at,
        actor=world.admin,
        reason_code="unconfirmed",
    )
    for donation in donations:
        _refund(
            world,
            donation,
            _available(donation),
            RefundReason.FREEZE,
            frozen_at + timedelta(minutes=5),
        )
    case.status, case.close_reason = CaseStatus.CANCELLED, CloseReason.FRAUD
    case.closed_at = frozen_at + timedelta(hours=1)
    _audit(world, "case.cancelled", "case", case.id, case.closed_at, actor=None, reason="fraud")
