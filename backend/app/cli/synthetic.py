"""``flask synthetic``: generate and validate the synthetic dataset in ``data/synthetic``."""

import subprocess
import sys
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext


def _data_dir() -> Path:
    return Path(current_app.config["SYNTHETIC_DATA_DIR"])


@click.group("synthetic")
def synthetic_group() -> None:
    """Synthetic invoices, appeals and the facility register (all fictional)."""


@synthetic_group.command("generate")
@click.option(
    "--out",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Output directory (default: the dataset directory).",
)
@click.option("--seed", type=int, default=42, show_default=True, help="Random seed.")
@click.option("--scale", type=int, default=1, show_default=True, help="Multiply document counts.")
@with_appcontext
def generate_command(out: Path | None, seed: int, scale: int) -> None:
    """Rebuild the dataset. The same seed always gives the same files."""
    data_dir = _data_dir()
    target = (out or data_dir).resolve()
    command = [
        sys.executable,
        "generate.py",
        "--out",
        str(target),
        "--seed",
        str(seed),
        "--scale",
        str(scale),
    ]
    click.echo(f"Generating into {target} (seed {seed}, scale {scale}) ...")
    result = subprocess.run(command, cwd=data_dir, check=False)
    if result.returncode:
        raise click.ClickException("The generator failed.")


@synthetic_group.command("validate")
@click.option("--ocr", type=int, default=0, help="Also OCR this many raster invoices.")
@with_appcontext
def validate_command(ocr: int) -> None:
    """Run the dataset's consistency checks."""
    data_dir = _data_dir()
    command = [sys.executable, "validate.py", *(["--ocr", str(ocr)] if ocr else [])]
    result = subprocess.run(command, cwd=data_dir, check=False)
    if result.returncode:
        raise click.ClickException("The dataset has failing checks.")
