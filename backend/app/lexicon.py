"""Word lists from ``data/lexicons`` and the matching that uses them.

``medical_terms.txt`` holds diagnoses, procedures, tests and drugs (English and common Swahili).
It backs the story check on public pages here and, in Phase 3, the redaction of bill text.
"""

import re
import unicodedata
from functools import cache
from pathlib import Path

from flask import current_app

DEFAULT_LEXICON_DIR = Path(__file__).resolve().parents[2] / "data" / "lexicons"


def _fold(text: str) -> str:
    """Case-folded, accent-free, single-spaced: the form terms are compared in."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


def read_terms(path: Path) -> tuple[str, ...]:
    """Terms from one lexicon file: one per line, ``#`` comments and blank lines skipped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(
        sorted(
            {_fold(line) for line in lines if line.strip() and not line.lstrip().startswith("#")}
        )
    )


@cache
def _medical_pattern(directory: str) -> re.Pattern[str]:
    terms = read_terms(Path(directory) / "medical_terms.txt")
    # Longest first, so "breast cancer" is reported rather than just "cancer".
    alternatives = "|".join(
        r"\s+".join(re.escape(word) for word in term.split())
        for term in sorted(terms, key=len, reverse=True)
    )
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")


def lexicon_dir() -> str:
    """The configured lexicon folder, or the repository's own when there is no application (the
    evaluation command and scripts use the word lists too)."""
    try:
        configured = current_app.config.get("LEXICON_DIR") or str(DEFAULT_LEXICON_DIR)
    except RuntimeError:
        configured = str(DEFAULT_LEXICON_DIR)
    return str(configured)


def find_medical_terms(text: str) -> list[str]:
    """Medical terms in ``text`` (whole words or phrases), each once, in order of appearance."""
    found: list[str] = []
    for match in _medical_pattern(lexicon_dir()).finditer(_fold(text)):
        term = " ".join(match.group(0).split())
        if term not in found:
            found.append(term)
    return found
