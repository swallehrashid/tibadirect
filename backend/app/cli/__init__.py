"""Flask CLI commands: ``seed``, ``reset-demo`` and the storage, synthetic and fx groups."""

from flask import Flask

from app.cli.fx import fx_group
from app.cli.seed import reset_demo_command, seed_command
from app.cli.storage import storage_group
from app.cli.synthetic import synthetic_group


def register_cli(app: Flask) -> None:
    app.cli.add_command(seed_command)
    app.cli.add_command(reset_demo_command)
    app.cli.add_command(synthetic_group)
    app.cli.add_command(storage_group)
    app.cli.add_command(fx_group)
