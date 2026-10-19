# TibaDirect

Verified medical fundraising that pays the hospital, not a stranger's phone number.

TibaDirect lets a Kenyan family raise money for a hospital bill from relatives and friends abroad.
The bill is checked, the hospital confirms it through its official register contact, donors pay
through PayPal, and the money is settled to the hospital in tranches. Donations never sit with the
family or with TibaDirect.

> **Hackathon demo.** All data is synthetic and all money is PayPal sandbox money.

## Quick start

_Placeholder. The three-command quick start is added with the judge-ready README (P5-F08)._

Local infrastructure today:

```bash
cp .env.example .env
make up        # Postgres, Redis, MinIO and Mailpit
make down
```

Mailpit (captured emails) is at http://localhost:8025 and the MinIO console at http://localhost:9001.

Demo data (all fictional):

```bash
make migrate       # create the schema
make seed          # 25 facilities, 3 hospitals with billing staff, 4 families, 8 cases
make reset-demo    # wipe everything and seed again (only where DEMO_BANNER is on)
make synthetic     # rebuild the synthetic invoices and appeals (about a minute)
```

Seed accounts sign in with an emailed code (read it in Mailpit): `admin@tibadirect.test`,
`family1@example.test` to `family4@example.test`, `clerk@kilele-hospital.test` and
`supervisor@kilele-hospital.test`. The hospital's register email is `billing@kilele-hospital.test`.

## Documentation

- [System design](docs/architecture/SYSTEM_DESIGN.md)
- [Database design](docs/database/DATABASE_DESIGN.md)
- [Synthetic dataset](data/synthetic/README.md)
- [Decisions log](docs/decisions/DECISIONS.md)

## Licence

[MIT](LICENSE)
