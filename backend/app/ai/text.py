"""Reading the text of an uploaded bill: the PDF text layer first, OCR when there is none.

* A PDF page with a real text layer (at least ``MIN_TEXT_CHARS`` characters) is read as it is,
  with each word's font and size, which the tamper checks use. Characters that another character
  was later drawn over (an amount covered by a white box and retyped) are not part of what a
  reader sees, so they are left out of the text and reported separately as ``hidden_words``.
* A PDF page without one (a scan) is rasterised and read with Tesseract.
* A PNG or JPEG is read with Tesseract. If the first read is poor the photo may be rotated, so
  the page orientation is asked for and the better of the two reads is kept.

Page one is also returned as an image, for the perceptual hash that finds the same bill
submitted twice. Nothing here stores or logs text. If Tesseract is not installed the result says
so (``ocr_available`` is False) instead of failing, and the bill simply has less to check.
"""

import io
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import pdfplumber
from pdf2image import convert_from_bytes
from pdfminer.layout import LTChar, LTRect
from PIL import Image, ImageOps, UnidentifiedImageError
from pytesseract import Output, TesseractError, TesseractNotFoundError, image_to_data, image_to_osd

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 50  # per page: below this a PDF page is treated as a scan
OCR_DPI = 200
PREVIEW_DPI = 100
MAX_RASTER_SIDE = 3200  # pixels; a poster-sized page is scaled down to this
MAX_PAGES = 10
MAX_OCR_PAGES = 5
OCR_TIMEOUT_SECONDS = 60
RENDER_TIMEOUT_SECONDS = 60
POOR_CONFIDENCE = 55.0  # mean word confidence (0 to 100) below which a rotated read is tried
OCR_LANGUAGE = "eng"

Method = Literal["text_layer", "ocr", "mixed", "none"]


@dataclass(frozen=True)
class Word:
    """One word from a PDF text layer, with where it sits and how it is set."""

    text: str
    fontname: str
    size: float
    page: int
    x0: float
    top: float


@dataclass
class ExtractedText:
    """Everything read from a bill. ``text`` joins the pages with a blank line.

    ``confidence`` is the mean OCR word confidence (0 to 100) over the pages that were OCR'd, or
    None if none were. ``words`` carries fonts for text-layer pages only.
    """

    text: str = ""
    page_texts: list[str] = field(default_factory=list)
    method: Method = "none"
    page_count: int = 0
    ocr_pages: list[int] = field(default_factory=list)
    ocr_available: bool = True
    confidence: float | None = None
    words: list[Word] = field(default_factory=list)
    first_page: Image.Image | None = None
    hidden_words: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def readable(self) -> bool:
        return bool(self.text.strip())


# --------------------------------------------------------------------------- entry point


def extract(raw: bytes, mime_type: str) -> ExtractedText:
    """Read ``raw`` (a PDF, PNG or JPEG already validated at upload) into an ``ExtractedText``."""
    if mime_type == "application/pdf":
        return _extract_pdf(raw)
    if mime_type in ("image/png", "image/jpeg"):
        return _extract_image(raw)
    return ExtractedText(error="unsupported_type")


# --------------------------------------------------------------------------- PDFs


def _extract_pdf(raw: bytes) -> ExtractedText:
    result = ExtractedText()
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            result.page_count = len(pdf.pages)
            pages = pdf.pages[:MAX_PAGES]
            for index, page in enumerate(pages, start=1):
                hidden, redundant = _hidden_chars(page)
                if hidden or redundant:
                    result.hidden_words.extend(_hidden_text(page, hidden))
                    gone = {id(page.chars[i]) for i in hidden | redundant}
                    page = page.filter(
                        lambda obj, gone=gone: obj["object_type"] != "char" or id(obj) not in gone
                    )
                layer = (page.extract_text() or "").strip()
                if len(layer) >= MIN_TEXT_CHARS:
                    result.page_texts.append(layer)
                    result.words.extend(_words(page, index))
                else:
                    result.page_texts.append(layer)  # may be replaced by OCR below
                    result.ocr_pages.append(index)
            dimensions = [(float(p.width), float(p.height)) for p in pages]
    except Exception as exc:  # noqa: BLE001 - a damaged file must not stop the triage job
        logger.warning("could not read a PDF (%s)", type(exc).__name__)
        result.error = "unreadable_pdf"
        return result

    if result.ocr_pages:
        _ocr_pdf_pages(raw, result, dimensions)
    result.method = _method(result)
    result.text = "\n\n".join(t for t in result.page_texts if t)
    result.first_page = _render_page(raw, 1, PREVIEW_DPI, dimensions[0] if dimensions else None)
    return result


OVERLAP_SHARE = 0.5  # a character is hidden if this share of it lies under a later, different one
GRID = 12  # points; the cell size of the lookup that keeps the overlap search quick
WHITE = 0.95  # a fill colour with every channel at least this bright counts as white


def _is_white(color: Any) -> bool:
    """Gray, RGB or CMYK white. Anything else (a coloured band, a table shade) is not a cover."""
    if isinstance(color, (int, float)):
        return float(color) >= WHITE
    if isinstance(color, (tuple, list)) and color:
        values = [float(v) for v in color]
        if len(values) == 4:  # CMYK: no ink at all
            return all(v <= 1 - WHITE for v in values)
        return all(v >= WHITE for v in values)
    return False


def _covers_by_box(page: Any) -> set[int]:
    """Indexes (into ``page.chars``) of characters drawn before a white box that lies over them.

    The text layer keeps what was typed first even after a white rectangle is drawn over it and a
    new value is typed on top, which is how a total is changed in a PDF editor. The page layout
    keeps the order things were drawn in, so a character that comes before a white box and sits
    under it is hidden; text drawn after the box is what a reader sees.
    """
    order: list[tuple[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, LTChar):
            order.append(("char", node))
        elif isinstance(node, LTRect):
            if node.fill and _is_white(node.non_stroking_color):
                order.append(("box", node))
        elif hasattr(node, "__iter__"):
            for child in node:
                walk(child)

    walk(page.layout)
    chars_in_layout = sum(1 for kind, _ in order if kind == "char")
    if chars_in_layout != len(page.chars):
        return set()  # the two views disagree; do not guess
    hidden: set[int] = set()
    seen = 0
    boxes: list[tuple[int, Any]] = []
    for kind, node in order:
        if kind == "char":
            seen += 1
        else:
            boxes.append((seen, node))  # drawn after the first `seen` characters
    for index, char in enumerate(page.chars):
        if not char["text"].strip():
            continue
        cx, cy = (char["x0"] + char["x1"]) / 2, (char["y0"] + char["y1"]) / 2
        for drawn_after, box in boxes:
            if (
                drawn_after > index
                and box.x0 - 0.5 <= cx <= box.x1 + 0.5
                and box.y0 - 0.5 <= cy <= box.y1 + 0.5
            ):
                hidden.add(index)
                break
    return hidden


def _hidden_chars(page: Any) -> tuple[set[int], set[int]]:
    """Indexes (into ``page.chars``) of characters a reader cannot see.

    The first set is *hidden* text: under a white box drawn later, or under a different character
    drawn later at the same spot. The second is *redundant*: the same character drawn twice at one
    spot (a way to fake bold), where the earlier copy changes nothing a reader sees and is not a
    sign of tampering. Both are left out of the text; only the first is reported.
    """
    chars = page.chars
    hidden = _covers_by_box(page)
    redundant: set[int] = set()
    cells: dict[tuple[int, int], list[int]] = {}
    for index, char in enumerate(chars):
        if not char["text"].strip():
            continue
        for gx in range(int(char["x0"] // GRID), int(char["x1"] // GRID) + 1):
            for gy in range(int(char["top"] // GRID), int(char["bottom"] // GRID) + 1):
                cells.setdefault((gx, gy), []).append(index)
    checked: set[tuple[int, int]] = set()
    for members in cells.values():
        for position, i in enumerate(members):
            for j in members[position + 1 :]:
                if (i, j) in checked:
                    continue
                checked.add((i, j))
                if not _overlap(chars[i], chars[j]):
                    continue
                earlier = min(i, j)
                if chars[i]["text"] == chars[j]["text"]:
                    redundant.add(earlier)
                else:
                    hidden.add(earlier)
    return hidden, redundant - hidden


def _overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """True if more than half of the smaller character lies under the other."""
    width = min(first["x1"], second["x1"]) - max(first["x0"], second["x0"])
    height = min(first["bottom"], second["bottom"]) - max(first["top"], second["top"])
    if width <= 0 or height <= 0:
        return False
    smaller = min(
        (first["x1"] - first["x0"]) * (first["bottom"] - first["top"]),
        (second["x1"] - second["x0"]) * (second["bottom"] - second["top"]),
    )
    return smaller > 0 and width * height / smaller > OVERLAP_SHARE


def _hidden_text(page: Any, hidden: set[int]) -> list[str]:
    """The hidden characters put back into reading order, one string per line."""
    lines: dict[int, list[dict[str, Any]]] = {}
    for index in hidden:
        char = page.chars[index]
        lines.setdefault(round(char["top"] / 4), []).append(char)
    return [_join(sorted(chars, key=lambda c: c["x0"])) for _, chars in sorted(lines.items())]


def _join(chars: list[dict[str, Any]]) -> str:
    """Characters in reading order as a string, with a space wherever there is a gap."""
    out = []
    previous = None
    for char in chars:
        if previous is not None and char["x0"] - previous["x1"] > 0.25 * char["size"]:
            out.append(" ")
        out.append(char["text"])
        previous = char
    return "".join(out).strip()


def _words(page: Any, number: int) -> list[Word]:
    return [
        Word(
            text=w["text"],
            fontname=str(w.get("fontname", "")),
            size=round(float(w.get("size", 0)), 1),
            page=number,
            x0=float(w["x0"]),
            top=float(w["top"]),
        )
        for w in page.extract_words(extra_attrs=["fontname", "size"])
    ]


def _dpi_for(dimensions: tuple[float, float] | None, wanted: int) -> int:
    """The resolution to render a page at: ``wanted``, less if the page is huge."""
    if not dimensions:
        return wanted
    longest_inches = max(dimensions) / 72
    return max(50, min(wanted, int(MAX_RASTER_SIDE / longest_inches)))


def _render_page(
    raw: bytes, page: int, dpi: int, dimensions: tuple[float, float] | None
) -> Image.Image | None:
    try:
        images = convert_from_bytes(
            raw,
            dpi=_dpi_for(dimensions, dpi),
            first_page=page,
            last_page=page,
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - rendering is best effort
        logger.warning("could not render a PDF page (%s)", type(exc).__name__)
        return None
    return images[0] if images else None


def _ocr_pdf_pages(
    raw: bytes, result: ExtractedText, dimensions: list[tuple[float, float]]
) -> None:
    """OCR the pages that had no text layer (up to ``MAX_OCR_PAGES``)."""
    confidences: list[float] = []
    done: list[int] = []
    for number in result.ocr_pages[:MAX_OCR_PAGES]:
        image = _render_page(raw, number, OCR_DPI, dimensions[number - 1])
        if image is None:
            continue
        read = _ocr(image)
        if read is None:
            result.ocr_available = False
            break
        text, confidence = read
        result.page_texts[number - 1] = text
        done.append(number)
        if confidence is not None:
            confidences.append(confidence)
    result.ocr_pages = done
    result.confidence = _mean(confidences)


# --------------------------------------------------------------------------- images


def _extract_image(raw: bytes) -> ExtractedText:
    result = ExtractedText(page_count=1)
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        logger.warning("could not read an image (%s)", type(exc).__name__)
        result.error = "unreadable_image"
        return result
    result.first_page = image.convert("RGB")
    read = _ocr(image)
    if read is None:
        result.ocr_available = False
        return result
    text, confidence = read
    result.page_texts = [text]
    result.text = text
    result.ocr_pages = [1]
    result.confidence = confidence
    result.method = "ocr" if text.strip() else "none"
    return result


# --------------------------------------------------------------------------- OCR


def _method(result: ExtractedText) -> Method:
    if not result.page_texts or not any(result.page_texts):
        return "none"
    ocr_done = set(result.ocr_pages)
    pages = set(range(1, len(result.page_texts) + 1))
    if not ocr_done:
        return "text_layer"
    return "ocr" if ocr_done == pages else "mixed"


def _prepare(image: Image.Image) -> Image.Image:
    """Grey, contrast-stretched: enough to help a shadowed or faded photo without distorting it."""
    return ImageOps.autocontrast(image.convert("L"), cutoff=1)


def _ocr(image: Image.Image) -> tuple[str, float | None] | None:
    """Text and mean confidence from Tesseract, or None if Tesseract is not installed.

    A poor first read triggers one more try with the page turned upright, if Tesseract can say
    which way up it is; the read with the higher confidence wins.
    """
    prepared = _prepare(image)
    try:
        best = _read(prepared)
        if best[1] is None or best[1] < POOR_CONFIDENCE:
            turned = _turned_upright(prepared)
            if turned is not None:
                other = _read(turned)
                if (other[1] or 0) > (best[1] or 0):
                    best = other
    except TesseractNotFoundError:
        return None
    except (TesseractError, RuntimeError) as exc:
        logger.warning("OCR failed (%s)", type(exc).__name__)
        return "", None
    return best


def _read(image: Image.Image) -> tuple[str, float | None]:
    data = image_to_data(
        image, lang=OCR_LANGUAGE, output_type=Output.DICT, timeout=OCR_TIMEOUT_SECONDS
    )
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        confidence = float(data["conf"][i])
        if confidence >= 0:
            confidences.append(confidence)
    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    return text, _mean(confidences)


def _turned_upright(image: Image.Image) -> Image.Image | None:
    try:
        osd = image_to_osd(image, output_type=Output.DICT, timeout=OCR_TIMEOUT_SECONDS)
    except (TesseractError, RuntimeError):
        return None
    degrees = int(osd.get("rotate", 0))
    if degrees == 0:
        return None
    return image.rotate(-degrees, expand=True)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None
