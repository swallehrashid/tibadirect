"""The committed synthetic dataset and lexicons: counts, coverage and consistency."""

import csv
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from app.synthetic_data import (
    DEFAULT_DATA_DIR,
    load_appeals,
    load_documents,
    load_register,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = DEFAULT_DATA_DIR
LEXICONS = ROOT / "data" / "lexicons"


def _lexicon(name: str) -> list[str]:
    lines = (LEXICONS / name).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _catalog():
    spec = importlib.util.spec_from_file_location("synthetic_catalog", DATA / "catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hospitals():
    sys.path.insert(0, str(DATA))
    try:
        import hospitals  # noqa: PLC0415

        return hospitals
    finally:
        sys.path.remove(str(DATA))
        sys.modules.pop("hospitals", None)


# ---- facility register ----


def test_the_register_has_25_distinct_fictional_facilities() -> None:
    rows = load_register()
    assert len(rows) == 25
    assert len({r.register_code for r in rows}) == 25
    assert len({r.name for r in rows}) == 25
    assert len({r.official_email for r in rows}) == 25
    assert all(r.official_email.endswith(".test") for r in rows)
    assert all(r.source == "synthetic-register-v1" for r in rows)
    assert all(re.fullmatch(r"FR-[A-Z]{3}-\d{4}", r.register_code) for r in rows)


def test_the_demo_hospitals_are_in_the_register_with_the_documented_emails() -> None:
    by_name = {r.name: r for r in load_register()}
    assert by_name["Kilele Mission Hospital"].official_email == "billing@kilele-hospital.test"
    assert "Mto Mwema Hospital" in by_name
    assert "Ziwa Bluu General Hospital" in by_name


def test_register_phone_numbers_use_the_fictional_555_exchange() -> None:
    assert all(re.fullmatch(r"\+254 \d{2} 555 \d{4}", r.official_phone) for r in load_register())


# ---- invoices and labels ----


def test_labels_cover_every_generated_file_and_nothing_else() -> None:
    with open(DATA / "labels.csv", newline="", encoding="utf-8") as handle:
        labelled = {row["filename"] for row in csv.DictReader(handle)}
    on_disk = {p.name for p in (DATA / "invoices").iterdir()}
    assert labelled == on_disk


def test_ground_truth_lists_the_same_documents_as_the_labels() -> None:
    with open(DATA / "ground_truth.jsonl", encoding="utf-8") as handle:
        truth = {json.loads(line)["doc_id"] for line in handle}
    assert truth == {d.doc_id for d in load_documents()}


def test_the_dataset_has_the_required_mix() -> None:
    documents = load_documents()
    assert len(documents) >= 60
    mix = Counter(d.label for d in documents)
    assert mix["genuine"] >= 30
    assert mix["tampered"] >= 20
    assert mix["duplicate"] >= 10


def test_there_are_prompt_injection_invoices_with_their_own_signal() -> None:
    with open(DATA / "labels.csv", newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r["tamper_type"] == "prompt_injection"]
    assert 1 <= len(rows) <= 2
    assert all(r["expected_signals"] == "instruction_like_text" for r in rows)
    assert all(r["filename"].endswith(".pdf") for r in rows)


def test_every_genuine_invoice_comes_from_a_register_facility() -> None:
    names = {r.name for r in load_register()}
    genuine = [d for d in load_documents() if d.label == "genuine"]
    assert genuine
    assert all(d.facility in names for d in genuine)


def test_every_invoicing_hospital_is_in_the_register() -> None:
    register = {r.register_code: r for r in load_register()}
    for hospital in _hospitals().HOSPITALS:
        entry = register[hospital["register_code"]]
        assert entry.name == hospital["name"]
        assert entry.official_email == hospital["official_email"]


def test_files_are_small_enough_to_upload() -> None:
    assert all(d.size_bytes <= 10 * 1024 * 1024 for d in load_documents())


def test_the_datasets_own_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, "validate.py"], cwd=DATA, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout[-2000:]
    assert " 0 failed" in result.stdout


# ---- appeals ----


def test_there_are_at_least_100_labelled_appeals_in_three_language_styles() -> None:
    appeals = load_appeals()
    assert len(appeals) >= 100
    assert {a["label"] for a in appeals} == {"genuine", "scam"}
    assert {a["language"] for a in appeals} >= {"en", "sw", "mixed"}
    assert len({a["text"] for a in appeals}) == len(appeals)
    assert len({a["appeal_id"] for a in appeals}) == len(appeals)


def test_appeal_screenshots_exist() -> None:
    shots = [a["screenshot_file"] for a in load_appeals() if a["screenshot_file"]]
    assert shots
    assert all((DATA / shot).exists() for shot in shots)


# ---- lexicons ----


def test_the_medical_lexicon_is_large_and_in_lowercase() -> None:
    terms = _lexicon("medical_terms.txt")
    assert len(terms) >= 300
    assert len(terms) == len(set(terms))
    assert all(term == term.lower() for term in terms)


def test_the_medical_lexicon_covers_every_clinical_string_the_invoices_print() -> None:
    catalog = _catalog()
    lexicon = set(_lexicon("medical_terms.txt"))
    printed = {s["diagnosis"] for s in catalog.SCENARIOS.values()}
    printed |= {s["procedure"] for s in catalog.SCENARIOS.values()}
    printed |= {catalog.PACKAGE["diagnosis"], catalog.PACKAGE["procedure"]}
    printed |= {item["term"] for item in catalog.CATALOG.values() if item.get("term")}
    missing = sorted(t for t in printed if t.lower() not in lexicon)
    assert missing == []


def test_editor_software_lexicon_flags_the_datasets_editors_but_not_billing_systems() -> None:
    editors = [e.lower() for e in _lexicon("editor_software.txt")]
    generate = (DATA / "generate.py").read_text(encoding="utf-8")
    for constant in ("EDITOR", "GENERIC"):
        value = re.search(rf'^{constant} = "([^"]+)"', generate, flags=re.MULTILINE).group(1)
        assert any(e in value.lower() for e in editors), value
    for system in _hospitals().BILLING_SYSTEMS:
        assert not any(e in system.lower() for e in editors), system


def test_scam_phrases_are_categorised() -> None:
    lines = _lexicon("scam_phrases.txt")
    categories = Counter(line.split(": ", 1)[0] for line in lines)
    assert len(lines) >= 50
    assert set(categories) == {
        "urgency",
        "personal_payee",
        "send_to_number",
        "advance_fee",
        "secrecy",
        "verification_refusal",
        "impersonation",
        "pressure_to_share",
        "vague_story",
    }


def test_scam_phrases_separate_the_labelled_appeals() -> None:
    phrases = [line.split(": ", 1)[1].lower() for line in _lexicon("scam_phrases.txt")]
    appeals = load_appeals()

    def flagged(label: str) -> float:
        subset = [a for a in appeals if a["label"] == label]
        hits = [a for a in subset if any(p in a["text"].lower() for p in phrases)]
        return len(hits) / len(subset)

    assert flagged("scam") >= 0.85
    assert flagged("genuine") <= 0.15


# ---- reproducibility (about a minute, so it runs separately: pytest -m slow) ----


@pytest.mark.slow
def test_the_same_seed_rebuilds_the_committed_dataset_byte_for_byte(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "generate.py", "--out", str(tmp_path), "--seed", "42"],
        cwd=DATA,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    def digests(base: Path) -> dict[str, str]:
        files = [p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        return {
            str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
            if p.suffix in {".csv", ".jsonl", ".pdf", ".png", ".jpg"}
        }

    rebuilt, committed = digests(tmp_path), digests(DATA)
    assert rebuilt.keys() == committed.keys()
    assert {k for k in rebuilt if rebuilt[k] != committed[k]} == set()
