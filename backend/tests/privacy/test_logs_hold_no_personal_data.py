"""Nothing that identifies a person or opens an account ever reaches the logs.

One journey is run through the real routes with plant words in every place a person's details can
go: a family signs in, names a case, uploads a bill with a patient's name and phone number on it,
the hospital asks for a code and confirms, a donor gives under a name and reads the receipt. Every
log line written meanwhile is then searched for each plant word, and for every code and token.
"""

import io
import json
import logging

import pytest

from app.logging import make_handler
from app.models.enums import CaseStatus
from app.services import mail_service
from tests.ai.helpers import text_pdf
from tests.browser import Browser
from tests.hospital_helpers import CLERK_EMAIL, REGISTER_EMAIL, last_code_sent_to, make_ward

FAMILY_EMAIL = "canary.mother@example.test"
ALIAS = "Canary Alias Zuri"
PATIENT_REFERENCE = "ADM-CANARY-7731"
PATIENT_NAME = "Joseph Canarius Otieno"
PATIENT_PHONE = "0700 123 456"
DONOR_NAME = "Donor Canary Njeri"
STORY = "Our canary story about a hospital bill"

BILL_LINES = [
    "Kilele Mission Hospital",
    "INVOICE No KMH/26/CANARY1",
    f"Patient: {PATIENT_NAME}",
    f"Patient No: {PATIENT_REFERENCE}",
    f"Phone: {PATIENT_PHONE}",
    "Date 20/10/2026",
    "Bed charges 5 x 4,000 = 20,000",
    "Theatre 1 x 30,000 = 30,000",
    "TOTAL KES 50,000",
]


@pytest.fixture
def emitted(app):  # type: ignore[no-untyped-def]
    """Everything the application logged, as the production handler writes it."""
    stream = io.StringIO()
    handler = make_handler(stream)
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)  # what production writes; DEBUG would only add the test factories
    yield lambda: stream.getvalue()
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.mark.usefixtures("s3")
def test_a_whole_journey_leaves_no_personal_data_in_the_logs(  # type: ignore[no-untyped-def]
    browser_for, browser: Browser, db_session, paypal, emitted
) -> None:
    ward = make_ward(alias=ALIAS, public_consent=True)
    case = ward.case
    case.created_by.email = FAMILY_EMAIL
    case.status = CaseStatus.DRAFT
    case.set_patient_ref(PATIENT_REFERENCE)
    case.story = STORY
    db_session.delete(ward.document)  # the family uploads the real one below
    db_session.commit()
    secrets: set[str] = {
        FAMILY_EMAIL,
        CLERK_EMAIL,
        REGISTER_EMAIL,
        ALIAS,
        PATIENT_REFERENCE,
        PATIENT_NAME,
        "Canarius",
        PATIENT_PHONE,
        PATIENT_PHONE.replace(" ", ""),
        DONOR_NAME,
        STORY,
    }

    # --- the family signs in with an emailed code, uploads the bill and sends it ----------
    family = Browser(browser.client, remote_addr="203.0.113.5")
    mail_service.outbox.clear()
    family.post("/api/auth/request-code", {"email": FAMILY_EMAIL})
    sign_in_code = last_code_sent_to(FAMILY_EMAIL)
    secrets.add(sign_in_code)
    assert (
        family.post("/api/auth/verify", {"email": FAMILY_EMAIL, "code": sign_in_code}).status_code
        == 200
    )
    family.refresh_csrf()
    secrets.add(family.token)
    upload = family.post(
        f"/api/cases/{case.id}/documents",
        data={"kind": "invoice", "file": (io.BytesIO(text_pdf(BILL_LINES)), "bill.pdf")},
    )
    assert upload.status_code == 201, upload.get_data(as_text=True)
    assert family.post(f"/api/cases/{case.id}/submit").status_code in (200, 202)
    db_session.expire_all()

    # --- the hospital asks for a code and confirms -------------------------------------
    clerk = Browser(browser.client, remote_addr="203.0.113.6")
    mail_service.outbox.clear()
    clerk.post("/api/auth/request-code", {"email": CLERK_EMAIL})
    clerk_code = last_code_sent_to(CLERK_EMAIL)
    clerk.post("/api/auth/verify", {"email": CLERK_EMAIL, "code": clerk_code})
    clerk.refresh_csrf()
    mail_service.outbox.clear()
    sent = clerk.post(f"/api/hospital/cases/{case.id}/otp", {"purpose": "decision"})
    assert sent.status_code == 201, sent.get_data(as_text=True)
    decision_code = last_code_sent_to(REGISTER_EMAIL)
    secrets |= {clerk_code, decision_code}
    decided = clerk.post(
        f"/api/hospital/cases/{case.id}/decision",
        {
            "request_id": sent.get_json()["request_id"],
            "otp": decision_code,
            "decision": "confirmed",
            "amount_kes": "50000.00",
            "invoice_number": "KMH/26/CANARY1",
        },
    )
    assert decided.status_code == 200, decided.get_data(as_text=True)

    # --- a donor gives under a name and reads the receipt -----------------------------
    db_session.expire_all()
    slug = case.public_slug
    donor = Browser(browser.client, remote_addr="203.0.113.7")
    order = donor.post(
        "/api/donations/orders",
        {
            "case_slug": slug,
            "amount_usd": "40",
            "donor_display_name": DONOR_NAME,
            "is_anonymous": False,
        },
    )
    assert order.status_code == 201, order.get_data(as_text=True)
    captured = donor.post(f"/api/donations/orders/{order.get_json()['order_id']}/capture")
    assert captured.status_code == 200, captured.get_data(as_text=True)
    token = captured.get_json()["receipt_token"]
    secrets.add(token)
    assert donor.get(f"/api/donations/receipts/{token}").status_code == 200

    log = emitted()
    assert log, "the journey logged nothing at all, so this test would prove nothing"
    json.loads(log.splitlines()[0])  # it is the structured log
    leaked = sorted(secret for secret in secrets if secret and secret.lower() in log.lower())
    assert leaked == [], f"found in the logs: {leaked}"


def test_the_scan_would_notice_a_leak(emitted) -> None:  # type: ignore[no-untyped-def]
    """The instrument works: a line that names the alias is found by the same search."""
    logging.getLogger("app.test").info("opened the case of %s", ALIAS)
    assert ALIAS.lower() in emitted().lower()
