import pytest

from app.models import enums
from app.models.enums import ALL_ENUMS, CaseStatus, db_enum_name, pg_enum


def test_all_enums_are_registered() -> None:
    defined = {
        obj
        for obj in vars(enums).values()
        if isinstance(obj, type) and issubclass(obj, enums.StrEnum) and obj is not enums.StrEnum
    }
    assert defined == set(ALL_ENUMS)


@pytest.mark.parametrize(
    ("cls_name", "db_name"),
    [("CaseStatus", "case_status"), ("ConfirmationRequestStatus", "confirmation_request_status")],
)
def test_db_enum_name_is_snake_case(cls_name: str, db_name: str) -> None:
    assert db_enum_name(getattr(enums, cls_name)) == db_name


def test_pg_enum_stores_lowercase_values() -> None:
    column_type = pg_enum(CaseStatus)
    assert column_type.name == "case_status"
    assert column_type.enums == [m.value for m in CaseStatus]
    assert all(value == value.lower() for value in column_type.enums)


def test_enum_members_compare_as_strings() -> None:
    assert CaseStatus.LIVE == "live"
    assert str(CaseStatus.LIVE) == "live"
