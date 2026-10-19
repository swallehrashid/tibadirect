# Phase 1 review: foundations and database

Mon 19 Oct 2026 · branch `initial-set-up` · 12 feature commits, plus this review.

## Summary

Phase 1 delivered the repository, the local infrastructure definition, the system and database design
documents, a tested Flask skeleton, all 22 database tables as SQLAlchemy models with a hand-edited initial
migration (native enums, partial indexes, CHECK constraints and an append-only ledger trigger), a synthetic
dataset with lexicons, seed and reset commands that build a complete fictional demo world with a valid
hash-chained ledger, and the continuous integration workflow. The database enforces the money, consent and
confirmation rules itself, so a service bug cannot, for example, over-allocate a donation or approve a bill
twice. Every constraint has a test that names the constraint that fired. One item could not be verified in this
environment: the Docker Compose stack was written and validated but never started (see Missing inputs).

## Commits

```
a34c67b P1-F12: Add continuous integration for backend tests, linting and secret scanning
838be8c P1-F11: Add synthetic dataset generator, lexicons and seed commands
4897792 P1-F10: Create initial PostgreSQL schema migration with constraints and ledger trigger
5c13a5b P1-F09: Add appeal check, audit log and agent action models
6d6e0c8 P1-F08: Add FX, donation, settlement, refund, ledger and webhook models
888e39d P1-F07: Add hospital confirmation request and confirmation models
ab36cd7 P1-F06: Add case, document, triage, consent and review models
0cae274 P1-F05: Add user, login code, facility register, hospital and staff models
67f155a P1-F04: Add declarative base, enums, encrypted column type and money helpers
f539360 P1-F03: Add Flask application factory, configuration and health check
1adab15 P1-F02: Add system architecture and database design documents
81d0bf8 P1-F01: Scaffold repository and local infrastructure
```

## How to verify

Prerequisites: Docker with the Compose plugin, Python 3.12. Everything below runs from the repository root.

```bash
cp .env.example .env
make up                                  # Postgres 16, Redis 7, MinIO (bucket created) and Mailpit, all healthy
docker compose ps                        # every service shows "healthy"

python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements-dev.txt
make migrate                             # creates 22 tables plus alembic_version at revision 0001
make seed                                # about half a second; creates the demo world
make lint && make test                   # ruff, then 514 tests
(cd backend && .venv/bin/pytest -m slow)  # dataset reproducibility (about a minute)
```

Look at the result:

- **API:** `cd backend && .venv/bin/flask --app wsgi run`, then `curl localhost:5000/api/health` returns
  `{"db":"up","redis":"up","status":"ok","version":"0.1.0"}`; `curl -i localhost:5000/api/nope` returns the error
  envelope with an `X-Request-ID`; `curl localhost:5000/api/openapi.json` lists `/api/health`.
- **Seeded cases** (one per lifecycle state): `psql "$DATABASE_URL" -c "select public_slug, status, target_kes_minor/100 as target_kes, pledged_kes_minor/100 as pledged_kes, settled_kes_minor/100 as settled_kes from cases order by created_at"`.
- **Seed accounts** (sign-in arrives in Phase 2; codes will appear in Mailpit at http://localhost:8025):
  `admin@tibadirect.test`, `family1@example.test` to `family4@example.test`,
  `clerk@kilele-hospital.test`, `supervisor@kilele-hospital.test` (and the same pair for Mto Mwema and Ziwa Bluu).
- **Ledger chain:** `cd backend && .venv/bin/python -c "from app import create_app; from app.extensions import db; from app.services import ledger_service as l; a=create_app(); a.app_context().push(); print(l.verify_chain(db.session))"` prints `valid=True`.
- **Append-only trigger:** `psql "$DATABASE_URL" -c "update ledger_entries set amount_minor = 1"` fails with `ledger_entries is append-only`.
- **Reset:** `make reset-demo` (asks first, runs only where `DEMO_BANNER` is on) takes under a second.
- **Dataset:** `cd data/synthetic && ../../backend/.venv/bin/python validate.py` reports 742 checks passed.
- **Commit hook:** `printf 'Co-authored-by: x' > /tmp/m && .git/hooks/commit-msg /tmp/m` is rejected (the hook is local and untracked).

## Test results

| Check | Result |
|---|---|
| Backend tests | 514 passed, plus 1 slow test (dataset reproducibility) passed |
| Coverage | 98.16 percent (gate: 75) |
| `ruff check` / `ruff format --check` | clean, 76 files |
| Migrations | upgrade, downgrade to base, upgrade: clean; migrated schema equals the models (a model change without a migration fails a test) |
| Dataset validator | 742 checks passed, 0 failed |
| gitleaks v8.30.1 over all history | no leaks (5 fictional false positives allow-listed by exact value; a planted secret is still caught) |
| CI workflow | actionlint clean; every backend step run locally on an empty database without a `.env` |
| Seed / reset timing | 0.5 s / 0.7 s on an empty database |

## Decisions made

82 lines were added to `docs/decisions/DECISIONS.md` this phase (listed in full below). The ones most
worth a look:

- The schema has **22 tables, not 21** as the spec says; the spec's own table groups add up to 22.
- Database-level safeguards added beyond the dictionary: a closed case needs a close reason, a frozen case a
  freeze reason, a resolved review item a decision and a written note, a confirmed bill an amount and a time, an
  agent action beyond *proposed* a named human, and no two ledger entries may share a predecessor.
- `Base` lives in `app/db_base.py` (re-exported by `models/base.py`) to avoid an import cycle between the
  extensions and the model package.
- The existing synthetic dataset was moved into `data/synthetic/` and extended, not rewritten. Hospitals 1 to 3 were
  renamed to Kilele Mission, Mto Mwema and Ziwa Bluu General so the demo hospitals are ones that print invoices.
- All facility names were web-checked; none matches a real Kenyan facility.

<details><summary>All 82 decision lines</summary>

- 2026-10-19 · P1-F01 · docker-compose holds the infrastructure services only (Postgres, Redis, MinIO, Mailpit); the `app` profile with api, worker, scheduler and web is added once the Dockerfile exists (P2-F12) — F01 scopes compose to infrastructure and there is nothing to build yet.
- 2026-10-19 · P1-F01 · All compose host ports are read from `.env` with the spec defaults as fallbacks, and bound to 127.0.0.1 — a developer machine may already run Postgres or Redis on 5432 and 6379.
- 2026-10-19 · P1-F01 · `make up` starts the long-running services with `--wait`, then runs the `minio-init` bucket bootstrap as a separate one-shot step — `--wait` is unreliable with containers that exit by design.
- 2026-10-19 · P1-F01 · `.env.example` ships a valid development-only `DATA_ENCRYPTION_KEY` so the app starts after `cp .env.example .env`; production config rejects the placeholder values (P1-F03).
- 2026-10-19 · P1-F01 · `PAYPAL_MODE` defaults to `mock` in `.env.example` so a fresh clone runs without credentials; staging and demo set `sandbox`.
- 2026-10-19 · P1-F01 · Empty directories from the layout in section 4.3 are kept with `.gitkeep`; deeper `backend/` and `frontend/` folders are created by the features that own them.
- 2026-10-19 · P1-F01 · The requirements document (.docx) is added to the local `.git/info/exclude` alongside the build spec so it is never staged — it is working material, not part of the repository.
- 2026-10-19 · P1-F01 · Backend uses a Python 3.12 virtual environment at `backend/.venv` — the spec pins 3.12 and the host default is newer.
- 2026-10-19 · P1-F02 · The schema has 22 tables, not 21 — the spec's own table groups list 22 (2 + 3 + 5 + 2 + 6 + 1 + 3); the enumerated tables are authoritative, so the F10 check expects 22 tables plus `alembic_version`.
- 2026-10-19 · P1-F02 · `cases.photo_document_id` and `case_documents.case_id` reference each other, so the foreign key from `cases` is created with `use_alter` after both tables exist — avoids a circular create and drop order in the migration.
- 2026-10-19 · P1-F02 · Foreign keys restrict deletion except `case_documents` and `consents`, which cascade from `cases` — money and audit history must not vanish with a parent row.
- 2026-10-19 · P1-F03 · The flask-smorest `Api` and the OpenAPI document (`/api/openapi.json`) are wired in F03, not P2-F01 — the health endpoint must appear in OpenAPI with its schemas (definition of done); P2-F01 adds the Swagger UI, pagination helper, CSRF, Talisman, Limiter and CORS to `extensions.py`.
- 2026-10-19 · P1-F03 · `/api/health` returns 200 `ok` when the database and Redis answer, 200 `degraded` when only Redis is down, and 503 `error` when the database is down — Render's health probe should only fail when the app cannot serve data.
- 2026-10-19 · P1-F03 · Error responses are produced by a `TibaApi` subclass of the flask-smorest `Api` plus Flask handlers for `AppError` and unexpected exceptions — one envelope for aborts, validation errors and bugs; 5xx responses never expose exception text.
- 2026-10-19 · P1-F03 · The request log line records the route template, method, status and duration only — never raw paths, query strings, bodies or client addresses, so identifiers and personal data in URLs stay out of the logs.
- 2026-10-19 · P1-F03 · The pytest schema comes from `db.create_all()` until the initial migration lands in P1-F10, which switches the fixture to Alembic — F03 to F09 have no migration to run yet.
- 2026-10-19 · P1-F03 · Test config refuses any database whose name does not end in `_test` and forces mock PayPal and no language model key — a test run can never touch the development database or leak a real key.
- 2026-10-19 · P1-F03 · SQLAlchemy is pinned to the 2.0 line (2.0.55) although 2.1 resolves — the spec targets SQLAlchemy 2.0 with Flask-SQLAlchemy 3.1.
- 2026-10-19 · P1-F03 · `constants.py` (policy settings) is deferred to the first feature that reads those settings (P2-F09 / P3-F02) — nothing in Phase 1 needs it, and the values already load into `app.config`.
- 2026-10-19 · P1-F03 · Until the Docker group is active in the build session, development uses a private Postgres 18 and Redis on ports 55432 and 56379 (outside the repo); CI and docker-compose use Postgres 16 as specified — the schema uses nothing version-specific.
- 2026-10-19 · P1-F04 · `new_public_slug()` and `new_token()` live in `security/crypto.py` next to the other random and keyed helpers — the spec lists them in F04 without naming a module, and they are security-sensitive randomness.
- 2026-10-19 · P1-F04 · `lookup_hash(value, context="")` takes an optional context that is mixed into the HMAC input — the same value used for different purposes (login code, confirmation code, patient reference) never yields the same digest; with no context it is exactly HMAC(pepper, normalised value) as specified.
- 2026-10-19 · P1-F04 · Values are normalised before hashing (NFKC, trimmed, case-folded, single spaces) — a patient reference typed with different case or spacing must still match for the duplicate-patient check.
- 2026-10-19 · P1-F04 · `pg_enum(cls, name=None)` derives the PostgreSQL type name from the class (`CaseStatus` becomes `case_status`), and a test compares every enum with the table in DATABASE_DESIGN.md — the code and the reviewed design cannot drift apart.
- 2026-10-19 · P1-F04 · `EncryptedBytes` returns plaintext bytes in Python and stores `nonce || ciphertext || tag` in the database; decryption failure raises `CryptoError` and never returns partial data.
- 2026-10-19 · P1-F04 · Money helpers reject `float` and `bool` input outright and expose rounding as a parameter (half up by default, ceiling for the donation goal) — floating point must not be able to enter a money calculation.
- 2026-10-19 · P1-F04 · The test config falls back to the development encryption key when none is set — tests stay hermetic in CI without a `.env`; production still rejects that key.
- 2026-10-19 · P1-F05 · `Base` and the naming convention live in `app/db_base.py` and are re-exported by `app/models/base.py` — `extensions` needs `Base` to build `db`, and importing `app.models.base` would run `app/models/__init__.py`, which imports every model, which needs `db`; the split removes the import cycle while keeping `db.Model` and the layout in the spec.
- 2026-10-19 · P1-F05 · `create_app` imports `app.models` after the extensions are initialised so every model is registered on the metadata before Alembic or `create_all` runs.
- 2026-10-19 · P1-F05 · Relationships to `Case` and other later tables are added with those models (F06–F08) so every relationship declares `back_populates` on both sides and no mapper references a class that does not exist yet.
- 2026-10-19 · P1-F05 · `users.preferred_language` has a CHECK limiting it to `en` or `sw`, and every foreign key out of `hospitals` and `hospital_staff` restricts deletion — the dictionary says "en or sw", and a register entry, hospital or user in use must not be deletable.
- 2026-10-19 · P1-F05 · `hospital_staff` has `created_at` only, exactly as the data dictionary lists it — changes to `is_active` are covered by the audit log rather than a second timestamp.
- 2026-10-19 · P1-F05 · `Hospital.official_email` is a read-only property that returns the register entry's email and no other source — the single code path that can supply a confirmation code destination.
- 2026-10-19 · P1-F06 · "Required when status = closed / frozen" and "required when resolving" are enforced as CHECK constraints (`closed_has_reason`, `frozen_has_reason`, `resolved_has_decision`) and added to DATABASE_DESIGN.md — principle 6 says the database enforces invariants, and the resolution note is the s.35 human-reason safeguard.
- 2026-10-19 · P1-F06 · `cases.photo_document_id` is `ON DELETE SET NULL` and created with `use_alter`; no ORM relationship is declared for it yet — the photo is a P1 feature, and a second relationship between the same two tables would need a viewonly pairing that breaks the both-sides rule.
- 2026-10-19 · P1-F06 · `Case.documents` and `CaseDocument.case` name `foreign_keys` explicitly — `cases` and `case_documents` have two foreign-key paths between them (the documents and the photo).
- 2026-10-19 · P1-F06 · `patient_ref_hmac` is covered by the partial index on non-closed, non-cancelled cases only, not by an extra plain index — the duplicate-patient lookup only ever asks about active cases.
- 2026-10-19 · P1-F06 · Patient reference hashes use `lookup_hash` with context `patient_ref` (`hash_patient_ref`) — one definition shared by the family-entered reference and the one read from the bill, so the two compare equal.
- 2026-10-19 · P1-F06 · Required JSONB columns use `none_as_null=True` — otherwise Python `None` is stored as the JSON value `null` and silently passes the NOT NULL constraint.
- 2026-10-19 · P1-F06 · `case_documents.uploaded_by_id` and `consents.granted_by_id` are plain foreign key columns with no relationship objects — the dictionary lists no navigation from them and none is needed yet.
- 2026-10-19 · P1-F07 · The dictionary's "amount required unless queried/rejected" and "finalized_at when confirmed" are CHECK constraints (`amount_required`, `confirmed_has_finalized_at`) — a confirmed bill without an amount or time must be impossible, because settlement trusts it.
- 2026-10-19 · P1-F07 · `query_note` is not forced to be present — the decision endpoint lists `note?` as optional, so the database does not demand a reason the API allows to be omitted.
- 2026-10-19 · P1-F07 · Confirmations and confirmation requests have relationships with explicit `foreign_keys` on both sides (decision code vs second-approval code, confirmer vs second approver) — each table pair has two foreign-key paths and every relationship must declare `back_populates`.
- 2026-10-19 · P1-F07 · "A threshold-sized bill needs two different staff members" stays in `confirmation_service` (P2-F09) because it depends on the hospital's configurable threshold; the database enforces only that the two approvers differ.
- 2026-10-19 · P1-F08 · `ledger_entries.prev_hash` is unique — the dictionary only makes `hash` unique, but two entries claiming the same predecessor would be a fork in a chain that must be strictly serial; the genesis entry is the only one with 64 zeros.
- 2026-10-19 · P1-F08 · `ledger_entries.seq` is an identity column `BY DEFAULT`, not `ALWAYS` — the hash covers `seq`, so `ledger_service` (P2-F10) reads the next value, computes the hash, then inserts with that explicit value.
- 2026-10-19 · P1-F08 · The model module split follows the layout: `money.py` (FxRate, Donation, Settlement, SettlementAllocation, Refund), `ledger.py`, `integration.py` — `SettlementAllocation` has a composite primary key and no `id`, as the dictionary specifies.
- 2026-10-19 · P1-F08 · Donation money is guarded by CHECKs for the $1.00 to $10,000.00 range, non-negative fee, net, allocated and refunded amounts, and `allocated + refunded <= net` — invariant 4 holds even if a service has a bug.
- 2026-10-19 · P1-F08 · No helper for setting the donor email is added to `Donation` yet — `donation_service` (P3-F03) owns capturing it and nothing earlier needs it.
- 2026-10-19 · P1-F09 · `agent_actions` has a CHECK that any status other than `proposed` has `decided_by_id` and `decided_at` — "nothing executes without approval" (PAY-11) is then enforced by the database, not only by the approve endpoint.
- 2026-10-19 · P1-F09 · `appeal_checks` has no text or image column at all and a test pins its exact column set — the pasted appeal belongs to a third party and must be impossible to store by accident.
- 2026-10-19 · P1-F09 · Whole-schema tests assert exactly 22 tables matching the design document, no never-collect column names, integer-only money columns, encrypted sensitive columns, paired relationships and explicit delete behaviour on every foreign key — Principles 2, 3 and 6 are checked against the metadata, not just by review.
- 2026-10-19 · P1-F09 · `audit_log.actor_user_id`, `agent_actions.decided_by_id` and similar user references are plain foreign key columns — no navigation from those tables to users is specified, and a relationship would need a reverse collection on `User` that nothing uses.
- 2026-10-19 · P1-F10 · The initial migration writes every enum's values out literally and creates the 30 types once before any table, with columns referencing them by name — migrations are frozen history and must not follow later edits to Python enums; a test compares the database labels with the code.
- 2026-10-19 · P1-F10 · The migration uses plain `op.create_index` instead of the autogenerated `batch_alter_table` wrappers and `sa.LargeBinary()` instead of the app's `EncryptedBytes` — batch mode is SQLite machinery, and a migration must not import application code.
- 2026-10-19 · P1-F10 · `migrations/env.py` does not call `logging.config.fileConfig` — Alembic's default would replace the application's JSON handler and disable existing loggers; Alembic's messages now flow through the same scrubbed JSON logging.
- 2026-10-19 · P1-F10 · `compare_type=True` is set on Flask-Migrate and a test fails if the models and the migrated schema differ — a model change without a migration is caught in CI.
- 2026-10-19 · P1-F10 · Up/down/up and drift tests run on a throwaway database created and dropped by the test, never on the shared test database — a failed downgrade must not break every other test.
- 2026-10-19 · P1-F10 · The pytest schema now comes from `flask_migrate.upgrade()` (replacing `create_all`), after dropping and recreating the schema of a database whose name ends in `_test` — every test runs against exactly what production gets, including the ledger trigger.
- 2026-10-19 · P1-F10 · Downgrading to base also drops the `citext` extension and the trigger function so up/down/up works on an empty database; the migration removes only what it created.
- 2026-10-19 · P1-F10 · `\dt` after `make migrate` lists 22 tables plus `alembic_version` (23 rows), not the spec's 21 — see the F02 decision on the table count.
- 2026-10-19 · P1-F11 · The existing synthetic dataset (90 invoices, 100 appeals, generator and validator) was moved into `data/synthetic/` and extended rather than rewritten — it already exceeds the spec's minimums and has a 740-check validator; changes: 25-facility register, `.test` domains, prompt-injection invoices, `appeals.jsonl`, `register_code` in labels.
- 2026-10-19 · P1-F11 · The generator stays as standalone scripts in `data/synthetic/` (not in the backend package) and `flask synthetic generate` runs it in a subprocess — it is dataset tooling with its own dependencies, and `flask synthetic validate` runs its checks.
- 2026-10-19 · P1-F11 · Hospitals 1 to 3 of the dataset were renamed to Kilele Mission Hospital, Mto Mwema Hospital and Ziwa Bluu General Hospital — the seed spec names them, and the demo hospitals must be ones that print invoices so the facility check matches.
- 2026-10-19 · P1-F11 · All 25 facility names, the 4 look-alike names and the 3 unknown-facility names were checked by web search on 2026-10-19 and none matched a real Kenyan facility; the nearest hits (a Makueni village, an unrelated Nairobi hospital with "Neema" in its name) are not exact matches.
- 2026-10-19 · P1-F11 · `facility_register.csv` is the public register only (no PayPal or settlement fields); onboarding data (settlement method, payout email) lives in the seed — payout emails default to `payouts@<hospital domain>` and `SEED_HOSPITAL_PAYOUT_EMAILS` overrides them with real sandbox accounts.
- 2026-10-19 · P1-F11 · The dataset's `jenga_stub` settlement value was dropped with the register's payment columns; the seed uses the database enum values (`paypal_payouts`, `mock_partner`).
- 2026-10-19 · P1-F11 · A minimal `ledger_service` (append with advisory lock, canonical hash, `verify_chain`) ships here because the seed needs a valid chain; P2-F10 adds per-case endpoints, concurrency tests and public verification on top.
- 2026-10-19 · P1-F11 · Entries in one `append` call share a `created_at` that is normalised to UTC with microseconds in the hashed payload — a database round trip must reproduce the same text.
- 2026-10-19 · P1-F11 · Seeded amounts come from the dataset's own bills (a case's target is its invoice balance); donation sizes are shares of the USD value of the target so every scenario (first tranche, funded, estimate cap) holds for any bill size; the live case's bill is floored at KES 240,000 so the two-person rule applies.
- 2026-10-19 · P1-F11 · Seeded documents whose raw file would still be held get a `demo/cases/...` storage key but no object yet — `storage_service` arrives in P2-F05, which will upload the dataset file for those documents, and `reset-demo` will clear the bucket prefix `demo/` then.
- 2026-10-19 · P1-F11 · `recompute_cached_totals` lives in `app/seed.py` for now: captured net and refunded USD, pledged KES (net minus refunded at each donation's rate), settled USD and KES, and donor count of captured donations; P2-F07 replaces it with `case_service.recompute_totals` and the same test asserts equality.
- 2026-10-19 · P1-F11 · Money donated to the solidarity pool stays on the donation row (no column records it); only the ledger shows the transfer — the ledger-balance test uses `held = net - allocated - refunded` less pool transfers to reconcile it.
- 2026-10-19 · P1-F11 · `flask seed` is a no-op when the admin account exists, and `flask reset-demo` refuses unless `DEMO_BANNER` is on and asks for confirmation unless `--yes` — a real environment must never be wiped by a mistyped command.
- 2026-10-19 · P1-F11 · The byte-for-byte reproducibility test takes about a minute, so it carries a `slow` marker, is skipped by default and runs in CI as `pytest -m slow`.
- 2026-10-19 · P1-F11 · Tests must not run in parallel against one test database — the session fixture rebuilds the schema, so a second pytest process drops it under the first (seen once by running the slow test in the background); CI runs one job.
- 2026-10-19 · P1-F11 · Medical lexicon has 416 terms (spec: about 300) because laboratory and imaging line items such as "Malaria parasite test" also reveal a condition; the scam-phrase lexicon flags 96 percent of scam and 6 percent of genuine labelled appeals, and a test keeps that separation above 85 and below 15 percent.
- 2026-10-19 · P1-F12 · The CI workflow was checked with actionlint and by running every backend step locally against a brand-new database with no `.env` file (lint, format, migrations up/down/up, seed and reset-demo, tests with coverage, the slow test) — Docker and `act` are not available here, and a missing default hidden by a local `.env` is the usual way a first CI run fails.
- 2026-10-19 · P1-F12 · The backend job sets the labelled development `DATA_ENCRYPTION_KEY` for the seed step (development config has no default key by design); tests use the test config, which falls back to it.
- 2026-10-19 · P1-F12 · gitleaks was run locally (v8.30.1, checksum verified) over all history: five false positives, all fictional — the development key in three files, a fake production key in a config test and a fake query token in a logging test; `.gitleaks.toml` allowlists those exact strings only, and a planted secret is still caught.
- 2026-10-19 · P1-F12 · Coverage gate is 75 percent as specified (current: 98 percent), and the slow reproducibility test runs as a separate step so a normal test run stays under a minute.
- 2026-10-19 · P1-F12 · Workflow actions are pinned to major versions (`actions/checkout@v4`, `actions/setup-python@v5`, `gitleaks/gitleaks-action@v2`) — pinning to commit SHAs is a Phase 5 hardening item; the frontend and audit jobs are added in P4-F01 and Phase 5 as specified.

</details>

## Hackathon compliance checklist (section 2)

| # | Requirement | Status after Phase 1 |
|---|---|---|
| R1 | PayPal sandbox integration | Not started (Phase 3). Mock mode and config are in place. |
| R2 | Meaningful AI | Foundations only: dataset with ground truth, lexicons, ledger. Engines are Phase 3. |
| R3 | Working prototype, hosted and runnable | Not yet. Compose file written (runtime unverified); Render files are P2-F12. |
| R4 | Documented for judges | Started: system and database design, dataset README, decisions log. Judge README is P5-F08. |
| R5 | Text description | Team task after freeze. |
| R6 | Identify AI tools used | README section is P5-F08. See the note under Missing inputs. |
| R7 | Public repo with code, assets, run instructions | Code, synthetic data and generators are committed. Making the repo public is yours. |
| R8 | Open-source licence visible | `LICENSE` (MIT) is at the root; it shows in About once pushed. |
| R9, R10 | Demo video | Team task after freeze. |
| R11 | No third-party trademarks | Fictional names, web-checked; no logos. |
| R12 | Meaningful progress during the hackathon | 12 feature commits on 19 Oct. |

## Missing inputs / blockers

1. **Docker access (needed to close P1-F01).** Docker 29.1.3 and Compose 2.40.3 are installed and you are in the
   `docker` group, but the session this work ran in predates the group change and could not reach the daemon. Until
   then, `make up` has not been run: the compose file validates (`docker compose config`) but the four services,
   the bucket bootstrap and the healthcheck commands (`mc ready local`, `/mailpit readyz`) are untested. After a
   fresh login, run `make up && docker compose ps`; if a healthcheck command is wrong, it is a one-line fix.
2. **Local stand-ins in use.** Development and tests ran against a private PostgreSQL 18 (port 55432) and Redis
   (port 56379) under `~/.local/share/tibadirect`, outside the repository. CI and compose use Postgres 16. Stop them
   with `/usr/lib/postgresql/18/bin/pg_ctl -D ~/.local/share/tibadirect/pg stop` and
   `redis-cli -p 56379 shutdown nosave`. Your `.env` (untracked) points at those ports; use the `.env.example`
   defaults once Docker works.
3. **First CI run on GitHub.** The workflow passes everything that can be checked locally; a real run needs a push.
   Please tell me the result.
4. **Licence holder.** `LICENSE` names the repository owner as copyright holder. The build rules say no individual
   names in docs; I left the legal text alone. Say if you want it changed to "TibaDirect contributors".
5. **Git author name.** Commit messages are in the team voice, but git records the configured author name on each
   commit. I may not change git config; set `git config user.name` yourself if you want a team identity.
6. **Before Phase 3:** install Tesseract (`sudo apt install tesseract-ocr`) and create the PayPal sandbox accounts
   and keys (collector, three hospital business accounts, three donor accounts, webhook ID) plus the language model
   key. Everything runs on the mock and local fallbacks until then. When the hospital sandbox accounts exist, put
   their emails in `SEED_HOSPITAL_PAYOUT_EMAILS` so the seed pays them.
7. **R6 disclosure.** The hackathon asks teams to identify the AI tools used. The README and Devpost text are the
   team's to write; decide whether to include development tooling as well as the runtime models (P5-F08).

## Known gaps / deferred items

| Item | Moves to |
|---|---|
| `make up` against a live Docker daemon | first session with Docker access (P1-F01 check) |
| `reset-demo` does not clear the bucket prefix `demo/`; seeded documents hold storage keys but no objects yet | P2-F05 |
| `recompute_cached_totals` lives in `app/seed.py` | replaced by `case_service.recompute_totals` in P2-F07 |
| Ledger: per-case public endpoints, concurrency test, global admin verify | P2-F10 |
| `constants.py` (policy settings) | first feature that reads them (P2-F09, P3-F02) |
| Swagger UI, pagination helper, CSRF, Talisman, Limiter, CORS | P2-F01 |
| Email sending (`mail_service`), sign-in, roles | P2-F02 |
| Dockerfile, Render blueprint, container check of the dataset path | P2-F12 |
| Invoice fingerprint is written by the seed with the agreed formula; the extraction step that computes it for uploads | P3-F12 |
| Swahili strings in the seed and dataset are unreviewed (marked in code) | P4-F10 |
| Workflow actions pinned to major versions, not commit SHAs; frontend and audit jobs | P4-F01, Phase 5 |
| Dataset scripts (`data/synthetic/*.py`) are not covered by backend `ruff` | decide in Phase 5 hardening |
| Tests are not parallel-safe (one shared test database) | note for any future `pytest-xdist` use |

## Next phase preview: Phase 2, core backend API (Wed 21 to Thu 22 Oct)

P2-F01 OpenAPI foundation, security headers, CSRF and rate limiting · P2-F02 email-code sign-in, sessions and
roles · P2-F03 audit logging service · P2-F04 background job queues, worker and scheduler · P2-F05 private object
storage and hardened upload validation · P2-F06 hospital directory and layered consent · P2-F07 case lifecycle
service and family endpoints · P2-F08 bill upload, submission and triage job scaffold · P2-F09 hospital
confirmation with one-time codes and two-person approval · P2-F10 hash-chained ledger and public verification ·
P2-F11 admin console API and review queue · P2-F12 production container and Render blueprint · P2-F99 review notes
(with a curl walkthrough from sign-in to a public case).

## Action for the developer

Review, push this branch, merge to `main`, check out the branch for Phase 2, then reply *proceed to Phase 2*.
