"""Checking the facility on a bill against the public facility register.

* The name on the bill is fuzzy-matched to the register (``token_set_ratio``). A name with no
  close match, a weak match, or a best match that is not the hospital the family chose all add
  to the risk score.
* The register code printed on the bill, if any, must be the matched facility's own: a name one
  typo away from a real hospital with another hospital's code is a classic copy.
* The phone number and email on the letterhead are compared with the register's official
  contacts. A mismatch is a signal; it never changes where confirmation codes go, which is always
  the register's contact and nothing printed on a bill.

Contacts are read from the original text, in memory, and only a match or mismatch is kept.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.extract import ExtractedBill
from app.ai.redact import EMAIL_RE, PHONE_RE
from app.ai.signals import Signal
from app.models.hospital import FacilityRegisterEntry, Hospital, normalize_facility_name

NOT_IN_REGISTER_BELOW = 60.0
WEAK_BELOW = 85.0
LETTERHEAD_LINES = 8
CODE_RE = re.compile(r"\bFR\s?-?\s?([A-Z]{3})\s?-?\s?([0-9OIL]{4})\b")


@dataclass(frozen=True)
class FacilityMatch:
    """The register entry a name matches best, and how well (0 to 100)."""

    entry: FacilityRegisterEntry | None
    score: float


@dataclass
class FacilityResult:
    match: FacilityMatch
    signals: list[Signal] = field(default_factory=list)


def best_match(session: Session, facility_name: str | None) -> FacilityMatch:
    """The register entry whose name is closest to ``facility_name``."""
    if not facility_name or not facility_name.strip():
        return FacilityMatch(None, 0.0)
    wanted = normalize_facility_name(facility_name)
    best: tuple[float, float, FacilityRegisterEntry] | None = None
    for entry in session.execute(select(FacilityRegisterEntry)).scalars():
        candidate = (
            fuzz.token_set_ratio(wanted, entry.normalized_name),
            fuzz.ratio(wanted, entry.normalized_name),  # breaks ties toward the closer spelling
            entry,
        )
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return FacilityMatch(None, 0.0)
    return FacilityMatch(best[2], round(best[0], 1))


def printed_register_code(raw_text: str) -> str | None:
    """A register code such as ``FR-NBI-0101`` printed on the bill, with OCR slips corrected."""
    for line in raw_text.split("\n")[: LETTERHEAD_LINES + 4]:
        match = CODE_RE.search(line.upper())
        if match:
            digits = match.group(2).replace("O", "0").replace("I", "1").replace("L", "1")
            return f"FR-{match.group(1)}-{digits}"
    return None


def _last_nine_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)[-9:]


def letterhead_contacts(raw_text: str) -> tuple[str | None, str | None]:
    """The first phone number and email in the top of the bill."""
    head = "\n".join(raw_text.split("\n")[:LETTERHEAD_LINES])
    phone = PHONE_RE.search(head)
    email = EMAIL_RE.search(head)
    return (phone.group(0) if phone else None, email.group(0).lower() if email else None)


def _code_differs(printed: str, official: str) -> bool:
    """True unless the codes agree, allowing one slipped character (OCR)."""
    if printed == official:
        return False
    if len(printed) != len(official):
        return True
    return sum(a != b for a, b in zip(printed, official, strict=True)) > 1


def check(
    session: Session, bill: ExtractedBill, raw_text: str, selected: Hospital | None
) -> FacilityResult:
    """Run the three facility checks and return the match and any signals."""
    match = best_match(session, bill.facility_name)
    result = FacilityResult(match)
    if match.entry is None or match.score < NOT_IN_REGISTER_BELOW:
        result.signals.append(Signal("facility_not_in_register", {"best_score": match.score}))
        return result
    if match.score < WEAK_BELOW:
        result.signals.append(Signal("facility_weak_match", {"best_score": match.score}))
    if selected is not None and match.entry.id != selected.register_entry_id:
        result.signals.append(Signal("facility_mismatch_selected", {"best_score": match.score}))
    code = printed_register_code(raw_text)
    if code and _code_differs(code, match.entry.register_code):
        result.signals.append(Signal("facility_code_mismatch"))
    phone, email = letterhead_contacts(raw_text)
    different = []
    if (
        phone
        and match.entry.official_phone
        and _last_nine_digits(phone) != _last_nine_digits(match.entry.official_phone)
    ):
        different.append("phone")
    if email and email != match.entry.official_email.lower():
        different.append("email")
    if different:
        result.signals.append(Signal("contact_mismatch", {"differs": different}))
    return result
