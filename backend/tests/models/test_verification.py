from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.enums import (
    ConfirmationRequestStatus,
    ConfirmationStatus,
    OtpChannel,
)
from app.models.verification import Confirmation
from tests.factories import (
    ConfirmationFactory,
    ConfirmationRequestFactory,
    HospitalStaffFactory,
)
from tests.models.helpers import assert_db_rejects


def test_request_defaults(db_session) -> None:
    request = ConfirmationRequestFactory()
    db_session.refresh(request)
    assert request.channel is OtpChannel.EMAIL
    assert request.status is ConfirmationRequestStatus.SENT
    assert request.attempts == 0
    assert request.verified_at is None
    assert request.expires_at > datetime.now(UTC)


def test_request_enums_are_lowercase_native(db_session) -> None:
    ConfirmationRequestFactory(status=ConfirmationRequestStatus.LOCKED)
    row = db_session.execute(text("SELECT channel::text, status::text FROM confirmation_requests"))
    assert tuple(row.one()) == ("email", "locked")


@pytest.mark.parametrize("purpose", ["decision", "second_approval"])
def test_valid_purposes_are_accepted(db_session, purpose: str) -> None:
    assert ConfirmationRequestFactory(purpose=purpose).purpose == purpose


def test_unknown_purpose_is_rejected(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: ConfirmationRequestFactory(purpose="other"))
    assert "ck_confirmation_requests_purpose_valid" in str(error)


def test_otp_hash_is_fixed_width(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: ConfirmationRequestFactory(otp_hmac="a" * 65))
    assert isinstance(error, DataError)


def test_request_belongs_to_a_case_and_to_the_requesting_staff_member(db_session) -> None:
    request = ConfirmationRequestFactory()
    db_session.flush()
    assert request in request.case.confirmation_requests
    assert request in request.requested_by.confirmation_requests
    assert request.requested_by.hospital_id == request.hospital_id


def test_request_repr_hides_the_code_hash_and_destination(db_session) -> None:
    request = ConfirmationRequestFactory(otp_hmac="c" * 64, destination_masked="bil***@h.test")
    assert "cccc" not in repr(request)
    assert "bil" not in repr(request)


def test_confirmation_defaults(db_session) -> None:
    confirmation = ConfirmationFactory()
    db_session.refresh(confirmation)
    assert confirmation.status is ConfirmationStatus.CONFIRMED
    assert confirmation.is_final_bill is False
    assert confirmation.deposit_kes_minor is None
    assert confirmation.second_approver_staff_id is None
    assert confirmation.second_request_id is None
    assert confirmation.paypal_invoice_id is None
    assert confirmation.updated_at is not None


def test_confirmation_links_to_everything_it_references(db_session) -> None:
    confirmation = ConfirmationFactory()
    db_session.flush()
    assert confirmation in confirmation.case.confirmations
    assert confirmation in confirmation.document.confirmations
    assert confirmation.confirmation_request.confirmation is confirmation
    assert confirmation in confirmation.confirmed_by.confirmations
    assert confirmation.document.case is confirmation.case


def test_a_second_approver_must_be_a_different_person(db_session) -> None:
    confirmation = ConfirmationFactory(status=ConfirmationStatus.AWAITING_SECOND_APPROVAL)
    db_session.flush()
    second_request = ConfirmationRequestFactory(case=confirmation.case, purpose="second_approval")
    db_session.flush()
    confirmation.second_approver_staff_id = confirmation.confirmed_by_staff_id
    confirmation.second_request_id = second_request.id
    error = assert_db_rejects(db_session, lambda: None)
    assert "ck_confirmations_second_approver_differs" in str(error)


def test_a_different_supervisor_can_give_the_second_approval(db_session) -> None:
    confirmation = ConfirmationFactory(status=ConfirmationStatus.AWAITING_SECOND_APPROVAL)
    supervisor = HospitalStaffFactory(hospital=confirmation.case.hospital)
    second_request = ConfirmationRequestFactory(case=confirmation.case, purpose="second_approval")
    db_session.flush()
    confirmation.second_approver_staff_id = supervisor.id
    confirmation.second_request_id = second_request.id
    confirmation.status = ConfirmationStatus.CONFIRMED
    db_session.flush()
    db_session.refresh(confirmation)
    assert confirmation.second_approver is supervisor
    assert confirmation.second_request.second_approval_for is confirmation
    assert confirmation in supervisor.second_approvals


def test_one_code_authorises_at_most_one_confirmation(db_session) -> None:
    confirmation = ConfirmationFactory()
    db_session.flush()
    clone = Confirmation(
        case_id=confirmation.case_id,
        hospital_id=confirmation.hospital_id,
        document_id=confirmation.document_id,
        confirmation_request_id=confirmation.confirmation_request_id,
        confirmed_by_staff_id=confirmation.confirmed_by_staff_id,
        status=ConfirmationStatus.CONFIRMED,
        amount_kes_minor=1,
        finalized_at=datetime.now(UTC),
    )
    error = assert_db_rejects(db_session, lambda: db_session.add(clone))
    assert "uq_confirmations_confirmation_request_id" in str(error)


def test_a_second_approval_code_cannot_be_reused(db_session) -> None:
    first = ConfirmationFactory(status=ConfirmationStatus.AWAITING_SECOND_APPROVAL)
    second = ConfirmationFactory(status=ConfirmationStatus.AWAITING_SECOND_APPROVAL)
    shared = ConfirmationRequestFactory(case=first.case, purpose="second_approval")
    approver_one = HospitalStaffFactory(hospital=first.case.hospital)
    approver_two = HospitalStaffFactory(hospital=second.case.hospital)
    db_session.flush()
    first.second_request_id = shared.id
    first.second_approver_staff_id = approver_one.id
    db_session.flush()
    second.second_request_id = shared.id
    second.second_approver_staff_id = approver_two.id
    error = assert_db_rejects(db_session, lambda: None)
    assert "uq_confirmations_second_request_id" in str(error)


@pytest.mark.parametrize("status", [ConfirmationStatus.QUERIED, ConfirmationStatus.REJECTED])
def test_queries_and_rejections_need_no_amount(db_session, status) -> None:
    confirmation = ConfirmationFactory(
        status=status, amount_kes_minor=None, finalized_at=None, query_note="Totals look wrong"
    )
    assert confirmation.amount_kes_minor is None


@pytest.mark.parametrize(
    "status", [ConfirmationStatus.CONFIRMED, ConfirmationStatus.AWAITING_SECOND_APPROVAL]
)
def test_confirmations_awaiting_or_confirmed_need_an_amount(db_session, status) -> None:
    error = assert_db_rejects(
        db_session, lambda: ConfirmationFactory(status=status, amount_kes_minor=None)
    )
    assert "ck_confirmations_amount_required" in str(error)


@pytest.mark.parametrize("amount", [0, -100])
def test_amount_must_be_positive(db_session, amount: int) -> None:
    error = assert_db_rejects(db_session, lambda: ConfirmationFactory(amount_kes_minor=amount))
    assert "ck_confirmations_amount_positive" in str(error)


def test_deposit_cannot_be_negative_but_can_be_zero(db_session) -> None:
    assert ConfirmationFactory(deposit_kes_minor=0).deposit_kes_minor == 0
    error = assert_db_rejects(db_session, lambda: ConfirmationFactory(deposit_kes_minor=-1))
    assert "ck_confirmations_deposit_non_negative" in str(error)


def test_a_confirmed_decision_records_when_it_was_finalised(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: ConfirmationFactory(finalized_at=None))
    assert "ck_confirmations_confirmed_has_finalized_at" in str(error)


def test_a_pending_second_approval_has_no_finalised_time(db_session) -> None:
    confirmation = ConfirmationFactory(
        status=ConfirmationStatus.AWAITING_SECOND_APPROVAL, finalized_at=None
    )
    assert confirmation.finalized_at is None


def test_paypal_invoice_ids_are_unique_but_many_can_be_unset(db_session) -> None:
    ConfirmationFactory(paypal_invoice_id=None)
    ConfirmationFactory(paypal_invoice_id=None)
    ConfirmationFactory(paypal_invoice_id="INV2-AAAA")
    db_session.flush()
    error = assert_db_rejects(
        db_session, lambda: ConfirmationFactory(paypal_invoice_id="INV2-AAAA")
    )
    assert "uq_confirmations_paypal_invoice_id" in str(error)


def test_a_case_can_have_an_estimate_and_then_a_final_bill_confirmation(db_session) -> None:
    first = ConfirmationFactory(is_final_bill=False)
    final = ConfirmationFactory(case=first.case, is_final_bill=True, amount_kes_minor=7_000_000)
    db_session.flush()
    assert {c.is_final_bill for c in first.case.confirmations} == {False, True}
    assert final.document.case is first.case


def test_a_used_code_request_cannot_be_deleted(db_session) -> None:
    confirmation = ConfirmationFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session, lambda: db_session.delete(confirmation.confirmation_request)
    )
    assert isinstance(error, IntegrityError)


def test_a_confirmed_document_cannot_be_deleted(db_session) -> None:
    confirmation = ConfirmationFactory()
    db_session.flush()
    error = assert_db_rejects(
        db_session,
        lambda: db_session.execute(
            text("DELETE FROM case_documents WHERE id = :id"), {"id": confirmation.document_id}
        ),
    )
    assert "fk_confirmations_document_id_case_documents" in str(error)


def test_staff_with_decisions_cannot_be_deleted(db_session) -> None:
    confirmation = ConfirmationFactory()
    db_session.flush()
    error = assert_db_rejects(db_session, lambda: db_session.delete(confirmation.confirmed_by))
    assert isinstance(error, IntegrityError)


def test_confirmation_repr_shows_only_id_and_status(db_session) -> None:
    confirmation = ConfirmationFactory(invoice_number="INV-SECRET-9", query_note="private note")
    text_repr = repr(confirmation)
    assert "SECRET" not in text_repr
    assert "private" not in text_repr
    assert str(confirmation.id) in text_repr
