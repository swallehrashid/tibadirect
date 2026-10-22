"""Checking a forwarded fundraising appeal (a WhatsApp or Facebook message).

For anyone who received an appeal and wants to know whether to trust it. The text (or a screenshot,
read with OCR) is scored from two sources that are combined:

* a small text classifier (TF-IDF over words and characters, logistic regression) trained by
  ``flask ai train-appeals`` on the labelled messages in ``data/synthetic/appeals.jsonl``; and
* rules built on ``data/lexicons/scam_phrases.txt``: urgency, a personal number to send money to,
  pressure to keep quiet or not call the hospital, advance fees, no hospital named, a hospital not
  in the register, an amount with no invoice number. Reassuring habits (pay the hospital, check
  with its accounts office, give an invoice number) lower the score.

Whatever the person pasted is hashed and **never stored**. A message that names a live
TibaDirect case (by its link, or by hospital and alias) is matched to it. The score only advises:
the safe way to give is always to pay through a hospital-confirmed page, and the tip says so.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import joblib
from flask import current_app
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import lexicon
from app.ai import text as text_reader
from app.models.case import Case
from app.models.enums import AppealInputKind, RiskBand
from app.models.hospital import FacilityRegisterEntry, normalize_facility_name
from app.models.platform import AppealCheck
from app.security import crypto
from app.services import case_service

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parent / "models" / "appeal_clf.joblib"
MAX_TEXT_CHARS = 5000
HMAC_CONTEXT = "appeal_input"
FLAG_FROM = 50  # a score at or above this reads as "likely a scam"
MIN_HOSPITAL_SCORE = 80.0

SAFETY_TIP = (
    "Pay through the hospital-confirmed page, never to a personal number.",
    # needs native review
    "Lipa kupitia ukurasa uliothibitishwa na hospitali, kamwe si kwa namba ya simu ya mtu.",
)

# category -> points when any of its phrases is present
CATEGORY_POINTS = {
    "urgency": 12,
    "personal_payee": 25,
    "send_to_number": 8,
    "advance_fee": 25,
    "secrecy": 18,
    "verification_refusal": 25,
    "impersonation": 15,
    "pressure_to_share": 8,
    "vague_story": 5,
}
# Points that lower the score: habits of a genuine appeal.
TRUST_PHRASES = (
    "verify with", "confirm with the hospital", "accounts office", "billing office on",
    "directly to the hospital", "straight to the hospital", "not to a person", "invoice",
    "receipt", "thibitisha na", "ofisi ya malipo", "moja kwa moja kwa hospitali",
    "pay through the hospital", "hospital will confirm", "kamwe kwa mtu",
)  # fmt: skip
TRUST_POINTS = 15
NO_HOSPITAL_POINTS = 12
UNKNOWN_HOSPITAL_POINTS = 18
AMOUNT_NO_INVOICE_POINTS = 8
PERSONAL_NUMBER_POINTS = 20
RULE_WEIGHT = 0.75
MODEL_WEIGHT = 0.55

_PHONE = re.compile(r"(?<![\w/])(?:\+?254|0)[17]\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\w/])")
_AMOUNT = re.compile(
    r"\b(?:kes|ksh|kshs|sh)\.?\s*[\d,]{3,}|\b[\d,]{4,}\s*(?:kes|ksh|shillings?)\b", re.I
)
_INVOICE = re.compile(r"\b(?:invoice|inv|ankara|bill\s*(?:no|number)|ref(?:erence)?)\b", re.I)
_PAYMENT_APP = re.compile(r"\b(?:m-?pesa|mpesa|till|pochi|airtel\s*money)\b", re.I)
_HOSPITAL_WORDS = re.compile(
    r"\b(?:hospital|hospitali|medical\s+cent(?:re|er)|clinic|nursing\s+home|health\s+cent(?:re|er)|dispensary)\b",
    re.I,
)
_SLUG = re.compile(r"\b[a-z2-7]{12}\b")

REASONS: dict[str, tuple[str, str]] = {
    "personal_number": (
        "It asks you to send money to a personal phone number.",
        "Inakuomba utume pesa kwa namba ya simu ya mtu binafsi.",
    ),
    "urgency": (
        "It pushes you to act at once, with no time to check.",
        "Inakusukuma kutenda mara moja, bila muda wa kuthibitisha.",
    ),
    "personal_payee": (
        "It asks for money to go to a person, not to the hospital.",
        "Inaomba pesa zitumwe kwa mtu, si kwa hospitali.",
    ),
    "advance_fee": (
        "It asks for a fee before money can be released.",
        "Inaomba ada kabla ya pesa kutolewa.",
    ),
    "secrecy": (
        "It asks you to keep quiet or not to call the hospital.",
        "Inakuomba unyamaze au usipige simu hospitali.",
    ),
    "verification_refusal": (
        "It discourages you from checking with the hospital.",
        "Inakukatisha tamaa kuthibitisha na hospitali.",
    ),
    "impersonation": (
        "It claims to speak for the hospital's billing office but asks for payment elsewhere.",
        "Inadai kuwakilisha ofisi ya malipo ya hospitali lakini inaomba malipo mahali pengine.",
    ),
    "no_hospital": (
        "It does not say which hospital the bill is from.",
        "Haitaji hospitali ambayo bili inatoka.",
    ),
    "unknown_hospital": (
        "The hospital it names is not in the facility register.",
        "Hospitali iliyotajwa haimo kwenye rejista ya vituo vya afya.",
    ),
    "amount_no_invoice": (
        "It names an amount but no invoice number you could check.",
        "Inataja kiasi lakini hakuna nambari ya ankara unayoweza kuthibitisha.",
    ),
    "model": (
        "The wording is very like messages we have seen from scams.",
        "Maneno yake yanafanana sana na jumbe za utapeli tulizowahi kuona.",
    ),
    "pressure_to_share": (
        "It asks you to forward it to many people.",
        "Inakuomba uisambaze kwa watu wengi.",
    ),
    "reassuring": (
        "It tells you to pay the hospital and gives a way to check.",
        "Inakuambia ulipe hospitali na inatoa njia ya kuthibitisha.",
    ),
}
GENUINE_LOOKING = (
    "Nothing in it looks like the usual signs of a scam, but always check with the hospital.",
    "Hakuna dalili za kawaida za utapeli ndani yake, lakini thibitisha na hospitali kila mara.",
)


@dataclass
class Verdict:
    score: int
    band: RiskBand
    reasons_en: list[str]
    reasons_sw: list[str]
    flagged: bool
    probability: float | None = None
    matched_case: Case | None = None
    features: dict[str, int] = field(default_factory=dict)
    model_version: str | None = None


# --------------------------------------------------------------------------- the classifier


def _normalise(value: str) -> str:
    """Lower case, with phone numbers and amounts made alike so the model learns the habit, not
    the particular number."""
    value = _PHONE.sub(" phonenumber ", value.lower())
    value = re.sub(r"\d[\d,.]*", " number ", value)
    return " ".join(value.split())


def build_model() -> Any:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, Pipeline

    return Pipeline(
        [
            (
                "features",
                FeatureUnion(
                    [
                        (
                            "words",
                            TfidfVectorizer(
                                ngram_range=(1, 2), sublinear_tf=True, preprocessor=_normalise
                            ),
                        ),
                        (
                            "chars",
                            TfidfVectorizer(
                                analyzer="char_wb",
                                ngram_range=(3, 5),
                                sublinear_tf=True,
                                preprocessor=_normalise,
                            ),
                        ),
                    ]
                ),
            ),
            (
                "classifier",
                LogisticRegression(C=4.0, class_weight="balanced", max_iter=2000, random_state=42),
            ),
        ]
    )


_loaded: dict[str, Any] = {}


def load_model(path: Path | None = None) -> dict[str, Any] | None:
    """The trained model and its metadata, or None if it has not been trained.

    The file is this project's own artifact (written by ``flask ai train-appeals``); joblib files
    are pickles and must never be loaded from anywhere else.
    """
    path = path or MODEL_PATH
    key = str(path)
    if key not in _loaded:
        if not path.exists():
            logger.warning("appeal classifier not found; using rules only")
            return None
        _loaded[key] = joblib.load(path)
    return _loaded[key]  # type: ignore[no-any-return]


def forget_model() -> None:
    _loaded.clear()


def train(messages: list[dict[str, Any]], *, version: str | None = None) -> dict[str, Any]:
    """Fit the classifier on labelled messages and return the artifact to save."""
    model = build_model()
    model.fit([m["text"] for m in messages], [1 if m["label"] == "scam" else 0 for m in messages])
    return {
        "model": model,
        "version": version or date.today().isoformat(),
        "n_train": len(messages),
        "classes": ["genuine", "scam"],
    }


def probability(value: str) -> tuple[float | None, str | None]:
    artifact = load_model()
    if artifact is None:
        return None, None
    scam = float(artifact["model"].predict_proba([value])[0][1])
    return scam, artifact["version"]


# --------------------------------------------------------------------------- the rules


def _phrases() -> dict[str, list[str]]:
    """Category -> phrases from the lexicon (``category: phrase`` lines)."""
    path = Path(lexicon.lexicon_dir()) / "scam_phrases.txt"
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        category, _, phrase = line.partition(":")
        out.setdefault(category.strip(), []).append(phrase.strip().lower())
    return out


def register_names(session: Session) -> list[str]:
    return list(session.execute(select(FacilityRegisterEntry.normalized_name)).scalars())


def _hospital_named(names: list[str], value: str) -> tuple[bool, bool]:
    """(a register hospital is named, a hospital-like name is given that is not in the register)."""
    lowered = normalize_facility_name(value)
    for name in names:
        if name in lowered or fuzz.partial_ratio(name, lowered) >= 92:
            return True, False
    return False, bool(_HOSPITAL_WORDS.search(value))


def rule_features(names: list[str], value: str) -> dict[str, int]:
    """Points for each habit found in the message (positive for scam-like, negative for
    reassuring ones). Keys name the reason shown to the person."""
    lowered = value.lower()
    points: dict[str, int] = {}
    for category, phrases in _phrases().items():
        if category in CATEGORY_POINTS and any(p in lowered for p in phrases):
            points[category] = CATEGORY_POINTS[category]
    if _PHONE.search(value) and (_PAYMENT_APP.search(value) or points.get("send_to_number")):
        points["personal_number"] = PERSONAL_NUMBER_POINTS
    named, unknown = _hospital_named(names, value)
    if not named:
        points["unknown_hospital" if unknown else "no_hospital"] = (
            UNKNOWN_HOSPITAL_POINTS if unknown else NO_HOSPITAL_POINTS
        )
    if _AMOUNT.search(value) and not _INVOICE.search(value):
        points["amount_no_invoice"] = AMOUNT_NO_INVOICE_POINTS
    if any(phrase in lowered for phrase in TRUST_PHRASES):
        points["reassuring"] = -TRUST_POINTS
    return points


# --------------------------------------------------------------------------- matching a live case


def matched_case(session: Session, value: str) -> Case | None:
    """A public, live case the message points to: by its page link, or by hospital and alias."""
    for slug in dict.fromkeys(_SLUG.findall(value.lower())):
        try:
            return case_service.get_public_case_or_404(session, slug)
        except Exception:  # noqa: BLE001 - not every twelve letters is a case
            continue
    lowered = value.lower()
    for case in session.execute(case_service.public_cases()).scalars():
        if case.alias.lower() in lowered and case.hospital.display_name.lower() in lowered:
            return case
    return None


# --------------------------------------------------------------------------- the verdict


def score_from(points: dict[str, int], prob: float | None) -> int:
    rules = max(0, min(60, sum(points.values())))
    model = (prob or 0.0) * 100
    if prob is None:
        return int(min(100, round(rules * 1.4)))
    return int(min(100, round(MODEL_WEIGHT * model + RULE_WEIGHT * rules)))


def _reasons(points: dict[str, int], prob: float | None) -> tuple[list[str], list[str]]:
    ranked = sorted((k for k, v in points.items() if v > 0), key=lambda k: -points[k])
    keys = ranked[:3]
    if prob is not None and prob >= 0.6 and len(keys) < 3:
        keys.append("model")
    if not keys:
        keys = []
    english = [REASONS[k][0] for k in keys]
    swahili = [REASONS[k][1] for k in keys]
    if points.get("reassuring"):
        english.append(REASONS["reassuring"][0])
        swahili.append(REASONS["reassuring"][1])
    if not english:
        return [GENUINE_LOOKING[0]], [GENUINE_LOOKING[1]]
    return english, swahili


def assess(session: Session, value: str) -> Verdict:
    """Score one message. Nothing is stored."""
    value = value[:MAX_TEXT_CHARS]
    prob, version = probability(value)
    points = rule_features(register_names(session), value)
    score = score_from(points, prob)
    from app.ai.risk import band_for

    reasons_en, reasons_sw = _reasons(points, prob)
    return Verdict(
        score=score,
        band=band_for(score),
        reasons_en=reasons_en,
        reasons_sw=reasons_sw,
        flagged=score >= FLAG_FROM,
        probability=prob,
        matched_case=matched_case(session, value),
        features=points,
        model_version=version,
    )


def read_image(data: bytes, mime_type: str) -> str:
    """The text of a screenshot of a message."""
    return text_reader.extract(data, mime_type).text


def check(
    session: Session, *, value: str, kind: AppealInputKind = AppealInputKind.TEXT
) -> tuple[Verdict, AppealCheck]:
    """Score a message and record that a check happened: an HMAC of the input, the result and
    the matched case, never the message itself. The caller commits."""
    verdict = assess(session, value)
    row = AppealCheck(
        input_hmac=crypto.lookup_hash(value, context=HMAC_CONTEXT),
        input_kind=kind,
        risk_score=verdict.score,
        risk_band=verdict.band,
        reasons_en=verdict.reasons_en,
        reasons_sw=verdict.reasons_sw,
        matched_case_id=verdict.matched_case.id if verdict.matched_case else None,
        used_llm=False,
    )
    session.add(row)
    session.flush()
    return verdict, row


def app_url(case: Case) -> str:
    return f"{current_app.config['APP_BASE_URL'].rstrip('/')}/c/{case.public_slug}"
