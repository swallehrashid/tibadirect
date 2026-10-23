"""POST /api/public/checker/appeal."""

import io

from sqlalchemy import select

from app.ai import appeals, llm
from app.models.enums import AppealInputKind, CaseStatus, ConsentScope
from app.models.platform import AppealCheck
from tests import pdf_fixtures as fx
from tests.factories import CaseFactory, ConsentFactory

URL = "/api/public/checker/appeal"
SCAM = (
    "URGENT!! My mother is dying, surgery tonight. Send any amount to my M-Pesa 0712 345 678 "
    "now, do not call the hospital, there is no time to ask questions."
)
PNG = fx.png()


def test_a_pasted_scam_is_flagged_with_reasons_in_both_languages(client, db_session) -> None:  # type: ignore[no-untyped-def]
    response = client.post(URL, json={"text": SCAM})

    assert response.status_code == 200
    body = response.get_json()
    assert body["flagged"] is True
    assert body["score"] >= appeals.FLAG_FROM
    assert body["reasons_en"]
    assert len(body["reasons_en"]) == len(body["reasons_sw"])
    assert body["safety_tip_en"]
    assert body["safety_tip_sw"]
    assert body["matched_case"] is None
    assert body["used_llm"] is False
    assert body["explanation"] is None


def test_only_a_hash_of_the_message_is_kept(client, db_session) -> None:  # type: ignore[no-untyped-def]
    client.post(URL, json={"text": SCAM})

    rows = db_session.scalars(select(AppealCheck)).all()
    assert len(rows) == 1
    assert rows[0].input_kind == AppealInputKind.TEXT
    assert "0712" not in repr(rows[0].__dict__)


def test_a_message_that_points_at_a_live_case_says_so(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)

    response = client.post(
        URL, json={"text": f"Please help, my son is unwell. Give here /c/{case.public_slug} thanks"}
    )

    matched = response.get_json()["matched_case"]
    assert matched["slug"] == case.public_slug
    assert matched["url"].endswith(f"/c/{case.public_slug}")
    assert matched["hospital_name"] == case.hospital.display_name
    row = db_session.scalars(select(AppealCheck)).one()
    assert row.matched_case_id == case.id


def test_text_that_is_missing_or_too_short_is_refused(client, db_session) -> None:  # type: ignore[no-untyped-def]
    for payload in ({}, {"text": "help"}, {"text": "x" * 5001}, [1, 2]):
        response = client.post(URL, json=payload)

        assert response.status_code == 422, payload
        assert response.get_json()["error"]["code"] == "validation_error"
    assert db_session.scalars(select(AppealCheck)).first() is None


def test_a_screenshot_is_read_and_scored(client, db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(appeals, "read_image", lambda data, mime: SCAM)

    response = client.post(
        URL, data={"image": (io.BytesIO(PNG), "forward.png")}, content_type="multipart/form-data"
    )

    assert response.status_code == 200
    assert response.get_json()["flagged"] is True
    assert db_session.scalars(select(AppealCheck)).one().input_kind == AppealInputKind.IMAGE


def test_a_picture_with_no_readable_text_is_refused(client, db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(appeals, "read_image", lambda data, mime: "  ")

    response = client.post(
        URL, data={"image": (io.BytesIO(PNG), "blank.png")}, content_type="multipart/form-data"
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "image_unreadable"


def test_something_that_is_not_a_picture_is_refused(client, db_session) -> None:  # type: ignore[no-untyped-def]
    response = client.post(
        URL,
        data={"image": (io.BytesIO(b"%PDF-1.4\n%%EOF\n"), "forward.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert db_session.scalars(select(AppealCheck)).first() is None


def test_the_language_model_only_sees_redacted_text_and_only_when_switched_on(  # type: ignore[no-untyped-def]
    client, db_session, app, monkeypatch
) -> None:
    seen: list[str] = []

    def explain(text: str, reasons: list[str]) -> str:
        seen.append(text)
        return "It asks for money to a personal number."

    monkeypatch.setattr(llm, "explain_appeal", explain)

    off = client.post(URL, json={"text": SCAM}).get_json()
    assert off["used_llm"] is False
    assert seen == []

    monkeypatch.setitem(app.config, "APPEAL_CHECKER_USE_LLM", True)
    on = client.post(URL, json={"text": SCAM}).get_json()

    assert on["used_llm"] is True
    assert on["explanation"] == "It asks for money to a personal number."
    assert "0712 345 678" not in seen[0]
    assert (
        db_session.scalars(select(AppealCheck).order_by(AppealCheck.created_at)).all()[-1].used_llm
    )


def test_when_the_model_gives_nothing_the_answer_still_comes(
    client, db_session, app, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(llm, "explain_appeal", lambda text, reasons: None)
    monkeypatch.setitem(app.config, "APPEAL_CHECKER_USE_LLM", True)

    body = client.post(URL, json={"text": SCAM}).get_json()

    assert body["used_llm"] is False
    assert body["explanation"] is None
    assert body["flagged"] is True


def test_it_can_be_switched_off(client, app, db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(app.config, "APPEAL_CHECKER_ENABLED", False)

    response = client.post(URL, json={"text": SCAM})

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "feature_disabled"


def test_twenty_checks_an_hour_is_the_limit(client, db_session) -> None:  # type: ignore[no-untyped-def]
    for _ in range(20):
        assert client.post(URL, json={"text": SCAM}).status_code == 200

    assert client.post(URL, json={"text": SCAM}).status_code == 429
