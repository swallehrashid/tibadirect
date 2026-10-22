"""``flask storage bootstrap``."""

import click
from flask import current_app
from flask.cli import with_appcontext

from app.services import storage_service


@click.group("storage")
def storage_group() -> None:
    """Object storage commands."""


@storage_group.command("bootstrap")
@with_appcontext
def bootstrap_command() -> None:
    """Create the private upload bucket if it is missing."""
    bucket = current_app.config["S3_BUCKET"]
    try:
        created = storage_service.ensure_bucket()
    except storage_service.StorageError as exc:
        raise click.ClickException(f"Could not reach object storage: {exc.message}") from exc
    click.echo(f"Bucket {bucket} {'created' if created else 'already exists'} and is private.")
