"""The evaluation harness: the arithmetic of the report, the isolation of the run, the command."""

from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.ai import evaluate
from app.ai.evaluate import DocRun, Evaluation, Rate
from app.cli.ai import ai_group
from app.extensions import db
from app.models.case import Case, CaseDocument
from app.models.enums import RiskBand, TriageRoute
from tests.ai.helpers import DATA_DIR, needs_dataset


def run(doc_id: str, label: str, *, split: str = "dev", codes=(), expected=(), **kw) -> DocRun:  # type: ignore[no-untyped-def]
    return DocRun(
        doc_id=doc_id,
        split=split,
        capture=kw.pop("capture", "digital_pdf"),
        label=label,
        tamper_type=kw.pop("tamper_type", ""),
        expected=set(expected),
        codes=set(codes),
        **kw,
    )


# --------------------------------------------------------------------------- the arithmetic


def test_a_rate_is_precision_and_recall_or_nothing_when_there_is_nothing_to_divide() -> None:
    assert Rate(tp=3, fp=1, fn=2).precision == 0.75
    assert Rate(tp=3, fp=1, fn=2).recall == 0.6
    assert Rate().precision is None
    assert Rate().recall is None
    assert Rate(fp=2).precision == 0.0


def test_signals_are_counted_against_the_dataset_names_through_the_map() -> None:
    runs = [
        run("A", "tampered", expected={"total_mismatch"}, codes={"totals_mismatch"}),
        run("B", "tampered", expected={"total_mismatch"}, codes=set()),
        run("C", "genuine", codes={"totals_mismatch"}),
        run("D", "genuine"),
    ]

    rate = evaluate.signal_rates(runs, "all")["total_mismatch"]

    assert (rate.tp, rate.fp, rate.fn) == (1, 1, 1)


def test_a_dataset_name_answered_by_two_codes_counts_either_one() -> None:
    runs = [
        run("A", "tampered", expected={"metadata_edited"}, codes={"pdf_modified_after_creation"}),
        run("B", "tampered", expected={"metadata_edited"}, codes={"pdf_editor_software"}),
    ]

    assert evaluate.signal_rates(runs, "all")["metadata_edited"].tp == 2


def test_a_signal_with_no_detector_never_counts_as_raised() -> None:
    runs = [run("A", "tampered", expected={"metadata_generic_producer"}, codes={"anything"})]

    rate = evaluate.signal_rates(runs, "all")["metadata_generic_producer"]

    assert (rate.tp, rate.fp, rate.fn) == (0, 0, 1)


def test_the_held_out_split_leaves_out_development_bills_and_unscored_ones() -> None:
    runs = [
        run(
            "A",
            "tampered",
            split="test",
            expected={"overlay_box"},
            codes={"hidden_text_under_overlay"},
        ),
        run("B", "tampered", split="dev", expected={"overlay_box"}),
        run("C", "tampered", split="test", expected={"overlay_box"}, skipped="needs OCR"),
    ]

    held = evaluate.signal_rates(runs, "test")["overlay_box"]
    everything = evaluate.signal_rates(runs, "all")["overlay_box"]

    assert (held.tp, held.fn) == (1, 0)
    assert (everything.tp, everything.fn) == (1, 1)


def test_the_headline_numbers_count_the_right_bills() -> None:
    runs = [
        run("T1", "tampered", codes={"totals_mismatch"}, score=25),
        run(
            "T2",
            "tampered",
            codes={"hidden_text_under_overlay", "totals_mismatch"},
            score=90,
            band=RiskBand.HIGH,
            route=TriageRoute.MANUAL_REVIEW,
        ),
        run("T3", "tampered"),
        run("D1", "duplicate", route=TriageRoute.MANUAL_REVIEW, band=RiskBand.HIGH),
        run("D2", "duplicate"),
        run("G1", "genuine"),
        run("G2", "genuine", route=TriageRoute.MANUAL_REVIEW),
        run("T4", "tampered", skipped="needs OCR"),
    ]

    numbers = evaluate.headline(runs, "all")

    assert numbers["tampered_signalled"] == (2, 3)
    assert numbers["tampered_flagged"] == (1, 3)
    assert numbers["duplicates_caught"] == (1, 2)
    assert numbers["genuine_left_alone"] == (1, 2)


def test_the_routing_matrix_counts_routes_and_bands_by_true_label() -> None:
    runs = [
        run("G", "genuine"),
        run("T", "tampered", route=TriageRoute.MANUAL_REVIEW, band=RiskBand.HIGH),
        run("T2", "tampered", band=RiskBand.MEDIUM),
    ]

    matrix = evaluate.routing_matrix(runs, "all")

    assert matrix["genuine"]["hospital_confirmation"] == 1
    assert matrix["tampered"]["manual_review"] == 1
    assert matrix["tampered"]["band:medium"] == 1
    assert matrix["duplicate"] == {}


def test_failures_name_each_bill_that_went_the_wrong_way() -> None:
    runs = [
        run("OK", "genuine"),
        run("BAD1", "genuine", route=TriageRoute.MANUAL_REVIEW),
        run("BAD2", "duplicate"),
        run("BAD3", "tampered", tamper_type="image_splice"),
        run("BAD4", "tampered", tamper_type="metadata_only", codes={"pdf_editor_software"}),
        run("FINE", "tampered", codes={"x"}, band=RiskBand.MEDIUM),
        run("SKIP", "duplicate", skipped="needs OCR"),
    ]

    found = {r.doc_id: why for r, why in evaluate.failures(runs)}

    assert set(found) == {"BAD1", "BAD2", "BAD3", "BAD4"}
    assert "nothing raised" in found["BAD3"]
    assert "stayed low" in found["BAD4"]


def test_amounts_are_compared_as_numbers_not_text() -> None:
    from decimal import Decimal

    from app.ai.extract import ExtractedBill

    bill = ExtractedBill(
        facility_name="Kilele Mission Hospital",
        invoice_number="KMH/26/1",
        document_type="invoice",
        currency="KES",
        total_kes=Decimal("85000.00"),
        balance_due_kes=Decimal("81000.00"),
        deposit_kes=None,
    )
    row = {"invoice_number_shown": "KMH/26/1", "total_shown": "85000", "balance_shown": "81000"}
    truth = {
        "fields": {
            "facility": "Kilele Mission Hospital",
            "invoice_date": "None",
            "document_type": "invoice",
            "currency": "KES",
            "deposit_paid": 0,
        }
    }

    matches = evaluate.field_matches(bill, row, truth)

    assert matches["total"] is True
    assert matches["balance_due"] is True
    assert matches["deposit"] is True  # no deposit read, none paid


# --------------------------------------------------------------------------- the report text


def make_evaluation(**kw) -> Evaluation:  # type: ignore[no-untyped-def]
    runs = [
        run("A", "genuine", split="test"),
        run("B", "tampered", split="test", tamper_type="overlay_total", codes={"totals_mismatch"}),
        run("P", "tampered", capture="phone_photo", skipped="needs OCR"),
    ]
    empty = {"hits": {"pdf": {}, "photo": {}}, "counts": {"pdf": 0, "photo": 0}, "skipped": 0}
    from collections import Counter

    empty["hits"] = {"pdf": Counter(), "photo": Counter()}
    empty["counts"] = Counter()
    return Evaluation(
        runs=runs,
        signals={s: evaluate.signal_rates(runs, s) for s in evaluate.SPLITS},
        extraction={"local": empty},
        appeals={
            "version": "evaluation",
            "n_train": 66,
            "dev": {"n": 66, "precision": 1.0, "recall": 1.0, "tp": 1, "fp": 0, "fn": 0, "tn": 1},
            "test": {"n": 34, "precision": 0.9, "recall": 0.8, "tp": 9, "fp": 1, "fn": 2, "tn": 22},
            "hard_test": None,
        },
        ocr_version=kw.get("ocr_version"),
        llm_model=kw.get("llm_model"),
        data_version="92 documents, labels abc",
        run_date="2026-10-22",
    )


def test_the_report_says_what_was_and_was_not_run() -> None:
    report = evaluate.render_report(make_evaluation())

    assert report.startswith("# AI evaluation report")
    assert "2026-10-22" in report
    assert "92 documents, labels abc" in report
    assert "OCR not installed here" in report
    assert "not used (no key set)" in report
    assert "Bills scored: 2 of 3" in report
    assert "were **not scored** because Tesseract is not installed" in report
    assert "not measured" in report  # the model path


def test_the_report_names_the_model_and_the_ocr_when_they_ran() -> None:
    report = evaluate.render_report(
        make_evaluation(ocr_version="Tesseract 5.5.0", llm_model="a-model")
    )

    assert "OCR Tesseract 5.5.0" in report
    assert "Language model: a-model" in report
    assert "were **not scored**" in report  # the photo is still marked skipped in this fixture


def test_the_report_has_every_section_and_the_failures_table() -> None:
    report = evaluate.render_report(make_evaluation())

    for heading in (
        "## Against the specification's targets",
        "## Extraction field accuracy",
        "## Per-signal precision and recall",
        "## Routing",
        "## Where it fails",
        "## Appeal checker",
        "## Read this before quoting a number",
    ):
        assert heading in report
    assert "| B | digital_pdf | overlay_total, raised but stayed low |" in report
    assert "*not separate: counted under `metadata_edited`*" in report


# --------------------------------------------------------------------------- the isolation


def test_the_run_happens_in_a_schema_that_is_dropped_and_touches_no_real_table(db_session) -> None:  # type: ignore[no-untyped-def]
    before = db.session.scalar(select(func.count()).select_from(CaseDocument))

    with evaluate.isolated_session() as session:
        schema = session.scalar(text("SELECT current_schema()"))
        assert schema.startswith("evaluation_")
        assert session.scalar(select(func.count()).select_from(Case)) == 0
        session.execute(text("SELECT 1 FROM case_documents LIMIT 1"))

    assert db.session.scalar(select(func.count()).select_from(CaseDocument)) == before
    left = db.session.scalar(
        text("SELECT count(*) FROM information_schema.schemata WHERE schema_name = :s"),
        {"s": schema},
    )
    assert left == 0


def test_a_failure_inside_the_run_still_drops_the_schema(db_session) -> None:  # type: ignore[no-untyped-def]
    names: list[str] = []

    def fail() -> None:
        with evaluate.isolated_session() as session:
            names.append(session.scalar(text("SELECT current_schema()")))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fail()

    assert (
        db.session.scalar(
            text("SELECT count(*) FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": names[0]},
        )
        == 0
    )


# --------------------------------------------------------------------------- the real thing


@pytest.fixture
def no_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever this machine has, the run below scores the digital PDFs only."""
    monkeypatch.setattr(evaluate.shutil, "which", lambda name: None)


@needs_dataset
def test_the_digital_bills_run_end_to_end_and_the_numbers_hold(db_session, no_ocr) -> None:  # type: ignore[no-untyped-def]
    result = evaluate.evaluate(DATA_DIR)

    scored = [r for r in result.runs if r.scored]
    assert len(result.runs) == 92
    assert 45 <= len(scored) <= 49
    assert all(r.capture == "digital_pdf" for r in scored)
    assert result.ocr_version is None
    # every genuine digital bill is left for the hospital, every reused one reaches a person
    numbers = evaluate.headline(result.runs, "all")
    assert numbers["genuine_left_alone"][0] == numbers["genuine_left_alone"][1] > 0
    assert numbers["duplicates_caught"][0] == numbers["duplicates_caught"][1] > 0
    # extraction on the digital bills is exact
    local = result.extraction["local"]
    assert sum(local["hits"]["pdf"].values()) == local["counts"]["pdf"] * len(evaluate.FIELDS)
    assert local["counts"]["photo"] == 0
    assert result.appeals["test"]["precision"] >= 0.85
    # a copy whose original was not scored is left out, not counted as a miss
    orphans = [r for r in result.runs if r.skipped == "original not scored"]
    assert orphans
    assert all(r.label == "duplicate" and not r.scored for r in orphans)
    # nothing was left in the real tables
    assert db.session.scalar(select(func.count()).select_from(CaseDocument)) == 0


@needs_dataset
def test_the_command_writes_the_report(app, db_session, no_ocr, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "nested" / "REPORT.md"

    result = app.test_cli_runner().invoke(ai_group, ["evaluate", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "Evaluated" in result.output
    report = out.read_text(encoding="utf-8")
    assert report.startswith("# AI evaluation report")
    assert "## Where it fails" in report
