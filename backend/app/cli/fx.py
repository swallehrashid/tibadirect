"""``flask fx refresh``."""

import click
from flask.cli import with_appcontext

from app.extensions import db
from app.services import fx_service


@click.group("fx")
def fx_group() -> None:
    """Exchange rate commands."""


@fx_group.command("refresh")
@with_appcontext
def refresh_command() -> None:
    """Fetch and store the latest USD to KES rate now (the scheduler does this every hour)."""
    row = fx_service.refresh_rates(db.session)
    db.session.commit()
    click.echo(f"USD/KES {row.rate:.8f} from {row.source}.")
