from unittest.mock import MagicMock

from app.services import health_service


def test_check_database_false_and_rolls_back_when_query_fails() -> None:
    session = MagicMock()
    session.execute.side_effect = RuntimeError("connection refused")
    assert health_service.check_database(session) is False
    session.rollback.assert_called_once()


def test_check_redis_false_when_ping_fails() -> None:
    client = MagicMock()
    client.ping.side_effect = ConnectionError("down")
    assert health_service.check_redis(client) is False


def test_get_health_reports_each_dependency() -> None:
    session = MagicMock()
    healthy = MagicMock()
    healthy.ping.return_value = True
    result = health_service.get_health(session, healthy, "1.2.3")
    assert (result.status, result.db, result.redis, result.version) == ("ok", "up", "up", "1.2.3")
    assert result.http_status == 200

    session.execute.side_effect = RuntimeError("down")
    result = health_service.get_health(session, healthy, "1.2.3")
    assert (result.status, result.db, result.http_status) == ("error", "down", 503)
