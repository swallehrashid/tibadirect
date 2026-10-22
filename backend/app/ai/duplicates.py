"""Finding the same bill submitted twice, in any disguise.

Four checks against bills on *other* cases:

* ``duplicate_exact``: the same file (SHA-256).
* ``duplicate_near``: the same bill seen again: a re-save, a re-photograph, a crop, another
  layout. It is judged mainly by what the bill *says*: the amounts on it (totals and line items),
  compared with the amounts on every other stored bill. The look of the first page (a perceptual
  hash) is a second opinion: on text-only pages it cannot tell two bills of one hospital apart on
  its own, so a similar look counts only when some of the amounts match too.
* ``duplicate_fingerprint``: the same facility, invoice number and total, in any layout. This is a
  keyed hash, so the table holds no readable invoice numbers.
* ``patient_active_elsewhere``: the same patient reference at the same hospital on a case that is
  still open.

Each of them sends the bill to a person; none of them rejects it.
"""

import re
from decimal import Decimal

import imagehash
from PIL import Image
from rapidfuzz.distance import Levenshtein
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.extract import invoice_number_in
from app.ai.signals import Signal
from app.models.case import Case, CaseDocument, hash_invoice_fingerprint
from app.models.enums import CaseStatus

NEAR_DISTANCE = 6  # bits of a 64-bit hash; the look-alike test
SAME_AMOUNTS = 0.6  # share of amounts two bills have in common to be the same bill
LOOKS_ALIKE_AMOUNTS = 0.3  # ...or, if their first pages also look alike, this share
MAX_COMPARED = 5000  # stored bills a new one is compared with (the newest first)
_AMOUNT_RE = re.compile(r"(?<![\w.,])\d[\d,]*(?:\.\d+)?(?!\w)")
# Dates are numbers too, and every bill of a year shares its year, so they are set aside first.
_DATE_RE = re.compile(
    r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}\b|\b\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}\b"
)
ENDED = (CaseStatus.CLOSED, CaseStatus.CANCELLED)
_OCR_FIXES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def image_hash(image: Image.Image) -> str:
    """A 64-bit perceptual hash of the page as 16 hex digits."""
    return str(imagehash.phash(image.convert("L")))


def normalize_invoice_number(number: str) -> str:
    """Upper case, letters and digits only, with the look-alikes OCR confuses made equal."""
    return re.sub(r"[^A-Z0-9]", "", number.upper()).translate(_OCR_FIXES)


def invoice_fingerprint(
    facility: str | None, invoice_number: str | None, total_kes: Decimal | None
) -> str | None:
    """The keyed fingerprint of facility, invoice number and total, or None if one is missing."""
    if not facility or not invoice_number or total_kes is None:
        return None
    number = normalize_invoice_number(invoice_number)
    if not number:
        return None
    return hash_invoice_fingerprint(facility, number, int(total_kes * 100))


def hamming(first: str, second: str) -> int:
    return bin(int(first, 16) ^ int(second, 16)).count("1")


def amounts_in(text: str | None) -> frozenset[str]:
    """The numbers of three or more digits in a text, written one way ("66,700.00" is "66700")."""
    found = set()
    for match in _AMOUNT_RE.findall(_DATE_RE.sub(" ", text or "")):
        value = match.replace(",", "")
        if value.endswith(".00"):
            value = value[:-3]
        if len(value.replace(".", "")) >= 3:
            found.add(value)
    return frozenset(found)


def similarity(first: frozenset[str], second: frozenset[str]) -> float:
    """The share of amounts two bills have in common (0 to 1)."""
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def check(
    session: Session,
    case: Case,
    document: CaseDocument,
    *,
    sha256: str,
    phash: str | None,
    fingerprint: str | None,
    redacted_text: str | None = None,
    invoice_number: str | None = None,
) -> list[Signal]:
    """The duplicate signals for ``document`` of ``case``, against every other case's bills."""
    signals: list[Signal] = []

    if session.execute(
        select(CaseDocument.id)
        .where(CaseDocument.case_id != case.id, CaseDocument.sha256 == sha256)
        .limit(1)
    ).first():
        signals.append(Signal("duplicate_exact"))

    near = _most_alike(session, case, phash, amounts_in(redacted_text), invoice_number)
    if near is not None:
        signals.append(Signal("duplicate_near", near))

    if (
        fingerprint
        and session.execute(
            select(CaseDocument.id)
            .where(CaseDocument.case_id != case.id, CaseDocument.fingerprint == fingerprint)
            .limit(1)
        ).first()
    ):
        signals.append(Signal("duplicate_fingerprint"))

    if (
        case.patient_ref_hmac
        and session.execute(
            select(Case.id)
            .where(
                Case.id != case.id,
                Case.hospital_id == case.hospital_id,
                Case.patient_ref_hmac == case.patient_ref_hmac,
                Case.status.notin_(ENDED),
            )
            .limit(1)
        ).first()
    ):
        signals.append(Signal("patient_active_elsewhere"))
    return signals


def _different_invoices(first: str | None, second: str | None) -> bool:
    """True if both bills carry an invoice number and the numbers are not the same (allowing a
    single slipped character, which OCR makes). Two bills with identical amounts and different
    invoice numbers are two bills, such as the same package billed twice at one hospital."""
    if not first or not second:
        return False
    one, other = normalize_invoice_number(first), normalize_invoice_number(second)
    return bool(one and other and Levenshtein.distance(one, other) > 1)


def _most_alike(
    session: Session,
    case: Case,
    phash: str | None,
    amounts: frozenset[str],
    invoice_number: str | None,
) -> dict[str, float | int | None] | None:
    """Detail of the stored bill most like this one if any is alike enough, else None."""
    if not amounts:
        return None
    rows = session.execute(
        select(CaseDocument.redacted_text, CaseDocument.phash)
        .where(CaseDocument.case_id != case.id, CaseDocument.redacted_text.is_not(None))
        .order_by(CaseDocument.created_at.desc())
        .limit(MAX_COMPARED)
    ).all()
    best: dict[str, float | int | None] | None = None
    for other_text, other_hash in rows:
        if _different_invoices(invoice_number, invoice_number_in(other_text)):
            continue
        shared = similarity(amounts, amounts_in(other_text))
        distance = hamming(phash, other_hash) if phash and other_hash else None
        alike = shared >= SAME_AMOUNTS or (
            distance is not None and distance <= NEAR_DISTANCE and shared >= LOOKS_ALIKE_AMOUNTS
        )
        if alike and (best is None or shared > (best["similarity"] or 0)):
            best = {"similarity": round(shared, 2), "distance": distance}
    return best
