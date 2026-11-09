"""Bill files kept in Postgres (the ``STORAGE_BACKEND=database`` demo shortcut).

The signed token is the permission, exactly like a signed S3 link: it names one file and stops
working after five minutes.
"""

from flask import Blueprint, Response, abort, current_app

from app.services import database_storage, storage_service

blp = Blueprint("files", __name__, url_prefix="/api/files")


@blp.get("/<token>")
def read_file(token: str) -> Response:
    if current_app.config.get("STORAGE_BACKEND") != "database":
        abort(404)
    key = database_storage.key_from_token(token, max_age=storage_service.SIGNED_URL_SECONDS)
    found = database_storage.get(key) if key else None
    if found is None:
        abort(404)
    data, content_type = found
    response = Response(data, mimetype=content_type)
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
