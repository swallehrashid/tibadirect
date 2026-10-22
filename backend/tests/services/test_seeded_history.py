"""The seeded demo cases carry a believable status history for their timelines."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.models.case import Case
from app.seed import seed_all
from app.services import case_service


def test_every_seeded_timeline_ends_at_the_cases_status_in_order_and_in_the_past(
    db_session,
) -> None:  # type: ignore[no-untyped-def]
    seed_all(db_session)
    db_session.flush()
    now = datetime.now(UTC)
    cases = db_session.execute(select(Case)).scalars().all()
    assert len(cases) == 8
    for case in cases:
        timeline = case_service.status_timeline(db_session, case)
        assert timeline[0]["status"] == "draft"
        assert timeline[-1]["status"] == case.status.value
        stamps = [entry["at"] for entry in timeline]
        assert stamps == sorted(stamps)
        assert stamps[-1] <= now
        assert case.created_at <= stamps[-1]


def test_a_seeded_cases_path_only_uses_allowed_moves(db_session) -> None:  # type: ignore[no-untyped-def]
    seed_all(db_session)
    db_session.flush()
    for case in db_session.execute(select(Case)).scalars():
        statuses = [e["status"] for e in case_service.status_timeline(db_session, case)]
        for before, after in zip(statuses, statuses[1:], strict=False):
            allowed = {s.value for s in case_service.ALLOWED_TRANSITIONS[type(case.status)(before)]}
            assert after in allowed, (case.public_slug, before, after)
