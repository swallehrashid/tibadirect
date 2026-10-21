"""The public list of hospitals a family can choose from."""

from sqlalchemy import Select, select

from app.models.hospital import Hospital

MAX_QUERY_LENGTH = 80


def _like_pattern(text: str) -> str:
    """A contains-match for ILIKE with wildcard characters escaped, so a typed ``%`` is plain."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def active_hospitals(query: str | None = None) -> Select[tuple[Hospital]]:
    """Active hospitals by name, optionally narrowed to a name or county containing ``query``."""
    statement = select(Hospital).where(Hospital.is_active.is_(True))
    cleaned = " ".join((query or "").split())[:MAX_QUERY_LENGTH]
    if cleaned:
        pattern = _like_pattern(cleaned)
        statement = statement.where(
            Hospital.display_name.ilike(pattern, escape="\\")
            | Hospital.county.ilike(pattern, escape="\\")
        )
    return statement.order_by(Hospital.display_name, Hospital.id)
