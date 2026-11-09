"""After triage, no patient name, phone, ID, diagnosis or medicine is anywhere in the database.

``tests/ai/test_redact.py`` checks the redaction step on every synthetic bill. This goes one step
further and reads every table, every text and JSON column, after the real pipeline has run: the
stored text, the extracted fields, the signals, the reasons, the audit trail and the review items.
"""

import re

import pytest
from sqlalchemy import inspect, text

from tests.ai import test_pipeline_real as real
from tests.ai.helpers import ground_truth, labels, needs_dataset

pytestmark = pytest.mark.usefixtures("s3")

register = real.register  # the fixture that onboards the 25 register facilities
triage = real.triage

# Genuine, altered, duplicated and prompt-injection bills, so every path of the pipeline is read.
SAMPLE = [
    row["doc_id"]
    for kind in ("genuine", "tampered", "duplicate")
    for row in [r for r in labels() if r["label"] == kind and r["filename"].endswith(".pdf")][:5]
] + [r["doc_id"] for r in labels() if r["tamper_type"] == "prompt_injection"]


def _flat(value: str) -> str:
    return re.sub(r"\s+", " ", value).lower()


def _everything_in_the_database(session) -> str:  # type: ignore[no-untyped-def]
    """Every cell of every table as one lowercase string."""
    parts: list[str] = []
    for table in inspect(session.get_bind()).get_table_names():
        if table == "alembic_version":
            continue
        for row in session.execute(text(f'SELECT t::text FROM "{table}" t')):
            parts.append(row[0])
    return _flat("\n".join(parts))


@needs_dataset
def test_no_patient_detail_survives_triage_anywhere_in_the_database(register, db_session) -> None:  # type: ignore[no-untyped-def]
    secrets: set[str] = set()
    for doc_id in SAMPLE:
        triage(register, doc_id)
        secrets.update(ground_truth()[doc_id]["must_redact"])
    assert len(secrets) > 20, "the sample must plant enough words to mean something"

    stored = _everything_in_the_database(db_session)
    assert "total" in stored or "kes" in stored, "the stored text should still hold what is checked"
    leaked = sorted(s for s in secrets if _flat(s) in stored)
    assert leaked == [], f"found in the database: {leaked}"
