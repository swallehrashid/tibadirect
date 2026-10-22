"""Reading a bill's fields: facility, invoice number, date, type, line items, total, currency.

Two readers fill the same ``ExtractedBill``:

* The **local reader** is rules over the text. It is always available, and its figures are the
  ones the rest of the system trusts: a language model's answer never overrides a number found here.
* The **language model reader** (``ai/llm.py``) runs only if the family agreed to processing
  abroad and a key is set. It sees the redacted text alone. When both read a total and they differ
  by more than 1 percent, the local figure is kept and the signal ``extraction_disagreement`` is
  raised. A field the local reader could not find may be filled from the model's answer.

The patient reference is read from the original text, in memory, by the local reader only, and
is never sent to a model. Line-item labels are read from the redacted text, so a procedure's name
is already gone.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.ai import redact
from app.ai.signals import Signal

logger = logging.getLogger(__name__)

DisagreementThreshold = Decimal("0.01")
LOW_CONFIDENCE = 0.6
MAX_LLM_CHARS = 12_000


class LineItem(BaseModel):
    label: str  # already redacted, e.g. "Bed charges", "[REDACTED:PROCEDURE] fee"
    amount_kes: Decimal
    # not in the specification's schema; read from the bill's quantity and unit price columns,
    # so the tamper check can test that quantity times unit price is the amount
    quantity: int | None = None
    unit_kes: Decimal | None = None

    @field_validator("label")
    @classmethod
    def _short(cls, value: str) -> str:
        return value.strip()[:200]


class ExtractedBill(BaseModel):
    facility_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    document_type: Literal["invoice", "estimate", "final_bill", "unknown"] = "unknown"
    patient_reference: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    total_kes: Decimal | None = None
    currency: Literal["KES", "USD", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0, le=1)
    # not in the specification's schema; the amount a case would raise, and what was paid up front
    balance_due_kes: Decimal | None = None
    deposit_kes: Decimal | None = None


@dataclass
class Extraction:
    """The result: the fields, which reader produced them, and the signals raised on the way."""

    bill: ExtractedBill
    used_llm: bool = False
    signals: list[Signal] = field(default_factory=list)
    local: ExtractedBill | None = None  # what the local reader alone found


# --------------------------------------------------------------------------- helpers

AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?"
AMOUNT_RE = re.compile(rf"(?<![\w.,])({AMOUNT})(?![\w,]|\.\d)")
MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
DATE_PATTERNS = (
    re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"),  # 2026-09-29
    re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b"),  # 29/09/2026, 29/09/26
    re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"),  # 27 Jun 2026
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\b"),  # Jun 27, 2026
)
INSTRUCTION_RE = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?)"
    r"|disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)"
    r"|(?:^|\n)\s*(?:system|assistant|user)\s*:"
    r"|you\s+are\s+now\b"
    r"|\bdo\s+not\s+flag\b"
    r"|pre-?approved"
    r"|(?:set|change|output|report)\s+(?:the\s+)?(?:total|amount|risk(?:\s+score)?)\s+(?:to|as)\b"
    r"|output\s+risk\s+score",
    re.IGNORECASE,
)
INVOICE_LABEL_RE = re.compile(
    r"(?:invoice|estimate|receipt|bill)\s*(?:no\.?|number|#)\s*[:;]?\s*(?P<value>\S+)"
    r"|(?:^|\s)no\s*[:;]\s*(?P<short>\S+)",
    re.IGNORECASE,
)
PATIENT_REF_RE = re.compile(
    r"(?:patient\s+ref(?:erence)?\.?|(?:admission|hospital|patient|file|ip)\s*(?:no\.?|number)|"
    r"mrn|\bref\b\.?)\s*[:;]?\s*(?P<value>[A-Za-z0-9][A-Za-z0-9/._-]*)",
    re.IGNORECASE,
)
TOTAL_RE = re.compile(r"^\s*(?:grand\s+|estimated\s+)?total\b", re.IGNORECASE)
BALANCE_RE = re.compile(r"^\s*(?:balance(?:\s+due)?|amount\s+due|net\s+payable)\b", re.IGNORECASE)
DEPOSIT_RE = re.compile(r"^\s*(?:deposit(?:\s+paid)?|amount\s+paid|paid)\b", re.IGNORECASE)
FACILITY_WORDS = re.compile(
    r"hospital|medical|centre|center|clinic|nursing|health|dispensary|mission|maternity", re.I
)
ROW_RE = re.compile(
    rf"^\s*(?:\d{{1,3}}\s+)?(?P<label>\D.*?)\s+(?P<qty>\d{{1,4}})\s+"
    rf"(?P<unit>{AMOUNT})\s+(?P<amount>{AMOUNT})\s*$"
)


def parse_amount(value: str) -> Decimal | None:
    """``"66,700.00"`` or ``"4,500"`` or ``"4500.5"`` as a Decimal, or None."""
    cleaned = value.replace(",", "").strip()
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _last_amount(line: str) -> Decimal | None:
    found = AMOUNT_RE.findall(line)
    return parse_amount(found[-1]) if found else None


def parse_date(line: str) -> date | None:
    for index, pattern in enumerate(DATE_PATTERNS):
        match = pattern.search(line)
        if not match:
            continue
        a, b, c = match.groups()
        try:
            if index == 0:
                return date(int(a), int(b), int(c))
            if index == 1:
                year = int(c) + 2000 if len(c) == 2 else int(c)
                return date(year, int(b), int(a))
            if index == 2:
                return date(int(c), MONTHS[b[:3].lower()], int(a))
            return date(int(c), MONTHS[a[:3].lower()], int(b))
        except (ValueError, KeyError):
            continue
    return None


# --------------------------------------------------------------------------- the local reader


def _document_type(text: str) -> str:
    head = "\n".join(text.split("\n")[:12]).lower()
    if "final bill" in head or "final invoice" in head:
        return "final_bill"
    if re.search(r"estimate|pro-?forma|quotation", head):
        return "estimate"
    if "invoice" in head or "bill" in head:
        return "invoice"
    return "unknown"


def _currency(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\bkes\b|\bksh[s]?\b|kenya shilling", lowered):
        return "KES"
    if re.search(r"\busd\b|\bus\$|\$\s?\d", lowered):
        return "USD"
    return "unknown"


def _facility(text: str) -> str | None:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines[:6]:
        if FACILITY_WORDS.search(line) and len(line) <= 80 and not re.search(r"\d{3}", line):
            return line
    return lines[0][:80] if lines else None


def invoice_number_in(text: str | None) -> str | None:
    """The invoice, estimate or receipt number printed in the top of a text, if any."""
    return _invoice_number(text or "")


def _invoice_number(text: str) -> str | None:
    for line in text.split("\n")[:25]:
        match = INVOICE_LABEL_RE.search(line)
        if match:
            value = (match.group("value") or match.group("short") or "").strip(".,;:")
            if value and not value.startswith("["):
                return value
    return None


def _invoice_date(text: str) -> date | None:
    for line in text.split("\n")[:25]:
        if re.search(r"date", line, re.IGNORECASE):
            found = parse_date(line)
            if found:
                return found
    for line in text.split("\n")[:15]:
        found = parse_date(line)
        if found:
            return found
    return None


def _line_items(text: str) -> list[LineItem]:
    items = []
    for line in text.split("\n"):
        match = ROW_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip(" -")
        if not label or TOTAL_RE.match(label) or BALANCE_RE.match(label) or DEPOSIT_RE.match(label):
            continue
        amount = parse_amount(match.group("amount"))
        if amount is None:
            continue
        items.append(
            LineItem(
                label=label,
                amount_kes=amount,
                quantity=int(match.group("qty")),
                unit_kes=parse_amount(match.group("unit")),
            )
        )
    return items


def _money_line(text: str, pattern: re.Pattern[str]) -> Decimal | None:
    found: Decimal | None = None
    for line in text.split("\n"):
        if pattern.match(line):
            amount = _last_amount(line)
            if amount is not None:
                found = amount
                if pattern is TOTAL_RE:
                    break
    return found


def patient_reference(raw_text: str) -> str | None:
    """The reference the hospital printed for the patient, from the original text."""
    for line in raw_text.split("\n"):
        match = PATIENT_REF_RE.search(line)
        if match:
            return match.group("value").strip(".,;:")
    return None


def read_locally(
    redacted_text: str, raw_text: str, ocr_confidence: float | None = None
) -> ExtractedBill:
    """Rules over the text. ``redacted_text`` gives the line items; ``raw_text`` gives the
    patient reference (in memory only)."""
    total = _money_line(redacted_text, TOTAL_RE)
    items = _line_items(redacted_text)
    found = {
        "facility": _facility(redacted_text),
        "invoice_number": _invoice_number(redacted_text),
        "date": _invoice_date(redacted_text),
        "total": total,
        "items": bool(items),
    }
    present = sum(1 for value in found.values() if value)
    confidence = present / len(found)
    if ocr_confidence is not None:
        confidence = min(confidence, max(0.0, min(1.0, ocr_confidence / 100)) + 0.2)
    return ExtractedBill(
        facility_name=found["facility"],
        invoice_number=found["invoice_number"],
        invoice_date=found["date"],
        document_type=_document_type(redacted_text),
        patient_reference=patient_reference(raw_text),
        line_items=items,
        total_kes=total,
        currency=_currency(redacted_text),
        confidence=round(confidence, 2),
        # an estimate has no balance line: what it asks for is its total
        balance_due_kes=_money_line(redacted_text, BALANCE_RE) or total,
        deposit_kes=_money_line(redacted_text, DEPOSIT_RE),
    )


def instruction_like(text: str) -> bool:
    """True if the text addresses software ("ignore previous instructions", "system: ...")."""
    return bool(INSTRUCTION_RE.search(text))


# --------------------------------------------------------------------------- combining


def extract(
    raw_text: str,
    *,
    use_llm: bool = False,
    ocr_confidence: float | None = None,
) -> Extraction:
    """Read the bill. ``raw_text`` is the original text, which never leaves this function."""
    result = redact.redact(raw_text)
    local = read_locally(result.text, raw_text, ocr_confidence)
    bill = local.model_copy(deep=True)
    signals: list[Signal] = []
    used_llm = False

    if instruction_like(raw_text):
        signals.append(Signal("instruction_like_text"))

    if use_llm:
        from app.ai import llm

        answer = llm.extract_bill(result.text)
        if answer is not None:
            used_llm = True
            bill, disagreed = _merge(local, answer)
            if disagreed:
                signals.append(
                    Signal(
                        "extraction_disagreement",
                        {"local_total": str(local.total_kes), "model_total": str(answer.total_kes)},
                    )
                )

    if bill.total_kes is None or bill.confidence < LOW_CONFIDENCE:
        signals.append(
            Signal(
                "low_extraction_confidence",
                {"confidence": bill.confidence, "total_found": bill.total_kes is not None},
            )
        )
    return Extraction(bill=bill, used_llm=used_llm, signals=signals, local=local)


def _merge(local: ExtractedBill, answer: ExtractedBill) -> tuple[ExtractedBill, bool]:
    """Local figures win; the model fills what the local reader missed. Returns the merged bill
    and whether the two disagreed about the total by more than 1 percent."""
    merged = local.model_copy(deep=True)
    for name in ("facility_name", "invoice_number", "invoice_date"):
        if getattr(merged, name) is None:
            setattr(merged, name, getattr(answer, name))
    if merged.document_type == "unknown":
        merged.document_type = answer.document_type
    if merged.currency == "unknown":
        merged.currency = answer.currency
    if not merged.line_items:
        merged.line_items = answer.line_items
    disagreed = False
    if merged.total_kes is None:
        merged.total_kes = answer.total_kes
    elif answer.total_kes is not None and merged.total_kes > 0:
        gap = abs(answer.total_kes - merged.total_kes) / merged.total_kes
        disagreed = gap > DisagreementThreshold
    merged.confidence = round(max(local.confidence, min(answer.confidence, 1.0)), 2)
    return merged, disagreed
