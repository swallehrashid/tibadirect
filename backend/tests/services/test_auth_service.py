"""Sign-in codes: issue, expiry, single use, lockout and account creation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import UserRole
from app.models.identity import LoginCode, User
from app.security import crypto
from app.services import auth_service, mail_service
from app.services.auth_service import InvalidCode
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _clean_outbox() -> None:
    mail_service.outbox.clear()


def _issue(db_session, email: str = "mama@example.test", **kwargs) -> str:  # type: ignore[no-untyped-def]
    issued = auth_service.request_code(db_session, email, **kwargs)
    assert issued.code is not None
    return issued.code


def test_request_stores_only_a_keyed_hash_of_the_code(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session)
    stored = db_session.execute(select(LoginCode)).scalars().one()
    assert len(code) == 6
    assert code.isdigit()
    assert code not in stored.code_hmac
    assert stored.code_hmac == crypto.lookup_hash(f"mama@example.test|{code}", context="login_code")
    assert stored.expires_at - stored.created_at <= timedelta(minutes=10, seconds=1)
    assert stored.attempts == 0
    assert stored.consumed_at is None


def test_the_code_is_emailed_once_in_the_users_language(app, db_session) -> None:  # type: ignore[no-untyped-def]
    UserFactory(email="mama@example.test", preferred_language="sw")
    code = _issue(db_session)
    [message] = mail_service.outbox
    assert message.to == "mama@example.test"
    assert message.language == "sw"
    assert code in message.text


def test_an_unknown_address_gets_a_code_in_english(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session, "New.Person@Example.test")
    [message] = mail_service.outbox
    assert message.to == "new.person@example.test"
    assert message.language == "en"
    assert code in message.text


def test_the_ip_is_kept_only_as_a_hash(app, db_session) -> None:  # type: ignore[no-untyped-def]
    _issue(db_session, ip="203.0.113.7")
    stored = db_session.execute(select(LoginCode)).scalars().one()
    assert stored.ip_hmac == crypto.hash_ip("203.0.113.7")
    assert "203.0.113.7" not in stored.ip_hmac


def test_a_switched_off_account_gets_no_code_and_no_email(app, db_session) -> None:  # type: ignore[no-untyped-def]
    UserFactory(email="gone@example.test", is_active=False)
    issued = auth_service.request_code(db_session, "gone@example.test")
    assert issued.code is None
    assert not mail_service.outbox
    assert db_session.execute(select(LoginCode)).first() is None


def test_a_mail_failure_is_swallowed_so_the_answer_stays_the_same(
    app, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    def refuse(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise mail_service.MailError("ConnectionRefusedError")

    monkeypatch.setattr(mail_service, "send", refuse)
    assert auth_service.request_code(db_session, "mama@example.test").code is not None


def test_a_new_code_cancels_the_earlier_one(app, db_session) -> None:  # type: ignore[no-untyped-def]
    _issue(db_session)
    second = _issue(db_session)
    stored = db_session.execute(select(LoginCode)).scalars().all()
    assert sorted(row.consumed_at is not None for row in stored) == [False, True]
    user, _ = auth_service.verify_code(db_session, "mama@example.test", second)
    assert user.email == "mama@example.test"


def test_first_success_creates_a_family_account(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session, "wanjiru.kamau@example.test")
    user, created = auth_service.verify_code(db_session, "wanjiru.kamau@example.test", code)
    assert created
    assert user.role == UserRole.FAMILY
    assert user.display_name == "Wanjiru Kamau"
    assert user.last_login_at is not None


def test_success_for_an_existing_account_keeps_its_role(app, db_session) -> None:  # type: ignore[no-untyped-def]
    admin = UserFactory(email="admin@example.test", role=UserRole.ADMIN)
    code = _issue(db_session, "ADMIN@example.test")
    user, created = auth_service.verify_code(db_session, "admin@example.test", code)
    assert not created
    assert user.id == admin.id
    assert user.role == UserRole.ADMIN
    assert (
        db_session.execute(select(User).where(User.email == "admin@example.test")).scalars().one()
    )


def test_the_code_works_once(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session)
    auth_service.verify_code(db_session, "mama@example.test", code)
    with pytest.raises(InvalidCode):
        auth_service.verify_code(db_session, "mama@example.test", code)


def test_whitespace_around_the_code_is_ignored(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session)
    user, _ = auth_service.verify_code(db_session, "MAMA@example.test ", f" {code} ")
    assert user.email == "mama@example.test"


def test_the_code_expires_after_ten_minutes(app, db_session) -> None:  # type: ignore[no-untyped-def]
    start = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    code = _issue(db_session, now=start)
    with pytest.raises(InvalidCode):
        auth_service.verify_code(
            db_session, "mama@example.test", code, now=start + timedelta(minutes=10)
        )


def test_the_code_works_just_before_it_expires(app, db_session) -> None:  # type: ignore[no-untyped-def]
    start = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    code = _issue(db_session, now=start)
    user, _ = auth_service.verify_code(
        db_session, "mama@example.test", code, now=start + timedelta(minutes=9, seconds=59)
    )
    assert user.email == "mama@example.test"


def test_a_code_for_one_address_does_not_open_another(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session, "a@example.test")
    _issue(db_session, "b@example.test")
    with pytest.raises(InvalidCode):
        auth_service.verify_code(db_session, "b@example.test", code)


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def test_five_wrong_guesses_lock_the_code_even_for_the_right_one(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session)
    for _ in range(auth_service.MAX_ATTEMPTS):
        with pytest.raises(InvalidCode):
            auth_service.verify_code(db_session, "mama@example.test", _wrong(code))
    stored = db_session.execute(select(LoginCode)).scalars().one()
    assert stored.attempts == auth_service.MAX_ATTEMPTS
    with pytest.raises(InvalidCode):
        auth_service.verify_code(db_session, "mama@example.test", code)
    assert db_session.execute(select(User).where(User.email == "mama@example.test")).first() is None


def test_four_wrong_guesses_still_allow_the_right_one(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session)
    for _ in range(auth_service.MAX_ATTEMPTS - 1):
        with pytest.raises(InvalidCode):
            auth_service.verify_code(db_session, "mama@example.test", _wrong(code))
    user, _ = auth_service.verify_code(db_session, "mama@example.test", code)
    assert user.email == "mama@example.test"


def test_a_fresh_code_after_a_lockout_works(app, db_session) -> None:  # type: ignore[no-untyped-def]
    code = _issue(db_session)
    for _ in range(auth_service.MAX_ATTEMPTS):
        with pytest.raises(InvalidCode):
            auth_service.verify_code(db_session, "mama@example.test", _wrong(code))
    fresh = _issue(db_session)
    user, _ = auth_service.verify_code(db_session, "mama@example.test", fresh)
    assert user.email == "mama@example.test"


def test_no_open_code_is_the_same_error_as_a_wrong_code(app, db_session) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(InvalidCode) as nothing:
        auth_service.verify_code(db_session, "nobody@example.test", "123456")
    _issue(db_session)
    with pytest.raises(InvalidCode) as wrong:
        auth_service.verify_code(db_session, "mama@example.test", "999999")
    assert nothing.value.message == wrong.value.message
    assert nothing.value.code == wrong.value.code == "invalid_code"


def test_an_account_switched_off_after_the_code_was_sent_cannot_sign_in(app, db_session) -> None:  # type: ignore[no-untyped-def]
    user = UserFactory(email="mama@example.test")
    code = _issue(db_session)
    user.is_active = False
    db_session.flush()
    with pytest.raises(InvalidCode):
        auth_service.verify_code(db_session, "mama@example.test", code)


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("a@example.test", "A"),
        ("jane_doe-smith@example.test", "Jane Doe Smith"),
        ("x" * 200 + "@example.test", "X" + "x" * 79),
        ("@example.test", "Family member"),
    ],
)
def test_default_display_name(email: str, expected: str) -> None:
    assert auth_service._default_display_name(email) == expected
