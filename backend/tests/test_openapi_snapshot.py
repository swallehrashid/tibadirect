"""``docs/api/openapi.json`` is the published API description; it must match the code."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_committed_snapshot_matches_the_running_api() -> None:
    env = {**os.environ, "APP_ENV": "development"}
    result = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "wsgi", "openapi", "print", "--format", "json"],
        cwd=ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    current = json.loads(result.stdout)
    committed = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text())
    assert current == committed, "the API changed: run `make openapi` and commit the new snapshot"


def test_the_snapshot_describes_every_route_group_and_no_test_routes() -> None:
    committed = json.loads((ROOT / "docs" / "api" / "openapi.json").read_text())
    paths = set(committed["paths"])
    for prefix in ("/api/auth", "/api/cases", "/api/hospital", "/api/admin", "/api/public"):
        assert any(path.startswith(prefix) for path in paths), prefix
    assert not any("_test" in path for path in paths)
    assert committed["openapi"] == "3.0.3"
