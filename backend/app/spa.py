"""Serves the built web app next to the API, so both share one origin.

In production Flask returns ``frontend/dist`` files and falls back to ``index.html`` for any
other path, which is what a single-page app's router needs. Files under ``assets/`` have hashed
names, so they are cached for a year; everything else is revalidated. Until the frontend exists
(Phase 4) a small placeholder page stands in for it. API paths never fall back to the page: an
unknown ``/api`` route is a JSON 404 like any other.
"""

from pathlib import Path

from flask import Flask, Response, send_from_directory

from app.errors import NotFound

PLACEHOLDER_DIR = Path(__file__).resolve().parent / "static" / "placeholder"
DEFAULT_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def dist_dir(configured: str | None = None) -> Path:
    """The built app's folder, or the placeholder when no build is there."""
    candidate = Path(configured) if configured else DEFAULT_DIST_DIR
    return candidate if (candidate / "index.html").is_file() else PLACEHOLDER_DIR


def _serve(directory: Path, name: str, cache: str) -> Response:
    response = send_from_directory(directory, name, max_age=None)
    response.headers["Cache-Control"] = cache
    return response


def init_spa(app: Flask) -> None:
    """Register the catch-all route (a no-op unless ``SERVE_SPA`` is on)."""
    if not app.config.get("SERVE_SPA"):
        return
    directory = dist_dir(app.config.get("FRONTEND_DIST_DIR"))

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path: str) -> Response:
        parts = path.split("/")
        if parts[0] == "api":
            raise NotFound()
        # Hidden files and traversal never reach send_from_directory.
        if path and not any(part.startswith(".") or part == "" for part in parts):
            candidate = directory / path
            if candidate.is_file():
                cache = IMMUTABLE if parts[0] == "assets" else REVALIDATE
                return _serve(directory, path, cache)
        return _serve(directory, "index.html", REVALIDATE)
