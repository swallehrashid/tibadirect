import uuid

from sqlalchemy import MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.base import NAMING_CONVENTION, Base, TimestampMixin, UUIDPrimaryKeyMixin


class _ScratchBase(DeclarativeBase):
    """Separate metadata so the probe model never leaks into the real schema."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class _Widget(_ScratchBase, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "widgets"
    name: Mapped[str] = mapped_column(String(20), unique=True)


def test_real_metadata_uses_the_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_constraint_names_follow_the_convention() -> None:
    names = {c.name for c in _Widget.__table__.constraints}
    assert names == {"pk_widgets", "uq_widgets_name"}


def test_mixins_provide_the_standard_columns() -> None:
    columns = _Widget.__table__.c
    assert set(columns.keys()) == {"id", "created_at", "updated_at", "name"}
    assert columns.id.primary_key
    assert not columns.created_at.nullable
    assert not columns.updated_at.nullable
    assert columns.created_at.type.timezone is True
    assert columns.updated_at.type.timezone is True


def test_primary_key_default_is_a_random_uuid4() -> None:
    generate = _Widget.__table__.c.id.default.arg
    first, second = generate(None), generate(None)
    assert isinstance(first, uuid.UUID)
    assert first.version == 4
    assert first != second
