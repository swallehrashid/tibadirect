# TibaDirect

Verified medical fundraising that pays the hospital, not a stranger's phone number.

TibaDirect lets a Kenyan family raise money for a hospital bill from relatives and friends abroad.
The bill is checked, the hospital confirms it through its official register contact, donors pay
through PayPal, and the money is settled to the hospital in tranches. Donations never sit with the
family or with TibaDirect.

> **Hackathon demo.** All data is synthetic and all money is PayPal sandbox money.

## Repository layout

```text
.
├── backend/                 Flask API and background jobs (Python 3.12)
│   ├── app/
│   │   ├── api/             HTTP routes (thin: validate, call a service, serialise)
│   │   ├── services/        business rules (ledger, health, ...)
│   │   ├── models/          SQLAlchemy models for the 22 PostgreSQL tables
│   │   ├── schemas/         request and response schemas (OpenAPI)
│   │   ├── security/        encryption, keyed hashing, masking
│   │   ├── cli/             flask seed, reset-demo, synthetic
│   │   └── seed.py          the fictional demo world
│   ├── migrations/          Alembic migrations (schema, enums, ledger trigger)
│   └── tests/               pytest suite (runs against PostgreSQL)
├── data/
│   ├── synthetic/           fictional invoices, appeals, facility register and labels
│   │   └── generator/       scripts that build and validate the dataset
│   └── lexicons/            medical terms, editor software, scam phrases
├── docker/                  container init scripts
├── docker-compose.yml       local PostgreSQL, Redis, MinIO and Mailpit
├── Makefile                 shortcuts for the commands below
└── .env.example             every setting, with safe development defaults
```

## Quick start

```bash
cp .env.example .env
make up                      # PostgreSQL, Redis, MinIO and Mailpit

python3.12 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt

make migrate                 # create the schema
make seed                    # load the fictional demo world
make test                    # run the test suite
```

Mailpit (captured emails) is at http://localhost:8025 and the MinIO console at http://localhost:9001.
`make reset-demo` wipes the database and seeds it again; it only runs where `DEMO_BANNER` is on.

Seed accounts sign in with an emailed code (read it in Mailpit): `admin@tibadirect.test`,
`family1@example.test` to `family4@example.test`, `clerk@kilele-hospital.test` and
`supervisor@kilele-hospital.test`. The hospital's register email is `billing@kilele-hospital.test`.

## Synthetic data

Everything in `data/` is invented: no real patients, documents, hospitals or money.

- **Facility register:** 25 fictional facilities with reserved `.test` email addresses. Every name was
  checked by web search, and none matches a real Kenyan facility.
- **Bills:** 92 invoices and estimates (36 genuine, 32 tampered, 24 duplicates), including two that
  carry hidden instructions aimed at software. `labels.csv` and `ground_truth.jsonl` hold the answers.
- **Appeals:** 100 forwarded fundraising messages (50 genuine, 50 scam) in English, Swahili and
  code-switched text.

`make synthetic` rebuilds the dataset; the same seed reproduces every file byte for byte.
`flask synthetic validate` (from `backend/`) runs the dataset's consistency checks.

## Licence

[MIT](LICENSE)
