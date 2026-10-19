"""The demo world: every lifecycle stage, and the invariants from the database design hold."""

import re
import time
from collections import defaultdict
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text

from app.extensions import db
from app.models.case import Case, ReviewItem, TriageResult
from app.models.enums import (
    CaseStatus,
    ConfirmationStatus,
    ConsentScope,
    DocumentStatus,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    RefundReason,
    ReviewStatus,
    RiskBand,
    SettlementStatus,
    StaffRole,
    TriageRoute,
    TrustLevel,
    UserRole,
)
from app.models.hospital import FacilityRegisterEntry, Hospital
from app.models.identity import User
from app.models.ledger import LedgerEntry
from app.models.money import Donation, Refund, Settlement, SettlementAllocation
from app.models.platform import AuditLog
from app.models.verification import Confirmation
from app.seed import SLUGS, is_seeded, recompute_cached_totals, reset_database, seed_all
from app.services import ledger_service
from tests.test_synthetic_dataset import _lexicon

PUBLIC = {CaseStatus.LIVE, CaseStatus.FUNDED, CaseStatus.CLOSED}


@pytest.fixture
def world(db_session):
    started = time.perf_counter()
    summary = seed_all(db_session)
    db_session.flush()
    elapsed = time.perf_counter() - started
    cases = {c.public_slug: c for c in db_session.execute(select(Case)).scalars()}
    return {"cases": cases, "summary": summary, "elapsed": elapsed, "session": db_session}


def case_for(world, key: str) -> Case:
    return world["cases"][SLUGS[key]]


# ---- shape ----


def test_seeding_is_fast(world) -> None:
    assert world["elapsed"] < 60


def test_the_summary_counts(world) -> None:
    counts = world["summary"].counts
    assert counts["users"] == 11  # 1 admin, 6 billing staff, 4 families
    assert counts["facilities"] == 25
    assert counts["hospitals"] == 3
    assert counts["cases"] == 8
    assert counts["donations"] == 16
    assert counts["settlements"] == 4
    assert counts["refunds"] == 3


def test_every_lifecycle_state_is_present_exactly_once(world) -> None:
    statuses = Counter_of(c.status for c in world["cases"].values())
    assert statuses == {
        CaseStatus.DRAFT: 1,
        CaseStatus.PENDING_TRIAGE: 1,
        CaseStatus.IN_REVIEW: 1,
        CaseStatus.PENDING_CONFIRMATION: 1,
        CaseStatus.LIVE: 1,
        CaseStatus.FUNDED: 1,
        CaseStatus.CLOSED: 1,
        CaseStatus.CANCELLED: 1,
    }


def Counter_of(items) -> dict:  # noqa: N802
    counts: dict = defaultdict(int)
    for item in items:
        counts[item] += 1
    return dict(counts)


def test_public_slugs_are_fixed_and_valid(world) -> None:
    assert set(world["cases"]) == set(SLUGS.values())
    assert all(re.fullmatch(r"[a-z2-7]{12}", slug) for slug in SLUGS.values())


def test_is_seeded_turns_true(db_session) -> None:
    assert is_seeded(db_session) is False
    seed_all(db_session)
    assert is_seeded(db_session) is True


# ---- people and places ----


def test_accounts_from_the_spec_exist(world) -> None:
    session = world["session"]
    emails = {u.email: u for u in session.execute(select(User)).scalars()}
    assert emails["admin@tibadirect.test"].role is UserRole.ADMIN
    for n in range(1, 5):
        assert emails[f"family{n}@example.test"].role is UserRole.FAMILY
    assert emails["clerk@kilele-hospital.test"].role is UserRole.HOSPITAL_STAFF
    assert emails["supervisor@kilele-hospital.test"].role is UserRole.HOSPITAL_STAFF


def test_each_onboarded_hospital_has_a_clerk_and_a_supervisor(world) -> None:
    session = world["session"]
    hospitals = session.execute(select(Hospital)).scalars().all()
    assert {h.display_name for h in hospitals} == {
        "Kilele Mission Hospital",
        "Mto Mwema Hospital",
        "Ziwa Bluu General Hospital",
    }
    for hospital in hospitals:
        assert {s.staff_role for s in hospital.staff} == {
            StaffRole.BILLING_CLERK,
            StaffRole.BILLING_SUPERVISOR,
        }


def test_kilele_confirmation_codes_go_to_the_register_email(world) -> None:
    kilele = (
        world["session"]
        .execute(select(Hospital).where(Hospital.display_name == "Kilele Mission Hospital"))
        .scalar_one()
    )
    assert kilele.official_email == "billing@kilele-hospital.test"


def test_the_whole_register_is_loaded(world) -> None:
    assert world["session"].execute(select(func.count(FacilityRegisterEntry.id))).scalar_one() == 25


# ---- each case ----


def test_live_case_has_six_donations_and_one_settled_tranche(world) -> None:
    case = case_for(world, "live")
    assert case.trust_level is TrustLevel.HOSPITAL_CONFIRMED
    assert len(case.donations) == 6
    assert [s.status for s in case.settlements] == [SettlementStatus.SUCCEEDED]
    assert case.is_estimate is True
    assert 0 < case.settled_kes_minor < case.pledged_kes_minor < case.target_kes_minor


def test_funded_case_met_its_goal_and_the_first_tranche_stopped_at_the_estimate_cap(world) -> None:
    case = case_for(world, "funded")
    assert case.pledged_kes_minor >= case.target_kes_minor * (10_000 + case.buffer_bps) // 10_000
    assert case.settled_kes_minor == case.target_kes_minor * 8000 // 10_000
    assert case.funded_at is not None


def test_closed_case_settled_its_final_bill_then_refunded_and_pooled_the_excess(world) -> None:
    session, case = world["session"], case_for(world, "closed")
    assert case.is_estimate is False
    assert case.close_reason.value == "final_bill_lower"
    assert len(case.settlements) == 2
    assert case.settled_kes_minor == case.target_kes_minor
    assert [c.is_final_bill for c in sorted(case.confirmations, key=lambda c: c.created_at)] == [
        False,
        True,
    ]
    refunds = (
        session.execute(select(Refund).join(Donation).where(Donation.case_id == case.id))
        .scalars()
        .all()
    )
    assert refunds
    assert all(r.reason is RefundReason.EXCESS_POLICY for r in refunds)
    pooled = (
        session.execute(
            select(LedgerEntry).where(
                LedgerEntry.case_id == case.id,
                LedgerEntry.entry_type == LedgerEntryType.POOL_TRANSFER,
            )
        )
        .scalars()
        .all()
    )
    assert {e.account for e in pooled} == {LedgerAccount.CASE_FUNDS, LedgerAccount.SOLIDARITY_POOL}
    assert len(pooled) == 2


def test_cancelled_case_was_frozen_and_every_donation_refunded(world) -> None:
    session, case = world["session"], case_for(world, "cancelled")
    assert case.frozen_at is not None
    assert case.freeze_reason
    assert case.close_reason.value == "fraud"
    assert case.trust_level is TrustLevel.UNVERIFIED
    assert case.donations
    assert all(d.status is DonationStatus.REFUNDED for d in case.donations)
    assert case.pledged_kes_minor == 0
    reasons = (
        session.execute(select(Refund.reason).join(Donation).where(Donation.case_id == case.id))
        .scalars()
        .all()
    )
    assert set(reasons) == {RefundReason.FREEZE}


def test_in_review_case_is_a_flagged_duplicate_of_the_live_case(world) -> None:
    session, case = world["session"], case_for(world, "in_review")
    live = case_for(world, "live")
    triage = session.execute(
        select(TriageResult).where(TriageResult.case_id == case.id)
    ).scalar_one()
    assert triage.route is TriageRoute.MANUAL_REVIEW
    assert triage.risk_band is RiskBand.HIGH
    assert {s["code"] for s in triage.signals} >= {"duplicate_exact"}
    item = session.execute(select(ReviewItem).where(ReviewItem.case_id == case.id)).scalar_one()
    assert item.status is ReviewStatus.OPEN
    assert case.documents[0].sha256 == live.documents[0].sha256


def test_pending_confirmation_case_is_waiting_for_the_hospital(world) -> None:
    session, case = world["session"], case_for(world, "pending_confirmation")
    assert not case.confirmations
    triage = session.execute(
        select(TriageResult).where(TriageResult.case_id == case.id)
    ).scalar_one()
    assert triage.route is TriageRoute.HOSPITAL_CONFIRMATION
    resolved = session.execute(select(ReviewItem).where(ReviewItem.case_id == case.id)).scalar_one()
    assert resolved.status is ReviewStatus.RESOLVED
    assert resolved.resolution_note


def test_draft_and_pending_triage_cases_have_the_expected_content(world) -> None:
    draft, triage = case_for(world, "draft"), case_for(world, "pending_triage")
    assert not draft.documents
    assert [d.status for d in triage.documents] == [DocumentStatus.PROCESSING]
    assert triage.submitted_at is not None


# ---- invariants from the database design (section 6) ----


def test_public_visibility_needs_a_public_status_and_the_public_page_consent(world) -> None:
    for case in world["cases"].values():
        expected = (
            case.status in PUBLIC and case.active_consent(ConsentScope.PUBLIC_PAGE) is not None
        )
        assert case.is_publicly_visible is expected
    assert {c.status for c in world["cases"].values() if c.is_publicly_visible} == PUBLIC


def test_settlement_only_for_confirmed_cases_with_a_confirmed_bill(world) -> None:
    for case in world["cases"].values():
        if not case.settlements:
            continue
        assert any(c.status is ConfirmationStatus.CONFIRMED for c in case.confirmations)
        assert case.live_at is not None
        if case.status in {CaseStatus.LIVE, CaseStatus.FUNDED}:
            assert case.trust_level is TrustLevel.HOSPITAL_CONFIRMED


def test_estimates_never_settle_more_than_the_cap(world) -> None:
    for case in world["cases"].values():
        if case.is_estimate and case.target_kes_minor:
            assert case.settled_kes_minor <= case.target_kes_minor * 8000 // 10_000


def test_allocation_rows_add_up_to_each_donations_allocated_amount(world) -> None:
    session = world["session"]
    allocated = dict(
        session.execute(
            select(
                SettlementAllocation.donation_id, func.sum(SettlementAllocation.amount_usd_minor)
            ).group_by(SettlementAllocation.donation_id)
        ).all()
    )
    for donation in session.execute(select(Donation)).scalars():
        assert allocated.get(donation.id, 0) == donation.allocated_usd_minor
        assert donation.allocated_usd_minor + donation.refunded_usd_minor <= donation.net_usd_minor


def test_settlement_allocations_add_up_to_each_settlement(world) -> None:
    session = world["session"]
    for settlement in session.execute(select(Settlement)).scalars():
        total = sum(a.amount_usd_minor for a in settlement.allocations)
        assert total == settlement.amount_usd_minor


def test_cached_totals_equal_totals_recomputed_from_the_rows(world) -> None:
    session = world["session"]
    for case in world["cases"].values():
        before = (
            case.captured_net_usd_minor,
            case.refunded_usd_minor,
            case.pledged_kes_minor,
            case.settled_usd_minor,
            case.settled_kes_minor,
            case.donor_count,
        )
        recompute_cached_totals(session, case)
        after = (
            case.captured_net_usd_minor,
            case.refunded_usd_minor,
            case.pledged_kes_minor,
            case.settled_usd_minor,
            case.settled_kes_minor,
            case.donor_count,
        )
        assert before == after


def test_the_ledger_chain_verifies_from_genesis(world) -> None:
    status = ledger_service.verify_chain(world["session"])
    assert status.valid
    assert status.entries_checked == world["summary"].counts["ledger entries"]


def test_the_ledger_accounts_for_every_cent_of_every_case(world) -> None:
    session = world["session"]
    for case in world["cases"].values():

        def balance(account: LedgerAccount, currency: str, case: Case = case) -> int:
            return session.execute(
                select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
                    LedgerEntry.case_id == case.id,
                    LedgerEntry.account == account,
                    LedgerEntry.currency == currency,
                )
            ).scalar_one()

        pooled = balance(LedgerAccount.SOLIDARITY_POOL, "USD")
        # Money still held for the case = what donors gave, net of fees, less what left the case.
        held = sum(
            d.net_usd_minor - d.allocated_usd_minor - d.refunded_usd_minor for d in case.donations
        )
        assert balance(LedgerAccount.CASE_FUNDS, "USD") == held - pooled
        assert balance(LedgerAccount.HOSPITAL_SETTLED, "KES") == case.settled_kes_minor
        assert balance(LedgerAccount.PAYPAL_FEES, "USD") == sum(
            d.paypal_fee_usd_minor for d in case.donations
        )
        assert balance(LedgerAccount.DONOR_REFUNDS, "USD") == case.refunded_usd_minor


def test_every_money_movement_has_ledger_entries(world) -> None:
    session = world["session"]
    refs = {
        (kind, ref)
        for kind, ref in session.execute(select(LedgerEntry.ref_type, LedgerEntry.ref_id)).all()
    }
    for model, kind in ((Donation, "donation"), (Settlement, "settlement"), (Refund, "refund")):
        for row in session.execute(select(model)).scalars():
            assert (kind, row.id) in refs


def test_two_staff_members_confirm_bills_at_or_above_the_threshold(world) -> None:
    seen_with_second = seen_without = 0
    for confirmation in world["session"].execute(select(Confirmation)).scalars():
        threshold = confirmation.case.hospital.two_person_threshold_kes_minor
        if confirmation.amount_kes_minor >= threshold:
            assert confirmation.second_approver_staff_id is not None
            assert confirmation.second_approver_staff_id != confirmation.confirmed_by_staff_id
            seen_with_second += 1
        else:
            assert confirmation.second_approver_staff_id is None
            seen_without += 1
    assert seen_with_second >= 1
    assert seen_without >= 1


def test_confirmation_codes_were_sent_to_the_register_contact_only(world) -> None:
    for confirmation in world["session"].execute(select(Confirmation)).scalars():
        masked = confirmation.confirmation_request.destination_masked
        official = confirmation.case.hospital.official_email
        assert masked.endswith("@" + official.split("@")[1])
        assert official.split("@")[0] not in masked


# ---- privacy and fictional data ----


def test_no_stored_text_contains_a_medical_term_or_contact_detail(world) -> None:
    lexicon = _lexicon("medical_terms.txt")
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in lexicon) + r")\b", re.IGNORECASE)
    texts = [c.story or "" for c in world["cases"].values()]
    texts += [c.alias for c in world["cases"].values()]
    texts += [d.redacted_text or "" for c in world["cases"].values() for d in c.documents]
    texts += [r.reason for r in world["session"].execute(select(ReviewItem)).scalars()]
    for value in texts:
        assert not pattern.search(value), value
        assert not re.search(r"[\w.]+@[\w.]+", value)
        assert not re.search(r"\+254|\b0[17]\d{8}\b", value)


def test_the_patient_reference_is_encrypted_in_the_database(world) -> None:
    session = world["session"]
    raw = session.execute(text("SELECT patient_ref_enc FROM cases")).scalars().all()
    assert len(raw) == 8
    assert all(not bytes(blob).startswith(b"ADM-") and b"ADM-" not in bytes(blob) for blob in raw)
    assert all(c.patient_reference.startswith("ADM-") for c in world["cases"].values())


def test_seeded_paypal_ids_are_marked_and_never_look_real(world) -> None:
    session = world["session"]
    ids = [d.paypal_order_id for d in session.execute(select(Donation)).scalars()]
    ids += [d.paypal_capture_id for d in session.execute(select(Donation)).scalars()]
    ids += [r.paypal_refund_id for r in session.execute(select(Refund)).scalars()]
    ids += [s.external_batch_id for s in session.execute(select(Settlement)).scalars()]
    assert ids
    assert all(i.startswith("SEED-") for i in ids)


def test_documents_without_a_raw_file_record_when_it_was_deleted(world) -> None:
    for case in world["cases"].values():
        for document in case.documents:
            if document.storage_key is None:
                assert document.raw_deleted_at is not None
            else:
                assert document.raw_deleted_at is None
                assert document.storage_key.startswith("demo/cases/")


def test_donor_emails_are_encrypted_and_hashed(world) -> None:
    session = world["session"]
    donors = [d for d in session.execute(select(Donation)).scalars() if d.donor_email_enc]
    assert donors
    stored = session.execute(
        text("SELECT donor_email_enc FROM donations WHERE donor_email_enc IS NOT NULL")
    ).scalars()
    assert all(b"@example.test" not in bytes(blob) for blob in stored)
    assert all(len(d.donor_email_hmac) == 64 for d in donors)


def test_audit_entries_exist_and_carry_no_personal_data(world) -> None:
    entries = world["session"].execute(select(AuditLog)).scalars().all()
    assert len(entries) >= 8
    for entry in entries:
        assert not re.search(r"@|\+254|\b0[17]\d{8}\b", str(entry.details))


def test_timestamps_are_in_the_past(world) -> None:
    now = datetime.now(UTC)
    for case in world["cases"].values():
        for stamp in (
            case.submitted_at,
            case.live_at,
            case.funded_at,
            case.closed_at,
            case.frozen_at,
        ):
            assert stamp is None or stamp <= now
    for donation in world["session"].execute(select(Donation)).scalars():
        assert donation.captured_at <= now


# ---- reset ----


def test_reset_empties_every_table_but_the_migration_history(db_session) -> None:
    seed_all(db_session)
    db_session.flush()
    reset_database(db_session)
    for table in db.metadata.sorted_tables:
        count = db_session.execute(text(f'SELECT count(*) FROM "{table.name}"')).scalar_one()
        assert count == 0, table.name
    assert db_session.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1


def test_a_reset_then_a_reseed_restarts_the_ledger_sequence(db_session) -> None:
    seed_all(db_session)
    reset_database(db_session)
    seed_all(db_session)
    db_session.flush()
    first = db_session.execute(select(func.min(LedgerEntry.seq))).scalar_one()
    assert first == 1
    assert ledger_service.verify_chain(db_session).valid
