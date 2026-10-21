"""The public ledger, its verification, and the admin check of the whole chain."""

import re
from collections.abc import Callable

import pytest
from sqlalchemy import select, text

from app.models.enums import (
    CaseStatus,
    ConsentScope,
    DonationStatus,
    LedgerAccount,
    LedgerEntryType,
    UserRole,
)
from app.models.ledger import GENESIS_HASH, LedgerEntry
from app.models.platform import AuditLog
from app.seed import SLUGS, seed_all
from app.services import ledger_service
from app.services.ledger_service import NewEntry
from tests.browser import Browser
from tests.factories import CaseFactory, ConsentFactory, DonationFactory, UserFactory

BrowserFor = Callable[..., Browser]
ROW_KEYS = {
    "created_at", "entry_type", "account", "amount_minor", "amount", "currency",
    "hash_short", "reference", "donor_name",
}  # fmt: skip


def _tamper(db_session, sql: str, **params) -> None:  # type: ignore[no-untyped-def]
    """Change a row the way someone with database access would: past the append-only trigger."""
    db_session.execute(
        text("ALTER TABLE ledger_entries DISABLE TRIGGER trg_ledger_entries_append_only")
    )
    db_session.execute(text(sql), params)
    db_session.execute(
        text("ALTER TABLE ledger_entries ENABLE TRIGGER trg_ledger_entries_append_only")
    )
    db_session.expire_all()


@pytest.fixture
def world(db_session):  # type: ignore[no-untyped-def]
    seed_all(db_session)
    db_session.flush()


def _all_rows(client, slug: str) -> list[dict]:  # type: ignore[no-untyped-def]
    rows, page = [], 1
    while True:
        body = client.get(f"/api/public/cases/{slug}/ledger?page={page}&page_size=100").get_json()
        rows.extend(body["items"])
        if len(rows) >= body["total"]:
            return rows
        page += 1


# --- The public ledger -------------------------------------------------------------------------


def test_the_public_ledger_shows_every_movement_oldest_first(client, world) -> None:  # type: ignore[no-untyped-def]
    body = client.get(f"/api/public/cases/{SLUGS['funded']}/ledger").get_json()
    assert body["total"] >= 6
    stamps = [row["created_at"] for row in body["items"]]
    assert stamps == sorted(stamps)
    kinds = {row["entry_type"] for row in body["items"]}
    assert {"donation_captured", "paypal_fee"} <= kinds
    assert all(set(row) == ROW_KEYS for row in body["items"])


def test_each_row_has_a_short_hash_a_formatted_amount_and_a_signed_sign(client, world) -> None:  # type: ignore[no-untyped-def]
    rows = _all_rows(client, SLUGS["funded"])
    for row in rows:
        assert len(row["hash_short"]) == 12
        assert row["currency"] in {"USD", "KES"}
        assert row["amount"] == f"{row['amount_minor'] / 100:.2f}"
    captured = next(r for r in rows if r["entry_type"] == "donation_captured")
    fee = next(r for r in rows if r["entry_type"] == "paypal_fee" and r["account"] == "case_funds")
    assert captured["amount_minor"] > 0
    assert fee["amount_minor"] < 0


def test_references_are_shortened_and_donor_names_follow_the_donors_choice(client, world) -> None:  # type: ignore[no-untyped-def]
    funded = _all_rows(client, SLUGS["funded"])
    closed = _all_rows(client, SLUGS["closed"])
    for row in funded + closed:
        if row["reference"]:
            assert "…" in row["reference"]
            assert len(row["reference"]) <= 9
    named = [r["donor_name"] for r in funded + closed if r["donor_name"]]
    assert "Grace W." in named
    # a name appears only on the donation line, never on fee, settlement or refund lines
    for row in funded + closed:
        if row["donor_name"]:
            assert row["entry_type"] == "donation_captured"


def test_anonymous_donors_are_never_named(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    donation = DonationFactory(
        case=case, status=DonationStatus.CAPTURED, net_usd_minor=2400, amount_usd_minor=2500,
        donor_display_name="Secret Donor", is_anonymous=True, paypal_capture_id="CAPTURE1234567890",
    )  # fmt: skip
    ledger_service.append(
        db_session,
        [
            NewEntry(
                case.id,
                LedgerEntryType.DONATION_CAPTURED,
                LedgerAccount.CASE_FUNDS,
                2500,
                "USD",
                "donation",
                donation.id,
            )
        ],
    )
    body = client.get(f"/api/public/cases/{case.public_slug}/ledger").get_json()
    [row] = body["items"]
    assert row["donor_name"] is None
    assert row["reference"] == "CAPT…7890"
    assert "Secret Donor" not in str(body)


def test_nothing_internal_appears_in_the_public_ledger(client, world) -> None:  # type: ignore[no-untyped-def]
    text_ = client.get(f"/api/public/cases/{SLUGS['funded']}/ledger?page_size=100").get_data(
        as_text=True
    )
    for private in ("prev_hash", "memo", "ref_id", "ref_type", "case_id", "seq", "PayPal fee"):
        assert private not in text_
    assert not re.search(r"[0-9a-f]{64}", text_)  # no full hash anywhere


def test_the_ledger_is_paginated(client, world) -> None:  # type: ignore[no-untyped-def]
    first = client.get(f"/api/public/cases/{SLUGS['funded']}/ledger?page_size=3").get_json()
    second = client.get(f"/api/public/cases/{SLUGS['funded']}/ledger?page_size=3&page=2").get_json()
    assert (len(first["items"]), first["page_size"]) == (3, 3)
    assert first["items"] != second["items"]
    assert first["total"] == len(_all_rows(client, SLUGS["funded"]))


def test_a_case_with_no_money_has_an_empty_ledger(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    assert client.get(f"/api/public/cases/{case.public_slug}/ledger").get_json()["items"] == []


def test_only_public_cases_have_a_public_ledger(client, world) -> None:  # type: ignore[no-untyped-def]
    for slug in (SLUGS["draft"], SLUGS["cancelled"], "nosuchslug12", SLUGS["pending_confirmation"]):
        for suffix in ("ledger", "ledger/verify"):
            response = client.get(f"/api/public/cases/{slug}/{suffix}")
            assert response.status_code == 404
            assert response.get_json()["error"]["code"] == "case_not_found"


def test_withdrawing_the_public_page_closes_the_ledger_too(client, world, db_session) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from app.models.case import Case

    case = db_session.execute(select(Case).where(Case.public_slug == SLUGS["live"])).scalar_one()
    assert client.get(f"/api/public/cases/{SLUGS['live']}/ledger").status_code == 200
    case.active_consent(ConsentScope.PUBLIC_PAGE).withdrawn_at = datetime.now(UTC)
    db_session.flush()
    db_session.expire_all()
    assert client.get(f"/api/public/cases/{SLUGS['live']}/ledger").status_code == 404


# --- Verifying one case ------------------------------------------------------------------------


def test_an_intact_ledger_verifies_for_each_public_case(client, world, db_session) -> None:  # type: ignore[no-untyped-def]
    for key in ("live", "funded", "closed"):
        body = client.get(f"/api/public/cases/{SLUGS[key]}/ledger/verify").get_json()
        rows = _all_rows(client, SLUGS[key])
        assert body["valid"] is True
        assert body["entries_checked"] == len(rows)
        assert body["last_hash"].startswith(rows[-1]["hash_short"])
        assert len(body["last_hash"]) == 64
        assert body["checked_at"]


def test_a_case_with_no_entries_verifies_with_the_genesis_hash(client, db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    body = client.get(f"/api/public/cases/{case.public_slug}/ledger/verify").get_json()
    assert (body["valid"], body["entries_checked"], body["last_hash"]) == (True, 0, GENESIS_HASH)


def test_tampering_with_any_row_makes_every_public_case_fail_verification(
    client, world, db_session
) -> None:  # type: ignore[no-untyped-def]
    victim = db_session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.case_id.is_not(None))
        .order_by(LedgerEntry.seq)
        .limit(1)
    ).scalar_one()
    _tamper(
        db_session,
        "UPDATE ledger_entries SET amount_minor = amount_minor + 1 WHERE id = :id",
        id=victim.id,
    )
    for key in ("live", "funded", "closed"):
        body = client.get(f"/api/public/cases/{SLUGS[key]}/ledger/verify").get_json()
        assert body["valid"] is False


def test_verification_is_limited_to_twenty_a_minute(client, world) -> None:  # type: ignore[no-untyped-def]
    statuses = [
        client.get(f"/api/public/cases/{SLUGS['live']}/ledger/verify").status_code
        for _ in range(21)
    ]
    assert statuses[:20] == [200] * 20
    assert statuses[20] == 429


# --- The admin check ---------------------------------------------------------------------------


def test_only_admins_may_verify_the_whole_chain(
    browser: Browser, browser_for: BrowserFor, world
) -> None:  # type: ignore[no-untyped-def]
    assert browser.get("/api/admin/ledger/verify").status_code == 401
    assert browser_for(UserFactory()).get("/api/admin/ledger/verify").status_code == 403


def test_the_admin_sees_the_whole_chain_and_the_check_is_audited(
    browser_for: BrowserFor, world, db_session
) -> None:  # type: ignore[no-untyped-def]
    admin = UserFactory(role=UserRole.ADMIN)
    total = db_session.execute(select(LedgerEntry)).scalars().all()
    body = browser_for(admin).get("/api/admin/ledger/verify").get_json()
    assert body["valid"] is True
    assert body["entries_checked"] == len(total)
    assert body["first_bad_seq"] is None
    assert body["last_hash"] == total[-1].hash
    entry = db_session.execute(
        select(AuditLog).where(AuditLog.action == "ledger.verified")
    ).scalar_one()
    assert entry.actor_user_id == admin.id
    assert entry.details == {"valid": True, "entries_checked": len(total)}


def test_the_admin_is_told_which_entry_broke_the_chain(
    browser_for: BrowserFor, world, db_session
) -> None:  # type: ignore[no-untyped-def]
    victim = db_session.execute(
        select(LedgerEntry).order_by(LedgerEntry.seq).offset(5).limit(1)
    ).scalar_one()
    seq = victim.seq
    _tamper(
        db_session,
        "UPDATE ledger_entries SET amount_minor = amount_minor + 1 WHERE id = :id",
        id=victim.id,
    )
    body = browser_for(UserFactory(role=UserRole.ADMIN)).get("/api/admin/ledger/verify").get_json()
    assert body["valid"] is False
    assert body["first_bad_seq"] == seq
    assert body["entries_checked"] == 5


def test_an_empty_ledger_verifies(browser_for: BrowserFor, db_session) -> None:  # type: ignore[no-untyped-def]
    db_session.execute(text("TRUNCATE ledger_entries RESTART IDENTITY"))
    body = browser_for(UserFactory(role=UserRole.ADMIN)).get("/api/admin/ledger/verify").get_json()
    assert (body["valid"], body["entries_checked"], body["last_hash"]) == (True, 0, GENESIS_HASH)


def test_the_routes_are_documented(client) -> None:  # type: ignore[no-untyped-def]
    paths = client.get("/api/openapi.json").get_json()["paths"]
    for path in (
        "/api/public/cases/{slug}/ledger",
        "/api/public/cases/{slug}/ledger/verify",
        "/api/admin/ledger/verify",
    ):
        assert path in paths
