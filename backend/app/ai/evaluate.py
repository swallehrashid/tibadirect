"""The evaluation harness (specification 7.11): every check against the labelled synthetic set.

``flask ai evaluate`` runs the real triage pipeline over the 92 synthetic bills in submission
order, scores the appeal checker on its messages, and writes ``docs/evaluation/REPORT.md``:

* per-signal precision and recall, with the dataset's signal names mapped to this engine's codes;
* the routing confusion matrix, tampered bills flagged and reused invoices caught;
* extraction field accuracy, for the local reader and (only when a key is set) the language
  model path, kept apart;
* appeal checker precision and recall on the messages it was not trained on.

The bills run in a throwaway schema of the application's database, dropped afterwards, so real
submissions are never seen and nothing is left behind. Photographs and scans need Tesseract;
without it they are counted and left out, never silently scored as misses.
"""

import hashlib
import shutil
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from flask import current_app
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.ai import appeals, extract, llm, pipeline, text
from app.extensions import db
from app.models.case import Case, CaseDocument, hash_patient_ref
from app.models.enums import DocumentKind, RiskBand, TriageRoute, UserRole
from app.models.hospital import FacilityRegisterEntry, Hospital, normalize_facility_name
from app.models.identity import User
from app.synthetic_data import RegisterRow, load_register

SPLITS = ("test", "all")

# The dataset's signal name -> the codes of this engine that answer it. An empty tuple means
# nothing here looks for it; the report says so instead of dropping the row.
SIGNAL_MAP: dict[str, tuple[str, ...]] = {
    "duplicate_exact_hash": ("duplicate_exact",),
    "duplicate_perceptual": ("duplicate_near",),
    "duplicate_fingerprint": ("duplicate_fingerprint",),
    "total_mismatch": ("totals_mismatch",),
    "line_math_mismatch": ("line_math_mismatch",),
    "overlay_box": ("hidden_text_under_overlay",),
    "hidden_text_under_overlay": ("hidden_text_under_overlay",),
    "font_inconsistent": ("font_inconsistency",),
    "metadata_edited": ("pdf_editor_software", "pdf_modified_after_creation"),
    "metadata_generic_producer": (),
    "incremental_update": ("pdf_incremental_updates",),
    "noise_inconsistency": ("image_ela_high",),
    "contact_mismatch": ("contact_mismatch",),
    "facility_unknown": ("facility_not_in_register",),
    "facility_fuzzy_match": ("facility_code_mismatch", "facility_weak_match"),
    "instruction_like_text": ("instruction_like_text",),
}
# Dataset signals this engine does not tell apart from another one, and why.
NOT_SEPARATE = {
    "metadata_generic_producer": "*not separate: counted under `metadata_edited`*",
}
DUPLICATE_CODES = ("duplicate_exact", "duplicate_near", "duplicate_fingerprint")
EXPECTED_ROUTE = {
    "genuine": TriageRoute.HOSPITAL_CONFIRMATION,
    "tampered": TriageRoute.MANUAL_REVIEW,
    "duplicate": TriageRoute.MANUAL_REVIEW,
}
# Targets from the specification (7.11): the first stage ("MVP") and the final one.
TARGETS = {
    "extraction": (0.85, 0.90),
    "tampered_signalled": (0.70, 0.85),
    "tampered_flagged": (0.70, 0.85),
    "duplicates_caught": (1.0, 1.0),
    "appeal_precision": (0.85, 0.85),
}
FIELDS = (
    "facility",
    "invoice_number",
    "invoice_date",
    "document_type",
    "currency",
    "total",
    "balance_due",
    "deposit",
)


@dataclass
class DocRun:
    """One bill's labels and what the pipeline made of it."""

    doc_id: str
    split: str
    capture: str
    label: str
    tamper_type: str
    expected: set[str]
    skipped: str | None = None
    codes: set[str] = field(default_factory=set)
    score: int = 0
    band: RiskBand = RiskBand.LOW
    route: TriageRoute = TriageRoute.HOSPITAL_CONFIRMATION

    @property
    def scored(self) -> bool:
        return self.skipped is None

    @property
    def is_pdf(self) -> bool:
        return self.capture == "digital_pdf"


@dataclass(frozen=True)
class Rate:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


@dataclass
class Evaluation:
    """Everything the report is made of."""

    runs: list[DocRun]
    signals: dict[str, dict[str, Rate]]  # split -> dataset signal -> rate
    extraction: dict[str, dict[str, Any]]  # "local" / "llm" -> result
    appeals: dict[str, Any]
    ocr_version: str | None
    llm_model: str | None
    data_version: str
    run_date: str


# --------------------------------------------------------------------------- reading the data


def _read_labels(data_dir: Path) -> list[dict[str, str]]:
    import csv

    with (data_dir / "labels.csv").open(encoding="utf-8", newline="") as handle:
        return sorted(csv.DictReader(handle), key=lambda r: (r["submitted_at"], r["doc_id"]))


def _read_truth(data_dir: Path) -> dict[str, dict[str, Any]]:
    import json

    with (data_dir / "ground_truth.jsonl").open(encoding="utf-8") as handle:
        return {row["doc_id"]: row for row in map(json.loads, handle) if row}


def _read_file(data_dir: Path, filename: str) -> tuple[bytes, str]:
    path = data_dir / "invoices" / filename
    suffix = path.suffix.lower()
    mime = {"pdf": "application/pdf", "png": "image/png"}.get(suffix[1:], "image/jpeg")
    return path.read_bytes(), mime


def dataset_version(data_dir: Path) -> str:
    """A short fingerprint of the labels, so a report names the exact data it was run on."""
    digest = hashlib.sha256((data_dir / "labels.csv").read_bytes()).hexdigest()[:12]
    count = len(_read_labels(data_dir))
    return f"{count} documents, labels {digest}"


def ocr_version() -> str | None:
    if shutil.which("tesseract") is None:
        return None
    import pytesseract

    try:
        return f"Tesseract {pytesseract.get_tesseract_version()}"
    except Exception:  # noqa: BLE001  # a wrapper that cannot report a version still reads text
        return "Tesseract"


# --------------------------------------------------------------------------- the triage run


def _hospital_for(session: Session, row: RegisterRow | None, fallback_number: int) -> Hospital:
    """The hospital a family would have chosen: one from the register, or a stand-in."""
    if row is None:
        entry = FacilityRegisterEntry(
            register_code=f"EVAL-{uuid.uuid4().hex[:8]}",
            name=f"Evaluation Stand-in Hospital {fallback_number}",
            normalized_name=f"evaluation stand in hospital {fallback_number}",
            county="Nairobi",
            official_email=f"billing{fallback_number}@evaluation.test",
            source="evaluation",
        )
        session.add(entry)
    else:
        entry = session.scalar(
            select(FacilityRegisterEntry).where(
                FacilityRegisterEntry.register_code == row.register_code
            )
        )
        if entry is None:
            entry = FacilityRegisterEntry(
                register_code=row.register_code,
                name=row.name,
                normalized_name=normalize_facility_name(row.name),
                county=row.county,
                official_email=row.official_email,
                official_phone=row.official_phone,
                source="evaluation",
            )
            session.add(entry)
    session.flush()
    if entry.hospital is None:
        session.add(
            Hospital(register_entry_id=entry.id, display_name=entry.name, county=entry.county)
        )
        session.flush()
        session.refresh(entry)
    return entry.hospital


def run_triage(session: Session, data_dir: Path, *, with_ocr: bool) -> list[DocRun]:
    """Triage every bill in submission order. The caller rolls the session back afterwards."""
    register = {r.register_code: r for r in load_register(data_dir)}
    user = User(
        email=f"evaluation-{uuid.uuid4().hex[:8]}@example.test",
        display_name="Evaluation",
        role=UserRole.FAMILY,
    )
    session.add(user)
    session.flush()

    runs: list[DocRun] = []
    by_id: dict[str, DocRun] = {}
    for number, row in enumerate(_read_labels(data_dir)):
        run = DocRun(
            doc_id=row["doc_id"],
            split=row["split"],
            capture=row["capture"],
            label=row["label"],
            tamper_type=row["tamper_type"],
            expected={s for s in row["expected_signals"].split("|") if s},
        )
        runs.append(run)
        original = by_id.get(row["dup_of"])
        by_id[run.doc_id] = run
        if row["capture"] != "digital_pdf" and not with_ocr:
            run.skipped = "needs OCR"
            continue
        if original is not None and not original.scored:
            # Its original was never stored, so nothing could be matched: not a miss.
            run.skipped = "original not scored"
            continue
        raw, mime = _read_file(data_dir, row["filename"])
        hospital = _hospital_for(session, register.get(row["register_code"]), number)
        reference = f"EVAL/{number:04d}/{uuid.uuid4().hex[:6]}"
        case = Case(
            created_by=user,
            hospital=hospital,
            alias=f"Evaluation {number}",
            region="Nairobi",
            patient_ref_enc=reference.encode(),
            patient_ref_hmac=hash_patient_ref(reference),
        )
        session.add(case)
        document = CaseDocument(
            case=case,
            uploaded_by_id=user.id,
            kind=DocumentKind.INVOICE,
            mime_type=mime,
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        session.add(document)
        session.flush()
        outcome = pipeline.analyse(document, raw, session)
        session.flush()
        run.codes = {signal["code"] for signal in outcome.signals}
        run.score, run.band, run.route = outcome.risk_score, outcome.risk_band, outcome.route
    return runs


# --------------------------------------------------------------------------- the numbers


def _in_split(run: DocRun, split: str) -> bool:
    return run.scored and (split == "all" or run.split == split)


def signal_rates(runs: Iterable[DocRun], split: str) -> dict[str, Rate]:
    """Per dataset signal: bills where it was expected and raised, only raised, only expected."""
    chosen = [run for run in runs if _in_split(run, split)]
    rates: dict[str, Rate] = {}
    for name, codes in SIGNAL_MAP.items():
        tp = fp = fn = 0
        for run in chosen:
            expected = name in run.expected
            raised = bool(codes) and any(code in run.codes for code in codes)
            tp += expected and raised
            fp += raised and not expected
            fn += expected and not raised
        rates[name] = Rate(tp, fp, fn)
    return rates


def routing_matrix(runs: Iterable[DocRun], split: str) -> dict[str, Counter]:
    """For each true label, how many bills went to each route and landed in each band."""
    matrix: dict[str, Counter] = {label: Counter() for label in EXPECTED_ROUTE}
    for run in runs:
        if _in_split(run, split):
            matrix[run.label][run.route.value] += 1
            matrix[run.label][f"band:{run.band.value}"] += 1
    return matrix


def headline(runs: Iterable[DocRun], split: str) -> dict[str, tuple[int, int]]:
    """(hits, total) for the specification's targets over the bills that were scored."""
    chosen = [run for run in runs if _in_split(run, split)]
    tampered = [run for run in chosen if run.label == "tampered"]
    duplicates = [run for run in chosen if run.label == "duplicate"]
    genuine = [run for run in chosen if run.label == "genuine"]
    return {
        "tampered_signalled": (sum(bool(run.codes) for run in tampered), len(tampered)),
        "tampered_flagged": (
            sum(
                run.band != RiskBand.LOW or run.route == TriageRoute.MANUAL_REVIEW
                for run in tampered
            ),
            len(tampered),
        ),
        "duplicates_caught": (
            sum(run.route == TriageRoute.MANUAL_REVIEW for run in duplicates),
            len(duplicates),
        ),
        "genuine_left_alone": (
            sum(run.route == TriageRoute.HOSPITAL_CONFIRMATION for run in genuine),
            len(genuine),
        ),
    }


def failures(runs: Iterable[DocRun]) -> list[tuple[DocRun, str]]:
    """Scored bills that went the wrong way, with what should have happened."""
    out: list[tuple[DocRun, str]] = []
    for run in runs:
        if not run.scored:
            continue
        if run.label == "genuine" and (
            run.route == TriageRoute.MANUAL_REVIEW or run.band != RiskBand.LOW
        ):
            out.append((run, "genuine, should be low risk"))
        elif run.label == "duplicate" and run.route != TriageRoute.MANUAL_REVIEW:
            out.append((run, "reused invoice, should reach review"))
        elif run.label == "tampered" and not run.codes:
            out.append((run, f"{run.tamper_type}, nothing raised"))
        elif (
            run.label == "tampered"
            and run.band == RiskBand.LOW
            and run.route != TriageRoute.MANUAL_REVIEW
        ):
            out.append((run, f"{run.tamper_type}, raised but stayed low"))
    return out


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def field_matches(
    bill: extract.ExtractedBill, row: dict[str, str], truth: dict[str, Any]
) -> dict[str, bool]:
    """Which fields of a read bill match the label. Amounts compare as numbers."""
    fields = truth["fields"]
    return {
        "facility": bill.facility_name == fields["facility"],
        "invoice_number": bill.invoice_number == row["invoice_number_shown"],
        "invoice_date": str(bill.invoice_date) == fields["invoice_date"],
        "document_type": bill.document_type == fields["document_type"],
        "currency": bill.currency == fields["currency"],
        "total": _decimal(bill.total_kes) == _decimal(row["total_shown"]),
        "balance_due": _decimal(bill.balance_due_kes) == _decimal(row["balance_shown"]),
        "deposit": (_decimal(bill.deposit_kes) or Decimal(0))
        == (_decimal(fields.get("deposit_paid")) or Decimal(0)),
    }


def extraction_accuracy(
    data_dir: Path, *, use_llm: bool, with_ocr: bool, split: str = "all"
) -> dict[str, Any]:
    """Field accuracy over the genuine bills, split by digital PDFs and photographs."""
    truth = _read_truth(data_dir)
    hits: dict[str, Counter] = {"pdf": Counter(), "photo": Counter()}
    counts: Counter = Counter()
    skipped = 0
    for row in _read_labels(data_dir):
        if row["label"] != "genuine" or (split != "all" and row["split"] != split):
            continue
        kind = "pdf" if row["capture"] == "digital_pdf" else "photo"
        if kind == "photo" and not with_ocr:
            skipped += 1
            continue
        raw, mime = _read_file(data_dir, row["filename"])
        read = text.extract(raw, mime)
        bill = extract.extract(read.text, use_llm=use_llm, ocr_confidence=read.confidence).bill
        counts[kind] += 1
        for name, ok in field_matches(bill, row, truth[row["doc_id"]]).items():
            hits[kind][name] += ok
    return {"hits": hits, "counts": counts, "skipped": skipped}


def appeal_scores(data_dir: Path) -> dict[str, Any]:
    """Precision and recall on each split of the appeal messages, with the saved classifier."""
    import json

    with (data_dir / "appeals.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    # Always fit on the development split here, whatever model is saved: a saved one may have been
    # trained on everything, and then the held-out numbers would mean nothing.
    artifact = appeals.train([r for r in rows if r["split"] == "dev"], version="evaluation")
    names = [normalize_facility_name(r.name) for r in load_register(data_dir)]
    out: dict[str, Any] = {"version": artifact["version"], "n_train": artifact["n_train"]}
    for split in ("dev", "test"):
        part = [r for r in rows if r["split"] == split]
        out[split] = appeals.split_metrics(artifact["model"], part, names)
    hard = [r for r in rows if r["split"] == "test" and r.get("difficulty") == "hard"]
    out["hard_test"] = appeals.split_metrics(artifact["model"], hard, names) if hard else None
    return out


@contextmanager
def isolated_session() -> Iterator[Session]:
    """A session on a throwaway schema in the same database.

    The duplicate and register checks look at every stored bill, so running among real
    submissions would change the answers, and would write to them. Here the tables are made
    fresh, used, and dropped.
    """
    root = db.engine.engine  # the engine itself, even where the session is bound to a connection
    schema = f"evaluation_{uuid.uuid4().hex[:8]}"
    with root.begin() as connection:
        connection.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(root.url, connect_args={"options": f"-c search_path={schema},public"})
    try:
        # ``public`` stays on the path for the citext type, so existing tables are visible to a
        # lookup; checkfirst=False makes every table here regardless, and the count below proves
        # they landed in this schema and not in the real one.
        db.metadata.create_all(engine, checkfirst=False)
        with Session(engine) as session:
            made = session.scalar(
                sa.text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :s"),
                {"s": schema},
            )
            if not made or made < len(db.metadata.tables):
                raise RuntimeError("the evaluation schema was not built; refusing to run")
            yield session
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))


def evaluate(data_dir: Path) -> Evaluation:
    """Run everything. Nothing is written to the application's own tables."""
    with_ocr = shutil.which("tesseract") is not None
    with isolated_session() as session:
        runs = run_triage(session, data_dir, with_ocr=with_ocr)
    extraction = {
        "local": extraction_accuracy(data_dir, use_llm=False, with_ocr=with_ocr),
    }
    if llm.available():
        extraction["llm"] = extraction_accuracy(data_dir, use_llm=True, with_ocr=with_ocr)
    return Evaluation(
        runs=runs,
        signals={split: signal_rates(runs, split) for split in SPLITS},
        extraction=extraction,
        appeals=appeal_scores(data_dir),
        ocr_version=ocr_version(),
        llm_model=current_app.config["LLM_MODEL"] if llm.available() else None,
        data_version=dataset_version(data_dir),
        run_date=datetime.now(UTC).date().isoformat(),
    )


# --------------------------------------------------------------------------- the report


def _skipped_note(skipped: list[DocRun]) -> str:
    ocr = sum(run.skipped == "needs OCR" for run in skipped)
    orphans = len(skipped) - ocr
    note = f"{ocr} photographs and scans (they need OCR)"
    if orphans:
        note += f" and {orphans} copies whose originals were among them"
    return note


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _ratio(hits: int, total: int) -> str:
    return "n/a" if not total else f"{hits}/{total} ({hits / total * 100:.0f}%)"


def _met(value: float | None, target: float) -> str:
    if value is None:
        return "not measured"
    return "met" if value >= target else "not met"


def render_report(evaluation: Evaluation) -> str:
    """The report as Markdown."""
    runs = evaluation.runs
    scored = [run for run in runs if run.scored]
    skipped = [run for run in runs if not run.scored]
    lines: list[str] = ["# AI evaluation report", ""]
    lines += [
        f"Run on {evaluation.run_date} by `flask ai evaluate`. Pipeline version "
        f"`{pipeline.PIPELINE_VERSION}`. Dataset: {evaluation.data_version} "
        "(synthetic, fictional).",
        "",
        "- Text reading: PDF text layer; OCR "
        + (evaluation.ocr_version or "not installed here, so photographs and scans were not scored")
        + ".",
        "- Language model: "
        + (
            evaluation.llm_model
            or "not used (no key set); every number below is the local reader and rules"
        )
        + ".",
        f"- Bills scored: {len(scored)} of {len(runs)}"
        + (f" ({_skipped_note(skipped)} left out)" if skipped else "")
        + ".",
        "",
    ]

    # --- headline
    test = headline(runs, "test")
    everything = headline(runs, "all")
    extraction_local = evaluation.extraction["local"]
    appeal_test = evaluation.appeals["test"]
    lines += ["## Against the specification's targets", ""]
    lines += [
        "| Measure | MVP target | Held-out split | All bills | Status |",
        "| --- | --- | --- | --- | --- |",
    ]

    def row(label: str, key: str, held: tuple[int, int], whole: tuple[int, int]) -> str:
        target = TARGETS[key][0]
        value = held[0] / held[1] if held[1] else None
        return (
            f"| {label} | {_pct(target)} | {_ratio(*held)} | {_ratio(*whole)} "
            f"| {_met(value, target)} |"
        )

    lines.append(
        row(
            "Tampered bills with at least one signal raised",
            "tampered_signalled",
            test["tampered_signalled"],
            everything["tampered_signalled"],
        )
    )
    lines.append(
        row(
            "Tampered bills lifted to medium or high, or sent to review",
            "tampered_flagged",
            test["tampered_flagged"],
            everything["tampered_flagged"],
        )
    )
    lines.append(
        row(
            "Reused invoices caught",
            "duplicates_caught",
            test["duplicates_caught"],
            everything["duplicates_caught"],
        )
    )
    for kind, name in (("pdf", "digital PDFs"), ("photo", "photographs and scans")):
        counted = extraction_local["counts"][kind]
        kind_hits = sum(extraction_local["hits"][kind].values())
        kind_total = counted * len(FIELDS)
        lines.append(
            f"| Extraction field accuracy, {name} (local reader, genuine bills) "
            f"| {_pct(TARGETS['extraction'][0])} | not split "
            f"| {_ratio(kind_hits, kind_total)} "
            f"| {_met(kind_hits / kind_total if kind_total else None, TARGETS['extraction'][0])} |"
        )
    lines.append(
        f"| Appeal checker precision | {_pct(TARGETS['appeal_precision'][0])} "
        f"| {_pct(appeal_test['precision'])} "
        f"({appeal_test['tp']}/{appeal_test['tp'] + appeal_test['fp']}) | not applicable | "
        f"{_met(appeal_test['precision'], TARGETS['appeal_precision'][0])} |"
    )
    lines.append(
        f"| Genuine bills left for the hospital alone (no false alarm) | none set | "
        f"{_ratio(*test['genuine_left_alone'])} "
        f"| {_ratio(*everything['genuine_left_alone'])} | for information |"
    )
    lines += [
        "",
        'The specification does not say what "flagged" means, so both readings are shown. '
        "A signal raised is what the hospital and the reviewer are shown (the reason, in plain "
        "words), and the first row is judged against the target. The second row is stricter: it "
        "needs the fixed signal weights to add up to 30 or more, and a single small signal "
        "(a changed total alone is 25; an editor named in the file plus a late modified date "
        "together are 25) does not "
        "reach it. "
        "A reused invoice counts as caught when it is sent to manual review.",
        "",
    ]

    # --- extraction
    lines += ["## Extraction field accuracy", ""]
    lines += [
        "Genuine bills only, compared with the dataset's ground truth. The local reader "
        "always decides "
        "the amounts; the model path can only fill in what the local reader missed.",
        "",
    ]
    for name, result in evaluation.extraction.items():
        title = "Local reader" if name == "local" else "With the language model"
        lines += [f"### {title}", ""]
        lines += ["| Field | Digital PDFs | Photographs and scans |", "| --- | --- | --- |"]
        for field_name in FIELDS:
            pdf = _ratio(result["hits"]["pdf"][field_name], result["counts"]["pdf"])
            photo = (
                _ratio(result["hits"]["photo"][field_name], result["counts"]["photo"])
                if result["counts"]["photo"]
                else "not scored (needs OCR)"
            )
            lines.append(f"| {field_name} | {pdf} | {photo} |")
        pdf_hits = sum(result["hits"]["pdf"].values())
        photo_hits = sum(result["hits"]["photo"].values())
        lines.append(
            f"| **all fields** | {_ratio(pdf_hits, result['counts']['pdf'] * len(FIELDS))} | "
            + (
                _ratio(photo_hits, result["counts"]["photo"] * len(FIELDS))
                if result["counts"]["photo"]
                else "not scored (needs OCR)"
            )
            + " |"
        )
        lines.append("")
    if "llm" not in evaluation.extraction:
        lines += [
            "The language model path was **not measured**: no key is set on the machine that "
            "ran this "
            "report. Run it again with `ANTHROPIC_API_KEY` set to fill this in.",
            "",
        ]

    # --- signals
    lines += ["## Per-signal precision and recall", ""]
    lines += [
        "Signal names are the dataset's; the engine codes that answer each are shown. "
        "TP is expected and raised, FP raised without being expected, FN expected and missed. "
        "Rows with no expected bills show recall as n/a.",
        "",
    ]
    for split, heading in (("test", "Held-out split (test)"), ("all", "All scored bills")):
        lines += [f"### {heading}", ""]
        lines += [
            "| Dataset signal | Engine code | TP | FP | FN | Precision | Recall |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for name, rate in evaluation.signals[split].items():
            codes = ", ".join(f"`{c}`" for c in SIGNAL_MAP[name]) or NOT_SEPARATE.get(
                name, "*none: not detected*"
            )
            lines.append(
                f"| {name} | {codes} | {rate.tp} | {rate.fp} | {rate.fn} "
                f"| {_pct(rate.precision)} | {_pct(rate.recall)} |"
            )
        lines.append("")
    unmapped = Counter(
        code
        for run in scored
        for code in run.codes
        if not any(code in c for c in SIGNAL_MAP.values())
    )
    genuine = [run for run in scored if run.label == "genuine"]
    if unmapped:
        lines += ["Signals raised that the dataset has no name for:", ""]
        for code, count in sorted(unmapped.items()):
            on_genuine = sum(code in run.codes for run in genuine)
            lines.append(f"- `{code}`: {count} bills ({on_genuine} of them genuine)")
        lines.append("")

    # --- routing
    lines += ["## Routing", ""]
    lines += [
        "Where each true label ended up. A genuine bill should go to the hospital; a tampered or "
        "reused one should reach a person, either by a high score or a forced-review signal. "
        "A tampered bill that lands in the medium band still goes to the hospital with the "
        "reasons shown.",
        "",
    ]
    for split, heading in (("test", "Held-out split"), ("all", "All scored bills")):
        matrix = routing_matrix(runs, split)
        lines += [f"### {heading}", ""]
        lines += [
            "| True label | Hospital confirmation | Manual review | Low | Medium | High |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for label, counts in matrix.items():
            lines.append(
                f"| {label} | {counts['hospital_confirmation']} | {counts['manual_review']} | "
                f"{counts['band:low']} | {counts['band:medium']} | {counts['band:high']} |"
            )
        lines.append("")

    # --- by kind of tampering and capture
    lines += ["### Tampered bills by kind", ""]
    lines += [
        "| Tamper type | Scored | Any signal | Medium or high, or review | Sent to manual review |",
        "| --- | --- | --- | --- | --- |",
    ]
    kinds = Counter(run.tamper_type for run in scored if run.label == "tampered")
    for kind in sorted(kinds):
        group = [run for run in scored if run.label == "tampered" and run.tamper_type == kind]
        flagged = sum(
            run.band != RiskBand.LOW or run.route == TriageRoute.MANUAL_REVIEW for run in group
        )
        reviewed = sum(run.route == TriageRoute.MANUAL_REVIEW for run in group)
        signalled = sum(bool(run.codes) for run in group)
        lines.append(f"| {kind} | {len(group)} | {signalled} | {flagged} | {reviewed} |")
    lines.append("")

    # --- where it fails
    lines += ["## Where it fails", ""]
    lines += [
        "Every scored bill the engine got wrong, so the gaps can be worked on one by one. "
        "Codes are the engine's, as raised.",
        "",
    ]
    misses = failures(runs)
    if misses:
        lines += [
            "| Bill | Capture | Truth | Went to | Raised |",
            "| --- | --- | --- | --- | --- |",
        ]
        for run, truth in misses:
            raised = ", ".join(f"`{c}`" for c in sorted(run.codes)) or "nothing"
            lines.append(
                f"| {run.doc_id} | {run.capture} | {truth} | {run.route.value} ({run.band.value}) "
                f"| {raised} |"
            )
        lines.append("")
    else:
        lines += ["None.", ""]

    # --- appeals
    appeal = evaluation.appeals
    lines += ["## Appeal checker", ""]
    lines += [
        f"A classifier fitted for this run on the {appeal['n_train']} development-split messages "
        f'only. "Likely a scam" means a score of '
        f"{appeals.FLAG_FROM} or more.",
        "",
        "| Messages | n | Precision | Recall | TP | FP | FN | TN |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, name in (
        ("test", "Held out (test split)"),
        ("dev", "Training split (optimistic)"),
        ("hard_test", "Held out, hard cases only"),
    ):
        m = appeal[key]
        if m:
            lines.append(
                f"| {name} | {m['n']} | {_pct(m['precision'])} | {_pct(m['recall'])} "
                f"| {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} |"
            )
    lines.append("")

    # --- limits
    lines += ["## Read this before quoting a number", ""]
    lines += [
        "- The data is synthetic and cleaner than real paperwork: no stamps, handwriting or "
        "multi-page bills. These figures are an upper bound.",
        "- The extraction rules, redaction lexicon, duplicate thresholds and the photo tamper "
        "threshold were tuned while looking at this dataset's bills, so the held-out bill "
        "numbers are not independent of it. Only the appeal classifier was trained on one split "
        "and scored on the other.",
        "- The held-out split is small (a few bills per tamper type); one more or fewer changes "
        "a percentage a lot. Read the counts, not only the percentages.",
        "- An incremental PDF update (a second end-of-file marker) is checked, but no dataset "
        "file carries one, so that row shows nothing to measure.",
        "- A splice pasted into a photo with uniform compression is not detected; the "
        "error-level check finds only uneven recompression.",
        "- Swahili and code-switched appeal text has not been reviewed by a native speaker.",
    ]
    if skipped:
        lines.append(
            f"- {_skipped_note(skipped)} were **not scored** because Tesseract is not installed on "
            "the machine that ran this report. Every figure here is for the digital PDFs. Run "
            "`flask ai evaluate` on an image that has OCR to include them."
        )
    return "\n".join(lines) + "\n"
