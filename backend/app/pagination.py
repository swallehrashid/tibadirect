"""Pagination: ``?page=1&page_size=20`` in, ``{items, page, page_size, total}`` out."""

from typing import Any

import marshmallow as ma
from flask_sqlalchemy.session import Session
from sqlalchemy import Select, func, select

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class PaginationArgsSchema(ma.Schema):
    """Query parameters accepted by every list endpoint."""

    class Meta:
        unknown = ma.EXCLUDE

    page = ma.fields.Integer(load_default=1, validate=ma.validate.Range(min=1))
    page_size = ma.fields.Integer(
        load_default=DEFAULT_PAGE_SIZE, validate=ma.validate.Range(min=1, max=MAX_PAGE_SIZE)
    )


_page_schemas: dict[type[ma.Schema], type[ma.Schema]] = {}


def page_of(item_schema: type[ma.Schema]) -> type[ma.Schema]:
    """The response schema for a page of ``item_schema`` (one class per item schema)."""
    if item_schema not in _page_schemas:
        base = item_schema.__name__.removesuffix("Schema")
        _page_schemas[item_schema] = type(
            f"{base}PageSchema",
            (ma.Schema,),
            {
                "items": ma.fields.List(ma.fields.Nested(item_schema)),
                "page": ma.fields.Integer(),
                "page_size": ma.fields.Integer(),
                "total": ma.fields.Integer(),
            },
        )
    return _page_schemas[item_schema]


def paginate(session: Session, statement: Select[Any], args: dict[str, int]) -> dict[str, Any]:
    """Run ``statement`` for one page. ``items`` are the selected ORM rows, not yet serialised.

    The statement must already have a stable ``ORDER BY`` so pages never overlap.
    """
    page, page_size = args["page"], args["page_size"]
    total = session.execute(
        select(func.count()).select_from(statement.order_by(None).subquery())
    ).scalar_one()
    items = (
        session.execute(statement.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    )
    return {"items": items, "page": page, "page_size": page_size, "total": total}
