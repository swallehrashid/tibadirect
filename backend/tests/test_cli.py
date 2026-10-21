"""Command-line commands, run against a throwaway database because they commit for real."""

import re
from pathlib import Path

from flask import Flask
from sqlalchemy import text

from app.cli.seed import reset_demo_command, seed_command
from app.extensions import db


def _count(app: Flask, table: str) -> int:
    with app.app_context():
        return db.session.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()


def test_seed_loads_the_demo_world(migrated_scratch_app: Flask) -> None:
    result = migrated_scratch_app.test_cli_runner().invoke(seed_command)
    assert result.exit_code == 0, result.output
    assert re.search(
        r"Seeded in \d+\.\ds: 11 users, 25 facilities, 3 hospitals, 8 cases", result.output
    )
    assert _count(migrated_scratch_app, "cases") == 8


def test_seeding_twice_does_not_duplicate_anything(migrated_scratch_app: Flask) -> None:
    runner = migrated_scratch_app.test_cli_runner()
    runner.invoke(seed_command)
    again = runner.invoke(seed_command)
    assert again.exit_code == 0
    assert "already has demo data" in again.output
    assert _count(migrated_scratch_app, "users") == 11


def test_reset_demo_asks_before_deleting_and_aborts_on_no(migrated_scratch_app: Flask) -> None:
    runner = migrated_scratch_app.test_cli_runner()
    runner.invoke(seed_command)
    result = runner.invoke(reset_demo_command, input="n\n")
    assert result.exit_code != 0
    assert _count(migrated_scratch_app, "cases") == 8


def test_reset_demo_with_yes_rebuilds_the_same_world(migrated_scratch_app: Flask) -> None:
    runner = migrated_scratch_app.test_cli_runner()
    runner.invoke(seed_command)
    with migrated_scratch_app.app_context():
        db.session.execute(text("DELETE FROM review_items"))
        db.session.commit()
    assert _count(migrated_scratch_app, "review_items") == 0
    result = runner.invoke(reset_demo_command, ["--yes"])
    assert result.exit_code == 0, result.output
    assert re.search(r"Reset in \d+\.\ds", result.output)
    assert _count(migrated_scratch_app, "review_items") == 2
    assert _count(migrated_scratch_app, "cases") == 8


def test_reset_demo_refuses_when_this_is_not_a_demo_environment(
    migrated_scratch_app: Flask,
) -> None:
    migrated_scratch_app.config["DEMO_BANNER"] = False
    runner = migrated_scratch_app.test_cli_runner()
    runner.invoke(seed_command)
    result = runner.invoke(reset_demo_command, ["--yes"])
    assert result.exit_code != 0
    assert "DEMO_BANNER" in result.output
    assert _count(migrated_scratch_app, "cases") == 8


def test_seed_works_without_the_invoice_files(migrated_scratch_app: Flask, tmp_path) -> None:
    """Only the register is needed; bill files are fabricated when the dataset is not there."""
    source = Path(migrated_scratch_app.config["SYNTHETIC_DATA_DIR"]) / "facility_register.csv"
    (tmp_path / "facility_register.csv").write_text(source.read_text(encoding="utf-8"), "utf-8")
    migrated_scratch_app.config["SYNTHETIC_DATA_DIR"] = str(tmp_path)
    result = migrated_scratch_app.test_cli_runner().invoke(seed_command)
    assert result.exit_code == 0, result.output
    assert _count(migrated_scratch_app, "case_documents") >= 6


def test_synthetic_commands_are_registered(app: Flask) -> None:
    runner = app.test_cli_runner()
    result = runner.invoke(args=["synthetic", "--help"])
    assert result.exit_code == 0
    assert "generate" in result.output
    assert "validate" in result.output


def test_synthetic_validate_runs_the_datasets_checks(app: Flask) -> None:
    result = app.test_cli_runner().invoke(args=["synthetic", "validate"])
    assert result.exit_code == 0, result.output


def test_synthetic_generate_builds_the_expected_command(app: Flask, monkeypatch, tmp_path) -> None:
    calls = []

    def fake_run(command, cwd, check):
        calls.append((command, Path(cwd)))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("app.cli.synthetic.subprocess.run", fake_run)
    out = tmp_path / "rebuilt"
    result = app.test_cli_runner().invoke(
        args=["synthetic", "generate", "--out", str(out), "--seed", "7", "--scale", "2"]
    )
    assert result.exit_code == 0, result.output
    [(command, cwd)] = calls
    assert command[1:] == ["generate.py", "--out", str(out), "--seed", "7", "--scale", "2"]
    assert cwd == Path(app.config["SYNTHETIC_DATA_DIR"]) / "generator"


def test_synthetic_generate_reports_a_failing_generator(app: Flask, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.synthetic.subprocess.run",
        lambda *a, **k: type("Result", (), {"returncode": 1})(),
    )
    result = app.test_cli_runner().invoke(args=["synthetic", "generate"])
    assert result.exit_code != 0
    assert "generator failed" in result.output


def test_synthetic_validate_reports_failing_checks(app: Flask, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.cli.synthetic.subprocess.run",
        lambda *a, **k: type("Result", (), {"returncode": 1})(),
    )
    result = app.test_cli_runner().invoke(args=["synthetic", "validate"])
    assert result.exit_code != 0
    assert "failing checks" in result.output
