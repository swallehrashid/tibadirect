"""The declarative base and constraint naming convention.

This lives outside ``app.models`` on purpose: ``extensions`` needs ``Base`` to build ``db``, while
``app.models`` imports every model (which in turn import ``db``). Keeping ``Base`` here avoids the
import cycle. ``app.models.base`` re-exports it.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Stable constraint names keep Alembic migrations deterministic.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
