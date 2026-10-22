"""The appeal checker: rules, classifier, matching a live case, and what it stores."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from app.ai import appeals
from app.cli.ai import ai_group, split_metrics
from app.models.enums import AppealInputKind, CaseStatus, ConsentScope, RiskBand
from app.models.hospital import normalize_facility_name
from app.models.platform import AppealCheck
from app.synthetic_data import load_register
from tests.ai.helpers import DATA_DIR, needs_dataset
from tests.factories import (
    CaseFactory,
    ConsentFactory,
    FacilityRegisterEntryFactory,
)

NAMES = ["kilele mission hospital", "highland reed hospital", "mto mwema hospital"]
SCAM = (
    "URGENT!! My mother is dying, surgery tonight. Send any amount to my M-Pesa 0712 345 678 "
    "now, do not call the hospital, there is no time to ask questions."
)
GENUINE = (
    "Dear family, my father is at Kilele Mission Hospital. The invoice KMH/26/059153 is "
    "KES 85,000. Please verify with their accounts office on +254 20 555 0101 and pay "
    "directly to the hospital, not to a person. I will share the receipt."
)


def appeals_rows() -> list[dict]:
    with (DATA_DIR / "appeals.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@pytest.fixture
def trained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """The dev-split classifier, saved to a temporary file and used in place of the shipped one."""
    artifact = appeals.train([r for r in appeals_rows() if r["split"] == "dev"], version="test-1")
    import joblib

    path = tmp_path / "clf.joblib"
    joblib.dump(artifact, path)
    monkeypatch.setattr(appeals, "MODEL_PATH", path)
    appeals.forget_model()
    yield artifact
    appeals.forget_model()


# --------------------------------------------------------------------------- rule features


def test_a_personal_number_for_money_is_the_strongest_habit() -> None:
    points = appeals.rule_features(NAMES, SCAM)

    assert points["personal_number"] == appeals.PERSONAL_NUMBER_POINTS
    assert points["urgency"] > 0
    assert len(points) >= 3
    assert "reassuring" not in points


def test_a_genuine_appeal_names_a_register_hospital_and_earns_trust_points() -> None:
    points = appeals.rule_features(NAMES, GENUINE)

    assert points["reassuring"] < 0
    assert "no_hospital" not in points
    assert "unknown_hospital" not in points
    assert "amount_no_invoice" not in points  # it gives an invoice number
    assert "personal_number" not in points


def test_no_hospital_named_is_a_small_concern() -> None:
    points = appeals.rule_features(
        NAMES, "Please help my brother, he is sick and we need KES 50,000."
    )

    assert points["no_hospital"] == appeals.NO_HOSPITAL_POINTS
    assert points["amount_no_invoice"] == appeals.AMOUNT_NO_INVOICE_POINTS


def test_a_hospital_like_name_missing_from_the_register_is_a_bigger_one() -> None:
    points = appeals.rule_features(
        NAMES, "My uncle is at Zebra Springs Private Hospital, send help."
    )

    assert points["unknown_hospital"] == appeals.UNKNOWN_HOSPITAL_POINTS
    assert "no_hospital" not in points


def test_a_register_hospital_is_recognised_despite_spelling_and_case() -> None:
    points = appeals.rule_features(NAMES, "she is at KILELE MISSION HOSPITAL ward")

    assert "no_hospital" not in points
    assert "unknown_hospital" not in points


def test_a_phone_number_alone_is_not_a_request_to_send_money() -> None:
    points = appeals.rule_features(NAMES, "Kilele Mission Hospital billing is on 0712 345 678")

    assert "personal_number" not in points


def test_mpesa_with_a_phone_number_is_a_request_to_send_money() -> None:
    points = appeals.rule_features(NAMES, "Kilele Mission Hospital bill, M-Pesa 0712345678 please")

    assert points["personal_number"] > 0


# --------------------------------------------------------------------------- the score


def test_without_a_model_the_rules_alone_are_scaled_up() -> None:
    assert appeals.score_from({"urgency": 12, "personal_payee": 25}, None) == round(37 * 1.4)
    assert appeals.score_from({}, None) == 0
    assert appeals.score_from({"a": 100}, None) == 84  # rules count for at most 60 points


def test_trust_points_lower_the_score_but_never_below_zero() -> None:
    assert appeals.score_from({"reassuring": -15}, None) == 0
    assert appeals.score_from({"urgency": 12, "reassuring": -15}, 0.2) == round(
        appeals.MODEL_WEIGHT * 20
    )


def test_a_confident_model_plus_rules_is_high_and_capped() -> None:
    assert appeals.score_from({"personal_payee": 25, "advance_fee": 25, "secrecy": 18}, 1.0) == 100


def test_the_reasons_are_the_three_heaviest_habits_then_the_models_view() -> None:
    english, swahili = appeals._reasons(  # noqa: SLF001
        {"urgency": 12, "personal_number": 20, "advance_fee": 25, "secrecy": 18, "reassuring": -15},
        0.9,
    )

    assert english[:3] == [
        appeals.REASONS["advance_fee"][0],
        appeals.REASONS["personal_number"][0],
        appeals.REASONS["secrecy"][0],
    ]
    assert english[-1] == appeals.REASONS["reassuring"][0]
    assert len(english) == len(swahili)


def test_with_no_concern_the_person_is_told_to_check_anyway() -> None:
    english, swahili = appeals._reasons({}, 0.1)  # noqa: SLF001

    assert english == [appeals.GENUINE_LOOKING[0]]
    assert swahili == [appeals.GENUINE_LOOKING[1]]


def test_the_model_is_mentioned_when_it_is_sure_and_few_habits_were_found() -> None:
    english, _ = appeals._reasons({"urgency": 12}, 0.9)  # noqa: SLF001

    assert english == [appeals.REASONS["urgency"][0], appeals.REASONS["model"][0]]


# --------------------------------------------------------------------------- with the trained model


@needs_dataset
def test_the_trained_model_separates_the_held_out_messages(trained) -> None:  # type: ignore[no-untyped-def]
    names = [normalize_facility_name(r.name) for r in load_register()]
    held_out = [r for r in appeals_rows() if r["split"] == "test"]

    metrics = split_metrics(trained["model"], held_out, names)

    assert metrics["precision"] >= 0.85  # the specification's target
    assert metrics["recall"] >= 0.8
    assert metrics["n"] == len(held_out)


@needs_dataset
def test_a_scam_and_a_genuine_message_land_in_different_bands(db_session, trained) -> None:  # type: ignore[no-untyped-def]
    FacilityRegisterEntryFactory(name="Kilele Mission Hospital")

    scam = appeals.assess(db_session, SCAM)
    genuine = appeals.assess(db_session, GENUINE)

    assert scam.flagged
    assert scam.band == RiskBand.HIGH or scam.score >= appeals.FLAG_FROM
    assert not genuine.flagged
    assert genuine.band == RiskBand.LOW
    assert scam.model_version == "test-1"
    assert scam.score > genuine.score


def test_without_a_trained_model_the_rules_still_work(db_session, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(appeals, "MODEL_PATH", tmp_path / "missing.joblib")
    appeals.forget_model()

    verdict = appeals.assess(db_session, SCAM)

    assert verdict.model_version is None
    assert verdict.probability is None
    assert verdict.flagged


def test_the_artifact_remembers_how_it_was_trained(trained) -> None:  # type: ignore[no-untyped-def]
    assert trained["version"] == "test-1"
    assert trained["classes"] == ["genuine", "scam"]
    assert trained["n_train"] == len([r for r in appeals_rows() if r["split"] == "dev"])


def test_numbers_are_made_alike_before_the_model_sees_them() -> None:
    assert appeals._normalise("Send KES 5,000 to 0712 345 678 now") == (  # noqa: SLF001
        "send kes number to phonenumber now"
    )


# --------------------------------------------------------------------------- matching a case


def live_case(alias: str = "Baby Zawadi"):  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.LIVE, alias=alias, target_kes_minor=8_500_000)
    ConsentFactory(case=case, scope=ConsentScope.PUBLIC_PAGE)
    return case


def test_a_message_with_a_case_link_is_matched(db_session) -> None:  # type: ignore[no-untyped-def]
    case = live_case()

    found = appeals.matched_case(
        db_session, f"Please give here https://tibadirect.test/c/{case.public_slug} thanks"
    )

    assert found == case


def test_a_message_naming_the_hospital_and_the_alias_is_matched(db_session) -> None:  # type: ignore[no-untyped-def]
    case = live_case("Baby Zawadi")

    text = f"Help baby zawadi at {case.hospital.display_name}, KES 85,000"

    assert appeals.matched_case(db_session, text) == case


def test_a_hospital_without_the_alias_is_not_a_match(db_session) -> None:  # type: ignore[no-untyped-def]
    case = live_case("Baby Zawadi")

    assert (
        appeals.matched_case(db_session, f"Someone at {case.hospital.display_name} needs help")
        is None
    )


def test_a_case_that_is_not_public_is_never_matched(db_session) -> None:  # type: ignore[no-untyped-def]
    case = CaseFactory(status=CaseStatus.DRAFT)

    assert appeals.matched_case(db_session, f"https://tibadirect.test/c/{case.public_slug}") is None


def test_twelve_ordinary_letters_are_not_a_case(db_session) -> None:  # type: ignore[no-untyped-def]
    live_case()

    assert appeals.matched_case(db_session, "please remember something wonderful tomorrow") is None


# --------------------------------------------------------------------------- what is stored


def test_only_a_keyed_hash_of_the_message_is_stored(db_session) -> None:  # type: ignore[no-untyped-def]
    verdict, row = appeals.check(db_session, value=SCAM)
    db_session.flush()

    stored = db_session.get(AppealCheck, row.id)
    assert len(stored.input_hmac) == 64
    assert stored.input_kind == AppealInputKind.TEXT
    assert (stored.risk_score, stored.risk_band) == (verdict.score, verdict.band)
    assert stored.matched_case_id is None
    assert stored.used_llm is False
    everything = repr(stored.__dict__) + str(stored.reasons_en)
    assert "0712" not in everything
    assert "M-Pesa" not in everything


def test_the_same_message_gives_the_same_hash_and_another_gives_another(db_session) -> None:  # type: ignore[no-untyped-def]
    _, first = appeals.check(db_session, value=SCAM)
    _, again = appeals.check(db_session, value=SCAM)
    _, other = appeals.check(db_session, value=GENUINE)

    assert first.input_hmac == again.input_hmac
    assert first.input_hmac != other.input_hmac


def test_a_check_of_a_matched_case_records_the_case(db_session) -> None:  # type: ignore[no-untyped-def]
    case = live_case()

    _, row = appeals.check(
        db_session, value=f"give at /c/{case.public_slug} today", kind=AppealInputKind.IMAGE
    )

    assert row.matched_case_id == case.id
    assert row.input_kind == AppealInputKind.IMAGE


# --------------------------------------------------------------------------- the command


@needs_dataset
def test_the_train_command_saves_a_model_and_prints_the_held_out_score(
    app, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    out = tmp_path / "model.joblib"

    result = app.test_cli_runner().invoke(ai_group, ["train-appeals", "--out", str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "Trained on 66 messages (dev)" in result.output
    assert "'precision': 1.0" in result.output
    import joblib

    saved = joblib.load(out)
    assert saved["trained_on"] == "dev"
    assert saved["test_metrics"]["n"] == 34


@needs_dataset
def test_training_on_everything_is_possible_but_says_so(app, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    result = app.test_cli_runner().invoke(
        ai_group, ["train-appeals", "--out", str(tmp_path / "all.joblib"), "--on", "all"]
    )

    assert result.exit_code == 0, result.output
    assert "Trained on 100 messages (all)" in result.output


def test_the_runner_is_the_real_click_group() -> None:
    assert CliRunner().invoke(ai_group, ["--help"]).exit_code == 0
