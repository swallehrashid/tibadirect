"""Emails to the family: the right language, and a failure never undoes anything."""

import pytest

from app.services import mail_service, notification_service
from tests.factories import CaseFactory, UserFactory


@pytest.fixture(autouse=True)
def _clean_outbox() -> None:
    mail_service.outbox.clear()


def test_the_owner_gets_the_email_in_their_language(app, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(
        alias="Baby Zawadi",
        created_by=UserFactory(email="mama@example.test", preferred_language="sw"),
    )
    with app.test_request_context():
        assert notification_service.notify_family(case, "case_submitted") is True
    [message] = mail_service.outbox
    assert (message.to, message.language) == ("mama@example.test", "sw")
    assert "Baby Zawadi" in message.text


def test_a_refusing_mail_server_is_logged_and_reported_not_raised(
    app, db_session, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def down(*args: object, **kwargs: object) -> None:
        raise mail_service.MailError("ConnectionRefusedError")

    monkeypatch.setattr(mail_service, "send", down)
    with app.test_request_context():
        assert notification_service.notify_family(CaseFactory(), "case_submitted") is False


def test_submitting_a_case_emails_the_family(browser_for, s3, redis_clean) -> None:  # type: ignore[no-untyped-def]
    import io

    from app.models.enums import ConsentScope
    from tests import pdf_fixtures as fx
    from tests.factories import ConsentFactory

    case = CaseFactory(created_by=UserFactory(email="mama@example.test"))
    ConsentFactory(case=case, scope=ConsentScope.HOSPITAL_VERIFICATION)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    browser = browser_for(case.created_by)
    browser.post(
        f"/api/cases/{case.id}/documents",
        data={"kind": "invoice", "file": (io.BytesIO(fx.clean_pdf()), "a.pdf")},
    )
    browser.post(f"/api/cases/{case.id}/submit")
    assert [m.template for m in mail_service.outbox if m.to == "mama@example.test"] == [
        "case_submitted"
    ]
