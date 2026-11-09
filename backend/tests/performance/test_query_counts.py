"""A list takes the same number of queries whether it shows three rows or twelve.

Each row of a page used to cost its own queries (the owner of a case, the case of a review item, the
triage result and bills of every waiting bill), which was fine in the demo and slow in front of a
hospital with hundreds of bills waiting. The counts below are measured on the real routes.
"""

from collections.abc import Callable

from sqlalchemy import event

from app.extensions import db
from app.models.enums import CaseStatus, ConsentScope, UserRole
from tests.browser import Browser
from tests.factories import (
    CaseDocumentFactory,
    CaseFactory,
    ConsentFactory,
    DonationFactory,
    RefundFactory,
    ReviewItemFactory,
    TriageResultFactory,
    UserFactory,
)
from tests.hospital_helpers import make_ward

BrowserFor = Callable[..., Browser]


class Counter:
    def __enter__(self) -> "Counter":
        self.count = 0
        event.listen(db.engine, "before_cursor_execute", self._tick)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(db.engine, "before_cursor_execute", self._tick)

    def _tick(self, *args: object) -> None:
        self.count += 1


def _queries(browser: Browser, path: str) -> int:
    # The test and the request share one session; expire what the factories loaded, as a real
    # request starts with nothing loaded, or the loader options on the route would not be exercised.
    db.session.expire_all()
    with Counter() as counter:
        response = browser.get(path)
    assert response.status_code == 200, (path, response.get_data(as_text=True)[:200])
    return counter.count


def _cases_with_everything(count: int, **fields):  # type: ignore[no-untyped-def]
    made = []
    for _ in range(count):
        case = CaseFactory(**fields)
        document = CaseDocumentFactory(case=case)
        TriageResultFactory(case=case, document=document)
        ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
        ReviewItemFactory(case=case)
        made.append(case)
    db.session.commit()
    return made


def test_the_admin_case_list_does_not_ask_again_for_each_owner(browser_for: BrowserFor) -> None:
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    _cases_with_everything(3)
    few = _queries(admin, "/api/admin/cases?page_size=50")
    _cases_with_everything(9)
    many = _queries(admin, "/api/admin/cases?page_size=50")
    assert many == few


def test_the_review_queue_does_not_ask_again_for_each_case(browser_for: BrowserFor) -> None:
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    _cases_with_everything(3)
    few = _queries(admin, "/api/admin/review-items?page_size=50")
    _cases_with_everything(9)
    many = _queries(admin, "/api/admin/review-items?page_size=50")
    assert many == few


def test_the_hospital_queue_does_not_ask_twice_for_each_waiting_bill(
    browser_for: BrowserFor,
) -> None:
    ward = make_ward()
    clerk = browser_for(ward.clerk.user)
    for _ in range(3):
        case = CaseFactory(hospital=ward.hospital, status=CaseStatus.PENDING_CONFIRMATION)
        TriageResultFactory(case=case, document=CaseDocumentFactory(case=case))
    db.session.commit()
    few = _queries(clerk, "/api/hospital/queue")
    for _ in range(9):
        case = CaseFactory(hospital=ward.hospital, status=CaseStatus.PENDING_CONFIRMATION)
        TriageResultFactory(case=case, document=CaseDocumentFactory(case=case))
    db.session.commit()
    many = _queries(clerk, "/api/hospital/queue")
    assert many == few


def test_the_admin_case_page_loads_refunds_for_all_donations_at_once(
    browser_for: BrowserFor,
) -> None:
    admin = browser_for(UserFactory(role=UserRole.ADMIN))
    counts = []
    for donations in (2, 10):
        case = CaseFactory(status=CaseStatus.LIVE)
        for _ in range(donations):
            RefundFactory(donation=DonationFactory(case=case, status="captured"))
        db.session.commit()
        counts.append(_queries(admin, f"/api/admin/cases/{case.id}"))
    assert counts[0] == counts[1], counts
