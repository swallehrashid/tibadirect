"""Signs that a bill was altered after it was made.

PDF structure and metadata, arithmetic, hidden text, fonts, and for photographs a re-compression
test and the EXIF "software" tag. Every check raises a signal with a weight (``ai/signals.py``);
none of them rejects a bill. Thresholds were set on the synthetic set so that no genuine bill in
it raises a signal; the evaluation (P3-F16) reports what each check finds and misses.

What was *not* found is as important as what was: the synthetic set's photographs spliced from a
cleaner render cannot be told apart from genuine photographs by any global image measure tried
(re-compression error, noise level), so ``image_ela_high`` is set high enough to stay silent on
genuine photographs and finds none of those splices. The hospital's confirmation, not this check,
is what protects the money.
"""

import io
import re
import warnings
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
from PIL import ExifTags, Image, ImageChops, ImageOps
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app import lexicon
from app.ai.extract import AMOUNT_RE, ExtractedBill
from app.ai.signals import Signal
from app.ai.text import ExtractedText, Word

TOTALS_TOLERANCE = Decimal("0.01")  # 1 percent
LINE_TOLERANCE = Decimal("1.00")  # a shilling of rounding between quantity x unit and amount
MODIFIED_AFTER = timedelta(hours=24)
MONEY_LINE_RE = re.compile(
    r"^(?:grand\s+|estimated\s+)?total\b|^balance|^deposit|^amount\s+due", re.IGNORECASE
)
ODD_FONT_AMOUNTS = 2  # line amounts in a font the other line amounts do not use
ELA_QUALITY = 90
ELA_TILE = 48
ELA_SIDE = 1600
ELA_HIGH = 2.5  # 95th-percentile tile error over the mean tile error; genuine photos stay under 1.9
SOFTWARE_TAG = ExifTags.Base.Software


def check(raw: bytes, mime_type: str, read: ExtractedText, bill: ExtractedBill) -> list[Signal]:
    """All tamper signals for one bill: file-level checks for its type, then the text ones."""
    signals: list[Signal] = []
    if mime_type == "application/pdf":
        signals.extend(pdf_signals(raw))
    else:
        signals.extend(image_signals(raw))
    signals.extend(arithmetic_signals(bill))
    if read.hidden_words:
        signals.append(Signal("hidden_text_under_overlay", {"segments": len(read.hidden_words)}))
    if font_inconsistent(read.words):
        signals.append(Signal("font_inconsistency"))
    return signals


# --------------------------------------------------------------------------- PDF files


def _pdf_date(value: Any) -> datetime | None:
    match = re.match(r"D:(\d{4})(\d\d)(\d\d)(\d\d)?(\d\d)?(\d\d)?", str(value or ""))
    if not match:
        return None
    try:
        return datetime(*(int(part) if part else 0 for part in match.groups()))  # type: ignore[arg-type]
    except ValueError:
        return None


def pdf_signals(raw: bytes) -> list[Signal]:
    """Signals from a PDF's structure and metadata."""
    signals: list[Signal] = []
    endings = raw.count(b"%%EOF") - (1 if b"/Linearized" in raw[:2048] else 0)
    if endings > 1:
        signals.append(Signal("pdf_incremental_updates", {"revisions": endings}))
    try:
        metadata = PdfReader(io.BytesIO(raw)).metadata
    except (PyPdfError, ValueError, OSError):
        return signals
    if metadata is None:
        return signals
    makers = f"{metadata.get('/Producer') or ''} | {metadata.get('/Creator') or ''}".casefold()
    known = lexicon.read_terms(lexicon.Path(lexicon.lexicon_dir()) / "editor_software.txt")
    if any(term in makers for term in known):
        signals.append(Signal("pdf_editor_software"))
    created, modified = (
        _pdf_date(metadata.get("/CreationDate")),
        _pdf_date(metadata.get("/ModDate")),
    )
    if created and modified and modified - created > MODIFIED_AFTER:
        signals.append(
            Signal(
                "pdf_modified_after_creation",
                {"hours": int((modified - created).total_seconds() // 3600)},
            )
        )
    return signals


# --------------------------------------------------------------------------- arithmetic


def arithmetic_signals(bill: ExtractedBill) -> list[Signal]:
    """Lines that do not add up to the total, or a quantity times a price that is not the amount."""
    signals: list[Signal] = []
    items = bill.line_items
    if items and bill.total_kes:
        added = sum((item.amount_kes for item in items), Decimal(0))
        gap = abs(added - bill.total_kes) / bill.total_kes
        if gap > TOTALS_TOLERANCE:
            signals.append(
                Signal("totals_mismatch", {"lines": str(added), "total": str(bill.total_kes)})
            )
    wrong = [
        item
        for item in items
        if item.quantity is not None
        and item.unit_kes is not None
        and abs(item.quantity * item.unit_kes - item.amount_kes) > LINE_TOLERANCE
    ]
    if wrong:
        signals.append(Signal("line_math_mismatch", {"lines": len(wrong)}))
    return signals


# --------------------------------------------------------------------------- fonts


def font_inconsistent(words: list[Word]) -> bool:
    """True if the amounts on the bill are not all set in the fonts the bill uses elsewhere.

    The total is suspect if its font appears nowhere else on the page (an edited figure is often
    typed in another typeface), and so are two or more line amounts in a font that most line
    amounts do not use.
    """
    if not words:
        return False
    lines: dict[tuple[int, int], list[Word]] = {}
    for word in words:
        lines.setdefault((word.page, round(word.top / 3)), []).append(word)
    money: list[Word] = []
    other: list[Word] = []
    for members in lines.values():
        ordered = sorted(members, key=lambda w: w.x0)
        target = money if MONEY_LINE_RE.match(" ".join(w.text for w in ordered)) else other
        target.extend(ordered)

    def is_amount(word: Word, shortest: int) -> bool:
        cleaned = word.text.replace(",", "")
        return len(cleaned) >= shortest and bool(AMOUNT_RE.fullmatch(word.text))

    elsewhere = {w.fontname for w in other}
    if any(w.fontname not in elsewhere for w in money if is_amount(w, 2)):
        return True
    amounts = [w for w in other if is_amount(w, 4)]
    if not amounts:
        return False
    usual = Counter(w.fontname for w in amounts).most_common(1)[0][0]
    return sum(1 for w in amounts if w.fontname != usual) >= ODD_FONT_AMOUNTS


# --------------------------------------------------------------------------- photographs


def _open(raw: bytes) -> Image.Image | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def ela_score(image: Image.Image) -> float:
    """How uneven the re-compression error is across the page: the 95th-percentile tile's error
    over the average tile's. A pasted-in patch that was compressed differently stands out; a
    photograph that was compressed once is even. Only tiles with something printed on them count."""
    image = image.convert("RGB")
    if max(image.size) > ELA_SIDE:
        image.thumbnail((ELA_SIDE, ELA_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=ELA_QUALITY)
    buffer.seek(0)
    error = np.asarray(ImageChops.difference(image, Image.open(buffer)).convert("L"), dtype=float)
    grey = np.asarray(image.convert("L"), dtype=float)
    height, width = grey.shape
    tiles = [
        error[y : y + ELA_TILE, x : x + ELA_TILE].mean()
        for y in range(0, height - ELA_TILE, ELA_TILE)
        for x in range(0, width - ELA_TILE, ELA_TILE)
        if grey[y : y + ELA_TILE, x : x + ELA_TILE].std() >= 8
    ]
    if len(tiles) < 8:
        return 0.0
    mean = float(np.mean(tiles))
    return float(np.percentile(tiles, 95) / mean) if mean > 0 else 0.0


def exif_software(image: Image.Image) -> str | None:
    value = image.getexif().get(SOFTWARE_TAG)
    return (
        value.decode(errors="ignore")
        if isinstance(value, bytes)
        else (str(value) if value else None)
    )


def image_signals(raw: bytes) -> list[Signal]:
    """Signals from a photograph or scan: an editor named in its EXIF, an uneven re-compression."""
    image = _open(raw)
    if image is None:
        return []
    signals: list[Signal] = []
    software = (exif_software(image) or "").casefold()
    known = lexicon.read_terms(lexicon.Path(lexicon.lexicon_dir()) / "editor_software.txt")
    if software and any(term in software for term in known):
        signals.append(Signal("exif_editor_software"))
    score = ela_score(image)
    if score > ELA_HIGH:
        signals.append(Signal("image_ela_high", {"score": round(score, 2)}))
    return signals
