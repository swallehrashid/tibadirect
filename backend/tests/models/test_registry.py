"""Whole-schema checks: the metadata matches the design and respects the data-minimisation rules."""

import re
from pathlib import Path

from sqlalchemy.orm import configure_mappers

from app.extensions import db
from app.models.enums import ALL_ENUMS, db_enum_name

DESIGN_DOC = Path(__file__).resolve().parents[3] / "docs" / "database" / "DATABASE_DESIGN.md"

EXPECTED_TABLES = {
    # identity
    "users",
    "login_codes",
    # hospitals
    "facility_register_entries",
    "hospitals",
    "hospital_staff",
    # cases
    "cases",
    "case_documents",
    "triage_results",
    "consents",
    "review_items",
    # verification
    "confirmation_requests",
    "confirmations",
    # money
    "fx_rates",
    "donations",
    "settlements",
    "settlement_allocations",
    "refunds",
    "ledger_entries",
    # integration
    "webhook_events",
    # platform
    "appeal_checks",
    "audit_log",
    "agent_actions",
}

# Column-name fragments that must never appear: the never-collect list.
NEVER_COLLECT = ("diagnosis", "procedure", "medication", "drug", "test_result", "lab_result")
# Money columns are named *_usd_minor, *_kes_minor or amount_minor ("is_minor" means a child).
MONEY_COLUMN = re.compile(r"(_usd_minor|_kes_minor|^amount_minor)$")
NEVER_COLLECT_EXACT = {"national_id", "id_number", "passport", "passport_number", "date_of_birth"}


def test_the_schema_has_exactly_the_22_designed_tables() -> None:
    assert set(db.metadata.tables) == EXPECTED_TABLES
    assert len(EXPECTED_TABLES) == 22


def test_every_table_in_the_design_document_is_modelled() -> None:
    text = DESIGN_DOC.read_text(encoding="utf8")
    documented = set(re.findall(r"^### (\w+)(?: \(append-only\))?$", text, flags=re.MULTILINE))
    dictionary = documented & EXPECTED_TABLES
    assert dictionary == EXPECTED_TABLES


def test_every_table_has_a_primary_key_and_creation_time() -> None:
    for name, table in db.metadata.tables.items():
        assert table.primary_key.columns, name
        assert "created_at" in table.c, name


def test_no_column_can_hold_a_diagnosis_or_national_id() -> None:
    offenders = [
        f"{table.name}.{column.name}"
        for table in db.metadata.tables.values()
        for column in table.columns
        if column.name in NEVER_COLLECT_EXACT or any(word in column.name for word in NEVER_COLLECT)
    ]
    assert offenders == []


def test_money_columns_are_integers_not_floats() -> None:
    from sqlalchemy import BigInteger, Float, Integer, Numeric, SmallInteger

    for table in db.metadata.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, Float), f"{table.name}.{column.name}"
            if MONEY_COLUMN.search(column.name):
                assert isinstance(column.type, BigInteger | Integer | SmallInteger), (
                    f"{table.name}.{column.name}"
                )
    rate = db.metadata.tables["fx_rates"].c.rate
    assert isinstance(rate.type, Numeric)
    assert (rate.type.precision, rate.type.scale) == (18, 8)


def test_sensitive_values_use_encrypted_columns() -> None:
    from app.models.types import EncryptedBytes

    encrypted = {
        f"{table.name}.{column.name}"
        for table in db.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, EncryptedBytes)
    }
    assert encrypted == {
        "cases.patient_ref_enc",
        "donations.donor_email_enc",
        "hospitals.settlement_account_ref_enc",
    }


def test_every_relationship_has_a_matching_back_populates() -> None:
    configure_mappers()
    for mapper in db.Model.registry.mappers:
        for relationship in mapper.relationships:
            assert relationship.back_populates, f"{mapper.class_.__name__}.{relationship.key}"
            reverse = relationship.mapper.relationships[relationship.back_populates]
            assert reverse.back_populates == relationship.key, (
                f"{mapper.class_.__name__}.{relationship.key}"
            )


def test_every_foreign_key_names_its_delete_behaviour() -> None:
    allowed = {"RESTRICT", "CASCADE", "SET NULL"}
    for table in db.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert foreign_key.ondelete in allowed, f"{table.name}.{foreign_key.parent.name}"


def test_enum_names_are_unique_and_registered() -> None:
    names = [db_enum_name(cls) for cls in ALL_ENUMS]
    assert len(names) == len(set(names)) == 30
