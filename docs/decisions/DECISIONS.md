# Decisions

One line per decision: `YYYY-MM-DD · feature · decision — reason`.

2026-10-19 · P1-F01 · docker-compose holds the infrastructure services only (Postgres, Redis, MinIO, Mailpit); the `app` profile with api, worker, scheduler and web is added once the Dockerfile exists (P2-F12) — F01 scopes compose to infrastructure and there is nothing to build yet.
2026-10-19 · P1-F01 · All compose host ports are read from `.env` with the spec defaults as fallbacks, and bound to 127.0.0.1 — a developer machine may already run Postgres or Redis on 5432 and 6379.
2026-10-19 · P1-F01 · `make up` starts the long-running services with `--wait`, then runs the `minio-init` bucket bootstrap as a separate one-shot step — `--wait` is unreliable with containers that exit by design.
2026-10-19 · P1-F01 · `.env.example` ships a valid development-only `DATA_ENCRYPTION_KEY` so the app starts after `cp .env.example .env`; production config rejects the placeholder values (P1-F03).
2026-10-19 · P1-F01 · `PAYPAL_MODE` defaults to `mock` in `.env.example` so a fresh clone runs without credentials; staging and demo set `sandbox`.
2026-10-19 · P1-F01 · Empty directories from the layout in section 4.3 are kept with `.gitkeep`; deeper `backend/` and `frontend/` folders are created by the features that own them.
2026-10-19 · P1-F01 · The requirements document (.docx) is added to the local `.git/info/exclude` alongside the build spec so it is never staged — it is working material, not part of the repository.
2026-10-19 · P1-F01 · Backend uses a Python 3.12 virtual environment at `backend/.venv` — the spec pins 3.12 and the host default is newer.
2026-10-19 · P1-F02 · The schema has 22 tables, not 21 — the spec's own table groups list 22 (2 + 3 + 5 + 2 + 6 + 1 + 3); the enumerated tables are authoritative, so the F10 check expects 22 tables plus `alembic_version`.
2026-10-19 · P1-F02 · `cases.photo_document_id` and `case_documents.case_id` reference each other, so the foreign key from `cases` is created with `use_alter` after both tables exist — avoids a circular create and drop order in the migration.
2026-10-19 · P1-F02 · Foreign keys restrict deletion except `case_documents` and `consents`, which cascade from `cases` — money and audit history must not vanish with a parent row.
2026-10-19 · P1-F03 · The flask-smorest `Api` and the OpenAPI document (`/api/openapi.json`) are wired in F03, not P2-F01 — the health endpoint must appear in OpenAPI with its schemas (definition of done); P2-F01 adds the Swagger UI, pagination helper, CSRF, Talisman, Limiter and CORS to `extensions.py`.
2026-10-19 · P1-F03 · `/api/health` returns 200 `ok` when the database and Redis answer, 200 `degraded` when only Redis is down, and 503 `error` when the database is down — Render's health probe should only fail when the app cannot serve data.
2026-10-19 · P1-F03 · Error responses are produced by a `TibaApi` subclass of the flask-smorest `Api` plus Flask handlers for `AppError` and unexpected exceptions — one envelope for aborts, validation errors and bugs; 5xx responses never expose exception text.
2026-10-19 · P1-F03 · The request log line records the route template, method, status and duration only — never raw paths, query strings, bodies or client addresses, so identifiers and personal data in URLs stay out of the logs.
2026-10-19 · P1-F03 · The pytest schema comes from `db.create_all()` until the initial migration lands in P1-F10, which switches the fixture to Alembic — F03 to F09 have no migration to run yet.
2026-10-19 · P1-F03 · Test config refuses any database whose name does not end in `_test` and forces mock PayPal and no language model key — a test run can never touch the development database or leak a real key.
2026-10-19 · P1-F03 · SQLAlchemy is pinned to the 2.0 line (2.0.55) although 2.1 resolves — the spec targets SQLAlchemy 2.0 with Flask-SQLAlchemy 3.1.
2026-10-19 · P1-F03 · `constants.py` (policy settings) is deferred to the first feature that reads those settings (P2-F09 / P3-F02) — nothing in Phase 1 needs it, and the values already load into `app.config`.
2026-10-19 · P1-F03 · Until the Docker group is active in the build session, development uses a private Postgres 18 and Redis on ports 55432 and 56379 (outside the repo); CI and docker-compose use Postgres 16 as specified — the schema uses nothing version-specific.
