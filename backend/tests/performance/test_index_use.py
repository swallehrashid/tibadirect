"""The main queries can use an index.

Tables that only grow (cases, donations, the ledger, the audit log, ...) must never be read from
end to end by a page or a periodic job. Each statement a route or job sends is run through
EXPLAIN with sequential scans switched off: if the planner still has to read a whole big table,
no index serves the query, and the test names the statement. Text searches with a wildcard on
both sides (the admin's search box) cannot use a plain index and are left out on purpose.
"""

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy import event

from app.extensions import db
from app.jobs import tasks
from app.models.enums import CaseStatus, ConsentScope, UserRole
from tests.browser import Browser
from tests.donation_helpers import donate, settleable_case
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    ConsentFactory,
    ReviewItemFactory,
    TriageResultFactory,
    UserFactory,
)
from tests.hospital_helpers import make_ward

BrowserFor = Callable[..., Browser]

TOTALS = re.compile(r"\s*SELECT (count\(|coalesce\(sum\()", re.IGNORECASE)
GROWING_TABLES = (
    "audit_log",
    "cases",
    "case_documents",
    "consents",
    "confirmations",
    "donations",
    "ledger_entries",
    "refunds",
    "review_items",
    "settlements",
    "triage_results",
    "webhook_events",
)


@contextmanager
def explained() -> Iterator[list[tuple[str, str]]]:
    """Collect (statement, table) for every statement that has to read a growing table in full."""
    found: list[tuple[str, str]] = []

    def check(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        if not statement.lstrip().upper().startswith("SELECT") or "ILIKE" in statement.upper():
            return
        if TOTALS.match(statement):
            return  # the admin overview adds up whole tables on purpose
        raw = conn.connection.dbapi_connection
        probe = raw.cursor()
        try:
            probe.execute("SET enable_seqscan = off")
            probe.execute("EXPLAIN (FORMAT JSON) " + statement, parameters)
            plan = str(probe.fetchone()[0])
        finally:
            probe.execute("RESET enable_seqscan")
            probe.close()
        for table in re.findall(r"'Node Type': 'Seq Scan'.*?'Relation Name': '(\w+)'", plan):
            if table in GROWING_TABLES:
                found.append((re.sub(r"\s+", " ", statement)[:700], table))

    event.listen(db.engine, "before_cursor_execute", check)
    try:
        yield found
    finally:
        event.remove(db.engine, "before_cursor_execute", check)


def _world(browser_for: BrowserFor):  # type: ignore[no-untyped-def]
    """Enough of everything for each page to have something to list."""
    ward = make_ward()
    live = settleable_case()
    donate(db.session, live, "50.00")
    CaseDocumentFactory(case=live)
    TriageResultFactory(case=live, document=CaseDocumentFactory(case=live))
    ReviewItemFactory(case=live)
    ConsentFactory(case=live, scope=ConsentScope.HOSPITAL_VERIFICATION)
    CaseFactory(status=CaseStatus.PENDING_TRIAGE)
    db.session.commit()
    return ward, live


def test_no_page_or_job_reads_a_growing_table_end_to_end(
    browser_for: BrowserFor, s3, paypal
) -> None:  # type: ignore[no-untyped-def]
    ward, live = _world(browser_for)
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    clerk = browser_for(ward.clerk.user)
    owner = browser_for(live.created_by)
    visitor = browser_for(UserFactory())
    slug = live.public_slug

    with explained() as scans:
        for path in (
            "/api/admin/cases?page_size=20",
            "/api/admin/cases?status=live",
            "/api/admin/review-items",
            "/api/admin/review-items?status=open",
            "/api/admin/audit-log?page_size=20",
            f"/api/admin/audit-log?target_type=case&target_id={live.id}",
            "/api/admin/audit-log?action=case.viewed",
            "/api/admin/stats",
            f"/api/admin/cases/{live.id}",
            "/api/admin/ledger/verify",
        ):
            assert admin.get(path).status_code == 200, path
        for path in ("/api/hospital/queue", "/api/hospital/settlements"):
            assert clerk.get(path).status_code == 200, path
        for path in ("/api/cases", f"/api/cases/{live.id}", f"/api/cases/{live.id}/documents"):
            assert owner.get(path).status_code == 200, path
        for path in (
            "/api/public/cases",
            "/api/public/stats",
            f"/api/public/cases/{slug}",
            f"/api/public/cases/{slug}/ledger",
            f"/api/public/cases/{slug}/ledger/verify",
        ):
            assert visitor.get(path).status_code == 200, path
        order = visitor.post("/api/donations/orders", {"case_slug": slug, "amount_usd": "20"})
        assert order.status_code == 201
        visitor.post(f"/api/donations/orders/{order.get_json()['order_id']}/capture")
        # The periodic jobs.
        tasks.sweep_pending_triage()
        tasks.retention_sweep()
        tasks.sweep_webhook_events()
        tasks.poll_payout_status()
        tasks.sweep_settlements()
        tasks.sweep_invoices()
        tasks.expire_follow_up_holds()

    assert scans == [], "\n".join(f"{table}: {statement}" for statement, table in scans)


def test_the_check_would_notice_a_missing_index(db_session) -> None:  # type: ignore[no-untyped-def]
    """The instrument works: a filter on a column with no index is reported."""
    with explained() as scans:
        db.session.execute(
            db.text("SELECT id FROM donations WHERE paypal_fee_usd_minor = :fee"), {"fee": 123}
        ).all()
    assert [table for _, table in scans] == ["donations"]
