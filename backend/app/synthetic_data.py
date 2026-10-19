"""Read the synthetic dataset in ``data/synthetic`` (see its README).

Used by ``flask seed`` and, later, by the AI evaluation. Everything in the dataset is invented.
"""

import csv
import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic"


@dataclass(frozen=True)
class RegisterRow:
    register_code: str
    name: str
    aliases: str
    county: str
    facility_level: str
    ownership: str
    official_email: str
    official_phone: str
    source: str


@dataclass(frozen=True)
class DatasetDocument:
    doc_id: str
    filename: str
    path: Path
    label: str
    doc_type: str
    register_code: str
    facility: str
    invoice_number: str
    total_kes: int
    balance_kes: int
    phash: str
    sha256: str
    size_bytes: int
    mime_type: str


def load_register(data_dir: Path = DEFAULT_DATA_DIR) -> list[RegisterRow]:
    with open(data_dir / "facility_register.csv", newline="", encoding="utf-8") as handle:
        return [RegisterRow(**row) for row in csv.DictReader(handle)]


def load_documents(data_dir: Path = DEFAULT_DATA_DIR) -> list[DatasetDocument]:
    """Every labelled invoice, estimate and bill, ordered by document ID."""
    truth = {}
    with open(data_dir / "ground_truth.jsonl", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            truth[record["doc_id"]] = record["fields"]

    documents = []
    with open(data_dir / "labels.csv", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = data_dir / "invoices" / row["filename"]
            payload = path.read_bytes()
            fields = truth[row["doc_id"]]
            documents.append(
                DatasetDocument(
                    doc_id=row["doc_id"],
                    filename=row["filename"],
                    path=path,
                    label=row["label"],
                    doc_type=row["doc_type"],
                    register_code=row["register_code"],
                    facility=fields["facility"],
                    invoice_number=fields["invoice_number"],
                    total_kes=fields["total"],
                    balance_kes=fields["balance_due"],
                    phash=row["phash"],
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
            )
    return sorted(documents, key=lambda d: d.doc_id)


def load_appeals(data_dir: Path = DEFAULT_DATA_DIR) -> list[dict]:
    with open(data_dir / "appeals.jsonl", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
