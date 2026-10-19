import re
from pathlib import Path

import pytest

from app.models import enums
from app.models.enums import ALL_ENUMS, CaseStatus, db_enum_name, pg_enum

DESIGN_DOC = Path(__file__).resolve().parents[3] / "docs" / "database" / "DATABASE_DESIGN.md"


def _documented_enums() -> dict[str, list[str]]:
    """Rows of the enumeration table in the database design document."""
    section = DESIGN_DOC.read_text(encoding="utf8").split("## 3. Enumerations")[1].split("\n## ")[0]
    rows = re.findall(r"^\| `(\w+)` \| (.+) \|$", section, flags=re.MULTILINE)
    return {name: re.findall(r"`(\w+)`", values) for name, values in rows}


def test_every_enum_matches_the_design_document() -> None:
    documented = _documented_enums()
    in_code = {db_enum_name(cls): [m.value for m in cls] for cls in ALL_ENUMS}
    assert in_code == documented


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
