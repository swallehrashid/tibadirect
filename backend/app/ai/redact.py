"""Removing medical and personal details from a bill's text, before it is stored or sent anywhere.

Only the redacted text is ever stored or shown to a language model. What stays is what the checks
need: the facility name, the invoice number, dates of service, line-item amounts, totals and the
currency. What goes is replaced by a token such as ``[REDACTED:DIAGNOSIS]``:

1. **Labelled values.** After a label such as ``Diagnosis:``, ``Patient:``, ``ID No.:`` or
   ``Tel:`` the value runs to the next label on the line or the end of the line. The patient
   reference (``Patient ref.:``) becomes ``[PATIENT_REF]``; its value is handed back separately,
   in memory, so the caller can compare it with the case and encrypt it.
2. **Medical terms.** Any diagnosis, procedure, test or drug in ``data/lexicons/medical_terms.txt``,
   wherever it appears, including inside a line item (``Theatre charges - [REDACTED:MEDICAL]``).
   A term misread by OCR is caught by a close-match pass over what is left.
3. **Patterns.** Email addresses, phone numbers, national ID numbers and dates of birth.
4. **Titled names.** ``Dr. Jane Wanjiru`` or ``Mrs Amina Otieno`` anywhere in the text.

The function is idempotent: redacting redacted text changes nothing.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from rapidfuzz import fuzz, process

from app import lexicon

PATIENT_REF_TOKEN = "[PATIENT_REF]"  # noqa: S105 - a placeholder, not a credential
MEDICAL = "MEDICAL"
FUZZY_MIN_LENGTH = 8  # shorter words are too easily confused with ordinary ones
FUZZY_CUTOFF = 88.0
FUZZY_MAX_WORDS = 4

# label text (a regex fragment, matched case-insensitively) -> what its value is. A *strong* label
# is unmistakable even without a colon ("Patient ref. IP/26/73179"); a *weak* one is an ordinary
# word ("Test", "Ward", "Patient") and counts only with a colon after it.
Label = tuple[str, bool]  # (regex fragment, strong)
LABELS: dict[str, tuple[Label, ...]] = {
    "DIAGNOSIS": (
        (r"provisional\s+diagnosis", True),
        (r"admitting\s+diagnosis", True),
        (r"final\s+diagnosis", True),
        (r"diagnosis", False),
        (r"dx", False),
    ),
    "PROCEDURE": (
        (r"procedure", False),
        (r"proc", False),
        (r"operation", False),
        (r"surgery", False),
        (r"theatre", False),
    ),
    "WARD": ((r"ward", False),),
    "DRUG": (
        (r"medications?", False),
        (r"drugs?", False),
        (r"prescription", False),
        (r"rx", False),
    ),
    "TEST": ((r"investigations?", False), (r"labs?", False), (r"tests?", False)),
    "NOTES": ((r"clinical\s+notes?", True),),
    "NAME": (
        (r"patient\s+name", True),
        (r"next\s+of\s+kin", True),
        (r"prepared\s+by", True),
        (r"served\s+by", True),
        (r"attended\s+by", True),
        (r"attending\s+doctor", True),
        (r"billed\s+to", False),
        (r"patient", False),
        (r"(?<!facility\s)(?<!hospital\s)(?<!business\s)name", False),
        (r"guarantor", False),
        (r"nok", False),
        (r"doctor", False),
        (r"consultant", False),
    ),
    "ID": (
        (r"national\s+id(?:\s+no\.?|\s+number)?", True),
        (r"id\s+(?:no\.?|number)", True),
        (r"passport(?:\s+no\.?|\s+number)?", True),
        (r"nhif(?:\s+no\.?|\s+number)?", True),
        (r"sha\s+(?:no\.?|number)", True),
        (r"(?:insurance|member|policy)\s+(?:no\.?|number)", True),
    ),
    "PHONE": (
        (r"telephone", False),
        (r"phone", False),
        (r"mobile", False),
        (r"cell", False),
        (r"contact", False),
        (r"tel", False),
    ),
    "EMAIL": ((r"e-?mail", False),),
    "DOB": (
        (r"date\s+of\s+birth", True),
        (r"d\.?o\.?b\.?", False),
        (r"born", False),
        (r"age", False),
    ),
    "PATIENT_REF": (
        (r"patient\s+ref(?:erence)?\.?(?:\s+no\.?)?", True),
        (r"(?:admission|hospital|patient|file)\s+(?:no\.?|number)", True),
        (r"ip\s+(?:no\.?|number)", True),
        (r"mrn", True),
        (r"ref\.?", False),
    ),
}
# Labels whose values stay: they end a redacted value but are not themselves redacted.
KEEP_LABELS: tuple[Label, ...] = (
    (r"(?:invoice|estimate|receipt|bill|account)\s+(?:no\.?|number)", True),
    (r"(?:invoice|estimate|admission|discharge|service|bill)\s+date", True),
    (r"facility\s+reg(?:istration)?\.?\s+(?:no\.?|number)", True),
    (r"reg(?:istration)?\.?\s+(?:no\.?|number)", True),
    (r"date", True),
    (r"no\.?", False),
    (r"total", False),
    (r"balance", False),
    (r"deposit", False),
    (r"amount", False),
    (r"signature", False),
    (r"sign", False),
    (r"stamp", False),
)

# A line like "BILLED TO DETAILS" is followed by the patient's name with no label of its own.
NAME_HEADER_RE = re.compile(
    r"^\s*(?:bill(?:ed)?\s+to|patient|customer|client)(?:\s+(?:details|information))?\s*:?\s*$",
    re.IGNORECASE,
)

_WORD_END = r"(?![A-Za-z])"


def _group(name: str, labels: tuple[Label, ...]) -> str:
    strong = "|".join(f for f, is_strong in labels if is_strong)
    weak = "|".join(f for f, is_strong in labels if not is_strong)
    parts = []
    if strong:
        parts.append(rf"(?:{strong}){_WORD_END}\s*[:;]?")
    if weak:
        parts.append(rf"(?:{weak}){_WORD_END}\s*[:;]")
    return f"(?P<{name}>{'|'.join(parts)})"


def _label_pattern() -> re.Pattern[str]:
    groups = [_group(kind.lower(), labels) for kind, labels in LABELS.items()]
    groups.append(_group("keep", KEEP_LABELS))
    return re.compile(rf"(?<![A-Za-z])(?:{'|'.join(groups)})", re.IGNORECASE)


LABEL_RE = _label_pattern()

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Kenyan numbers, any spacing: +254 7xx / 1xx, 07xx / 01xx, landlines with an area code, and
# bracketed area codes such as (057) 5550303.
PHONE_RE = re.compile(
    r"(?<![\w/.,])(?:\+\s?254[\s-]?|\(?0)\d{1,3}\)?[\s-]?\d{3}[\s-]?\d{3,4}(?![\w/]|\.\d)"
)
# Seven or eight digits standing alone, the shape of a national ID or an insurance member number.
ID_RE = re.compile(r"(?<![\w/.,-])\d{7,8}(?![\w/.,-]|\.\d)")
# A line about money is not a place to look for an ID number.
MONEY_LINE_RE = re.compile(
    r"\b(?:total|sub-?total|balance|deposit|amount|paid|due|kes|ksh|kshs)\b", re.IGNORECASE
)
SHA_RE = re.compile(r"\b(?:SHA|NHIF)[-\s]?\d{6,}\b", re.IGNORECASE)
DOB_RE = re.compile(r"\b(?:d\.?o\.?b\.?|born)\s*[:;]?\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", re.I)
TITLE_RE = re.compile(
    r"\b(?:Dr|Prof|Mr|Mrs|Ms|Miss|Master|Baby|Mzee|Bi|Bw)\.?\s+"
    r"[A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){0,2}"
)


@dataclass
class Redacted:
    """The redacted text and what was found, without the sensitive values themselves.

    ``patient_references`` holds the reference value(s) as printed, for the caller to compare
    with the case and encrypt; it must never be logged or stored with the text.
    """

    text: str
    counts: dict[str, int] = field(default_factory=dict)
    patient_references: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def token(kind: str) -> str:
    return f"[REDACTED:{kind}]"


# --------------------------------------------------------------------------- the lexicon


def _flat(text: str) -> str:
    """Lower case with accents removed, one character for every character in (same length)."""
    out = []
    for char in text:
        base = "".join(
            c for c in unicodedata.normalize("NFKD", char) if not unicodedata.combining(c)
        )
        folded = base.lower()
        out.append(folded if len(folded) == 1 else char)
    return "".join(out)


@cache
def _terms(directory: str) -> tuple[str, ...]:
    return lexicon.read_terms(Path(directory) / "medical_terms.txt")


@cache
def _medical_re(directory: str) -> re.Pattern[str]:
    terms = sorted(_terms(directory), key=len, reverse=True)
    alternatives = "|".join(r"\s+".join(re.escape(word) for word in term.split()) for term in terms)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


def _redact_terms(text: str, counts: dict[str, int]) -> str:
    """Replace every lexicon term, keeping the text around it exactly as it was."""
    pattern = _medical_re(lexicon.lexicon_dir())
    flat = _flat(text)
    pieces = []
    last = 0
    for match in pattern.finditer(flat):
        pieces.append(text[last : match.start()])
        pieces.append(token(MEDICAL))
        last = match.end()
        counts[MEDICAL] = counts.get(MEDICAL, 0) + 1
    pieces.append(text[last:])
    return "".join(pieces)


def _redact_close_matches(text: str, counts: dict[str, int]) -> str:
    """Catch a lexicon term that OCR misread ("Obstructed Iabour"): a run of words that is a very
    close match to a term, with no digits, is replaced. Short words are never matched this way."""
    terms = _terms(lexicon.lexicon_dir())
    candidates = [t for t in terms if len(t) >= FUZZY_MIN_LENGTH]
    if not candidates:
        return text
    out_lines = []
    for line in text.split("\n"):
        out_lines.append(_close_matches_in_line(line, candidates, counts))
    return "\n".join(out_lines)


def _close_matches_in_line(line: str, terms: list[str], counts: dict[str, int]) -> str:
    words = list(re.finditer(r"\S+", line))
    if not words:
        return line
    replace: list[tuple[int, int]] = []
    index = 0
    while index < len(words):
        matched = False
        for size in range(min(FUZZY_MAX_WORDS, len(words) - index), 0, -1):
            span = words[index : index + size]
            phrase = line[span[0].start() : span[-1].end()]
            core = re.sub(r"[^\w\s'-]", "", phrase)
            if len(core) < FUZZY_MIN_LENGTH or any(c.isdigit() for c in core) or "[" in phrase:
                continue
            if process.extractOne(_flat(core), terms, scorer=fuzz.ratio, score_cutoff=FUZZY_CUTOFF):
                replace.append((span[0].start(), span[-1].end()))
                index += size
                matched = True
                break
        if not matched:
            index += 1
    for start, end in reversed(replace):
        line = line[:start] + token(MEDICAL) + line[end:]
        counts[MEDICAL] = counts.get(MEDICAL, 0) + 1
    return line


# --------------------------------------------------------------------------- labels and patterns


def _names_header(line: str) -> bool:
    return bool(NAME_HEADER_RE.match(line))


def _redact_labelled(
    line: str, counts: dict[str, int], references: list[str], *, name_first: bool = False
) -> str:
    """Replace the value after each redactable label on one line.

    With ``name_first`` the line follows a header such as "BILLED TO", so the words before its
    first label are the patient's name.
    """
    matches = list(LABEL_RE.finditer(line))
    out: list[str] = []
    cursor = 0
    if name_first and line.strip() and not line.lstrip().startswith("["):
        end = matches[0].start() if matches else len(line)
        head = line[:end]
        if head.strip():
            lead = head[: len(head) - len(head.lstrip())]
            gap = head[len(head.rstrip()) :]
            out.append(f"{lead}{token('NAME')}{gap}")
            counts["NAME"] = counts.get("NAME", 0) + 1
            cursor = end
    for position, match in enumerate(matches):
        out.append(line[cursor : match.end()])
        value_end = matches[position + 1].start() if position + 1 < len(matches) else len(line)
        value = line[match.end() : value_end]
        cursor = value_end
        kind = next((k for k in (*LABELS, "keep") if match.group(k.lower())), "keep")
        stripped = value.strip()
        if (
            kind == "keep"
            or not stripped
            or stripped.startswith("[REDACTED")
            or stripped == PATIENT_REF_TOKEN
        ):
            out.append(value)
            continue
        lead = value[: len(value) - len(value.lstrip())]
        trail = value[len(value.rstrip()) :]
        if kind == "PATIENT_REF":
            references.append(stripped)
            replacement = PATIENT_REF_TOKEN
        else:
            replacement = token(kind)
        counts[kind] = counts.get(kind, 0) + 1
        out.append(f"{lead}{replacement}{trail}")
    out.append(line[cursor:])
    return "".join(out)


def _sub(pattern: re.Pattern[str], text: str, kind: str, counts: dict[str, int]) -> str:
    def swap(match: re.Match[str]) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        return token(kind)

    return pattern.sub(swap, text)


def redact(text: str) -> Redacted:
    """Remove medical and personal details from ``text``. See the module docstring for the rules."""
    counts: dict[str, int] = {}
    references: list[str] = []
    lines: list[str] = []
    previous = ""
    for line in text.split("\n"):
        lines.append(_redact_labelled(line, counts, references, name_first=_names_header(previous)))
        previous = line
    result = "\n".join(lines)
    result = _sub(EMAIL_RE, result, "EMAIL", counts)
    result = _sub(DOB_RE, result, "DOB", counts)
    result = _sub(SHA_RE, result, "ID", counts)
    result = _sub(PHONE_RE, result, "PHONE", counts)
    result = "\n".join(
        line if MONEY_LINE_RE.search(line) else _sub(ID_RE, line, "ID", counts)
        for line in result.split("\n")
    )
    result = _sub(TITLE_RE, result, "NAME", counts)
    result = _redact_terms(result, counts)
    result = _redact_close_matches(result, counts)
    return Redacted(result, counts, references)


def residual(text: str) -> list[str]:
    """What is still in ``text`` that redaction should have removed: a list of kinds, empty if
    the text is clean. Used as a last check before text leaves the server."""
    found = []
    if lexicon.find_medical_terms(text):
        found.append(MEDICAL)
    if EMAIL_RE.search(text):
        found.append("EMAIL")
    if PHONE_RE.search(text):
        found.append("PHONE")
    if any(not MONEY_LINE_RE.search(line) and ID_RE.search(line) for line in text.split("\n")):
        found.append("ID")
    return found
