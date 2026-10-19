"""Flask CLI commands: ``seed``, ``reset-demo`` and the ``synthetic`` group."""

from flask import Flask

from app.cli.seed import reset_demo_command, seed_command
from app.cli.synthetic import synthetic_group


def register_cli(app: Flask) -> None:
    app.cli.add_command(seed_command)
    app.cli.add_command(reset_demo_command)
    app.cli.add_command(synthetic_group)
