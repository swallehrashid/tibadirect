"""Helpers shared by model tests."""

from collections.abc import Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import scoped_session


def assert_db_rejects(session: scoped_session, action: Callable[[], object]) -> DBAPIError:
    """Run ``action`` then flush; the database must refuse with an integrity or data error.

    The session is rolled back afterwards, so call this last in a test.
    """
    try:
        action()
        session.flush()
    except DBAPIError as error:
        session.rollback()
        return error
    raise AssertionError("The database accepted a row it should have rejected")
