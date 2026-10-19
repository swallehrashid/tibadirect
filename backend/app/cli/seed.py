"""``flask seed`` and ``flask reset-demo``."""

import time
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db
from app.seed import is_seeded, reset_database, seed_all


def _seed_options() -> dict:
    return {
        "data_dir": Path(current_app.config["SYNTHETIC_DATA_DIR"]),
        "payout_emails": current_app.config["SEED_HOSPITAL_PAYOUT_EMAILS"],
    }


@click.command("seed")
@with_appcontext
def seed_command() -> None:
    """Load the demo world (admin, hospitals, families and eight cases) into an empty database."""
    if is_seeded(db.session):
        click.echo("The database already has demo data. Run `flask reset-demo` to start over.")
        return
    started = time.perf_counter()
    summary = seed_all(db.session, **_seed_options())
    db.session.commit()
    click.echo(f"Seeded in {time.perf_counter() - started:.1f}s: {summary}")


@click.command("reset-demo")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@with_appcontext
def reset_demo_command(yes: bool) -> None:
    """Delete ALL data and load the demo world again. Only runs where DEMO_BANNER is on."""
    if not current_app.config["DEMO_BANNER"]:
        raise click.ClickException(
            "Refusing to reset: DEMO_BANNER is off, so this does not look like a demo environment."
        )
    if not yes:
        click.confirm("This deletes every row in every table. Continue?", abort=True)
    started = time.perf_counter()
    reset_database(db.session)
    summary = seed_all(db.session, **_seed_options())
    db.session.commit()
    click.echo(f"Reset in {time.perf_counter() - started:.1f}s: {summary}")
