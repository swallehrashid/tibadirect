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

## Documentation

- [Decisions log](docs/decisions/DECISIONS.md)

## Licence

[MIT](LICENSE)
