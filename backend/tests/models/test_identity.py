from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.models.enums import UserRole
from app.models.identity import LoginCode, User
from tests.factories import LoginCodeFactory, UserFactory
from tests.models.helpers import assert_db_rejects


def test_user_defaults(db_session) -> None:
    user = UserFactory()
    db_session.refresh(user)
    assert user.role is UserRole.FAMILY
    assert user.preferred_language == "en"
    assert user.is_active is True
    assert user.phone is None
    assert user.last_login_at is None
    assert user.created_at is not None
    assert user.updated_at is not None


def test_role_is_stored_as_a_lowercase_native_enum(db_session) -> None:
    UserFactory(role=UserRole.HOSPITAL_STAFF)
    stored = db_session.execute(text("SELECT role::text FROM users")).scalar_one()
    assert stored == "hospital_staff"


def test_email_is_unique_ignoring_case(db_session) -> None:
    UserFactory(email="Family@Example.test")
    error = assert_db_rejects(db_session, lambda: UserFactory(email="family@example.TEST"))
    assert isinstance(error, IntegrityError)
    assert "uq_users_email" in str(error)


def test_email_lookup_is_case_insensitive(db_session) -> None:
    UserFactory(email="Family@Example.test")
    found = db_session.query(User).filter(User.email == "FAMILY@example.test").one()
    assert found.email == "Family@Example.test"


def test_unsupported_language_is_rejected(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: UserFactory(preferred_language="fr"))
    assert "ck_users_preferred_language_valid" in str(error)


def test_swahili_is_accepted(db_session) -> None:
    assert UserFactory(preferred_language="sw").preferred_language == "sw"


def test_role_is_required(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: UserFactory(role=None))
    assert isinstance(error, IntegrityError)


def test_repr_never_shows_personal_fields(db_session) -> None:
    user = UserFactory(
        email="jane.doe@example.test", display_name="Jane Doe", phone="+254700000001"
    )
    text_repr = repr(user)
    assert "jane" not in text_repr.lower()
    assert "254700000001" not in text_repr
    assert str(user.id) in text_repr


def test_login_code_defaults(db_session) -> None:
    code = LoginCodeFactory()
    db_session.refresh(code)
    assert code.attempts == 0
    assert code.consumed_at is None
    assert code.ip_hmac is None
    assert code.expires_at > datetime.now(UTC)


def test_login_code_does_not_need_an_existing_user(db_session) -> None:
    assert LoginCodeFactory(email="new.person@example.test").id is not None
    assert db_session.query(User).count() == 0


@pytest.mark.parametrize("attempts", [-1, 11])
def test_login_attempts_must_stay_between_0_and_10(db_session, attempts: int) -> None:
    error = assert_db_rejects(db_session, lambda: LoginCodeFactory(attempts=attempts))
    assert "ck_login_codes_attempts_range" in str(error)


@pytest.mark.parametrize("attempts", [0, 5, 10])
def test_login_attempts_in_range_are_accepted(db_session, attempts: int) -> None:
    assert LoginCodeFactory(attempts=attempts).attempts == attempts


def test_code_hmac_is_fixed_width(db_session) -> None:
    error = assert_db_rejects(db_session, lambda: LoginCodeFactory(code_hmac="a" * 65))
    assert isinstance(error, DataError)


def test_login_code_repr_hides_email_and_hash(db_session) -> None:
    code = LoginCodeFactory(email="jane.doe@example.test", code_hmac="b" * 64)
    assert "jane" not in repr(code).lower()
    assert "bbbb" not in repr(code)


def test_email_is_indexed_for_lookup(db_session) -> None:
    indexes = db_session.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'login_codes'")
    ).scalars()
    assert "ix_login_codes_email" in set(indexes)


def test_model_classes_are_registered_on_the_shared_metadata() -> None:
    from app.extensions import db

    assert {"users", "login_codes"} <= set(db.metadata.tables)
    assert LoginCode.__table__.metadata is User.__table__.metadata
