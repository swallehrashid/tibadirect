"""One account must never reach another account's records by guessing or reusing an id.

The route table in ``test_route_inventory`` guarantees every route has a gate; this file walks every
route that takes the id of a case, a document, a confirmation or a receipt, and calls it as someone
who is signed in, has the right role, but does not own the thing. The answer is always "not found",
the same words as for an id that never existed, and nothing of the owner's shows in it.
"""

import io
import re
import uuid
from collections.abc import Callable
from typing import Any

import pytest

from app.models.enums import CaseStatus, ConsentScope, UserRole
from tests.browser import Browser
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    ConfirmationFactory,
    ConsentFactory,
    DonationFactory,
    UserFactory,
)
from tests.hospital_helpers import make_ward
from tests.security.test_route_inventory import FAMILY, STAFF, gated

BrowserFor = Callable[..., Browser]

SECRET_ALIAS = "Secret Alias Zawadi"
SECRET_STORY = "A story only the family and the hospital may read"


def _fill(rule: str, ids: dict[str, str]) -> str:
    """The rule with the owner's real ids in its placeholders."""
    return re.sub(r"<(?:\w+:)?(\w+)>", lambda m: ids.get(m.group(1), "x" * 12), rule)


def _send(browser: Browser, method: str, url: str) -> Any:
    """A request with a body that would be valid, so a 404 can only come from the lookup."""
    if method in ("GET", "DELETE"):
        return getattr(browser, method.lower())(url)
    bodies = {
        "/consents": {
            "scope": "public_page",
            "actor": "self",
            "wording_version": "1",
            "language": "en",
        },
        "/cancel": {"reason": "other"},
        "/decision": {
            "request_id": str(uuid.uuid4()),
            "otp": "123456",
            "decision": "confirmed",
            "amount_kes": 85000,
            "invoice_number": "INV-1",
        },
        "/otp": {"purpose": "decision"},
        "/review-request": {"message": "Please look at my case again."},
        "/approve": {"request_id": str(uuid.uuid4()), "otp": "123456"},
    }
    body = next((value for key, value in bodies.items() if url.rstrip("/").endswith(key)), {})
    if url.endswith("/documents"):
        return browser.post(
            url, data={"kind": "invoice", "file": (io.BytesIO(b"%PDF-1.4\n%%EOF"), "b.pdf")}
        )
    if url.endswith("/final-bill"):
        return browser.post(url, data={"file": (io.BytesIO(b"%PDF-1.4\n%%EOF"), "b.pdf")})
    return getattr(browser, method.lower())(url, body)


@pytest.fixture
def world(db_session):  # type: ignore[no-untyped-def]
    """A family's case waiting at its hospital, with a bill, consents and a confirmation, plus a
    second family and a second hospital with its own staff."""
    ward = make_ward(alias=SECRET_ALIAS, story=SECRET_STORY)
    case = ward.case
    document = ward.document
    ConsentFactory(case=case, scope=ConsentScope.AI_PROCESSING_ABROAD)
    confirmation = ConfirmationFactory(
        case=case, document=CaseDocumentFactory(case=case), status="awaiting_second_approval"
    )
    donation = DonationFactory(case=case, status="captured", net_usd_minor=2500)
    other_ward = make_ward(other=True, alias="Another family")
    stranger_family = UserFactory(role=UserRole.FAMILY)
    db_session.commit()
    return {
        "ward": ward,
        "case": case,
        "document": document,
        "confirmation": confirmation,
        "donation": donation,
        "other_ward": other_ward,
        "stranger_family": stranger_family,
        "ids": {
            "case_id": str(case.id),
            "document_id": str(document.id),
            "confirmation_id": str(confirmation.id),
            "scope": "public_page",
        },
    }


def _assert_nothing_leaked(response: Any) -> None:
    text = response.get_data(as_text=True)
    for secret in (SECRET_ALIAS, SECRET_STORY, "mama@example.test", "ADM-"):
        assert secret not in text, secret


# --- Control: the right people do get in, so the refusals below mean something ----------------


def test_the_owner_and_the_hospital_can_read_the_case(browser_for: BrowserFor, world) -> None:  # type: ignore[no-untyped-def]
    ids = world["ids"]
    assert (
        browser_for(world["case"].created_by).get(f"/api/cases/{ids['case_id']}").status_code == 200
    )
    clerk = browser_for(world["ward"].clerk.user)
    assert clerk.get(f"/api/hospital/cases/{ids['case_id']}").status_code == 200


# --- A family sees only its own cases -----------------------------------------------------------


@pytest.mark.parametrize(("method", "rule"), [r for r in gated(FAMILY) if "<uuid:case_id>" in r[1]])
def test_another_family_cannot_touch_a_case_by_its_id(
    browser_for: BrowserFor, world, method: str, rule: str
) -> None:  # type: ignore[no-untyped-def]
    stranger = browser_for(world["stranger_family"])
    response = _send(stranger, method, _fill(rule, world["ids"]))
    assert response.status_code == 404, (method, rule, response.get_data(as_text=True)[:200])
    assert response.get_json()["error"]["code"] in {"case_not_found", "document_not_found"}
    _assert_nothing_leaked(response)


def test_another_familys_list_does_not_include_the_case(browser_for: BrowserFor, world) -> None:  # type: ignore[no-untyped-def]
    response = browser_for(world["stranger_family"]).get("/api/cases")
    assert response.status_code == 200
    assert response.get_json()["items"] == []
    _assert_nothing_leaked(response)


def test_a_document_id_does_not_work_under_the_wrong_case(
    browser_for: BrowserFor, world, db_session
) -> None:  # type: ignore[no-untyped-def]
    """The owner's own document id, asked for under the owner's *other* case, is not found."""
    owner = world["case"].created_by
    second = CaseFactory(created_by=owner, status=CaseStatus.DRAFT)
    db_session.commit()
    response = browser_for(owner).delete(f"/api/cases/{second.id}/documents/{world['document'].id}")
    assert response.status_code == 404


# --- A hospital sees only its own bills ---------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "rule"),
    [r for r in gated(STAFF) if "<uuid:case_id>" in r[1] or "<uuid:confirmation_id>" in r[1]],
)
def test_another_hospitals_staff_cannot_reach_a_bill_by_its_id(
    browser_for: BrowserFor, world, method: str, rule: str
) -> None:  # type: ignore[no-untyped-def]
    other_clerk = browser_for(world["other_ward"].clerk.user)
    response = _send(other_clerk, method, _fill(rule, world["ids"]))
    assert response.status_code == 404, (method, rule, response.get_data(as_text=True)[:200])
    _assert_nothing_leaked(response)


def test_another_hospitals_queue_and_payments_are_its_own(browser_for: BrowserFor, world) -> None:  # type: ignore[no-untyped-def]
    other = browser_for(world["other_ward"].clerk.user)
    queue = other.get("/api/hospital/queue")
    assert queue.status_code == 200
    assert SECRET_ALIAS not in queue.get_data(as_text=True)
    assert [c["alias"] for c in queue.get_json()["pending"]] == ["Another family"]
    mine = browser_for(world["ward"].clerk.user).get("/api/hospital/queue")
    assert SECRET_ALIAS in mine.get_data(as_text=True)
    assert "Another family" not in mine.get_data(as_text=True)


def test_a_one_time_code_is_for_the_hospital_and_case_it_was_sent_for(
    browser_for: BrowserFor, world
) -> None:  # type: ignore[no-untyped-def]
    """A code requested by one hospital's staff for a case is useless to another hospital."""
    fresh = CaseFactory(hospital=world["ward"].hospital, status=CaseStatus.PENDING_CONFIRMATION)
    CaseDocumentFactory(case=fresh)
    ConsentFactory(case=fresh, scope=ConsentScope.HOSPITAL_VERIFICATION)
    clerk = browser_for(world["ward"].clerk.user)
    sent = clerk.post(f"/api/hospital/cases/{fresh.id}/otp", {"purpose": "decision"})
    assert sent.status_code == 201
    other = browser_for(world["other_ward"].clerk.user)
    response = other.post(
        f"/api/hospital/cases/{fresh.id}/decision",
        {
            "request_id": sent.get_json()["request_id"],
            "otp": "000000",
            "decision": "confirmed",
            "amount_kes": 85000,
            "invoice_number": "INV-9",
        },
    )
    assert response.status_code == 404


# --- Receipts and public data ---------------------------------------------------------------------


def test_a_receipt_is_found_only_by_its_unguessable_token(client, world) -> None:  # type: ignore[no-untyped-def]
    donation = world["donation"]
    assert len(donation.receipt_token) >= 32
    assert client.get(f"/api/donations/receipts/{donation.receipt_token}").status_code == 200
    for guess in (str(donation.id), donation.receipt_token[:-1], "a" * 32, "1"):
        assert client.get(f"/api/donations/receipts/{guess}").status_code == 404


def test_the_public_case_by_slug_never_exposes_a_private_case(client, world) -> None:  # type: ignore[no-untyped-def]
    """A case not yet live answers exactly like a slug that does not exist."""
    case = world["case"]
    absent = client.get("/api/public/cases/doesnotexist1")
    present = client.get(f"/api/public/cases/{case.public_slug}")
    assert present.status_code == absent.status_code == 404
    assert present.get_json()["error"]["code"] == absent.get_json()["error"]["code"]
    assert present.get_json()["error"]["message"] == absent.get_json()["error"]["message"]
    _assert_nothing_leaked(present)
