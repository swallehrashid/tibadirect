"""The signals the checks can raise, their weights, and which ones send a bill to a person.

Weights and the "forces manual review" flags come from the specification (7.6 to 7.8); the risk
score (``ai/risk.py``) is the capped sum of the weights of the signals raised. A signal carries
only a code and short, personal-data-free detail: it is stored with the triage result and shown
to the hospital and admins.
"""

from dataclasses import dataclass, field
from typing import Any

# code -> weight
WEIGHTS: dict[str, int] = {
    # duplicates (7.6)
    "duplicate_exact": 60,
    "duplicate_near": 45,
    "duplicate_fingerprint": 45,
    "patient_active_elsewhere": 30,
    # facility (7.7), plus the letterhead contact comparison
    "facility_not_in_register": 25,
    "facility_weak_match": 10,
    "facility_mismatch_selected": 20,
    "facility_code_mismatch": 20,
    "contact_mismatch": 20,
    # tamper and extraction (7.8)
    "pdf_incremental_updates": 15,
    "pdf_editor_software": 15,
    "pdf_modified_after_creation": 10,
    "totals_mismatch": 25,
    "line_math_mismatch": 20,
    "hidden_text_under_overlay": 30,
    "font_inconsistency": 10,
    "image_ela_high": 15,
    "exif_editor_software": 10,
    "low_extraction_confidence": 10,
    "extraction_disagreement": 10,
    "instruction_like_text": 15,
}

# Signals that send a bill to a person whatever the total score is (7.6).
FORCES_MANUAL_REVIEW = frozenset(
    {"duplicate_exact", "duplicate_near", "duplicate_fingerprint", "patient_active_elsewhere"}
)


@dataclass(frozen=True)
class Signal:
    """One thing a check found. ``detail`` holds codes and numbers, never document text."""

    code: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def weight(self) -> int:
        return WEIGHTS[self.code]

    @property
    def forces_manual_review(self) -> bool:
        return self.code in FORCES_MANUAL_REVIEW

    def as_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "weight": self.weight,
            "forces_manual_review": self.forces_manual_review,
            "detail": self.detail,
        }
