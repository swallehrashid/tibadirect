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
