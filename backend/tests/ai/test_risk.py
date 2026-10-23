"""The risk score, its bands, the route, and the plain-language reasons."""

import re

import pytest

from app.ai import reasons, risk
from app.ai.signals import FORCES_MANUAL_REVIEW, WEIGHTS, Signal
from app.models.enums import RiskBand, TriageRoute


def make(*codes: str) -> list[Signal]:
    return [Signal(code) for code in codes]


# --------------------------------------------------------------------------- score, band, route


def test_no_signals_is_a_low_score_that_goes_to_the_hospital() -> None:
    assessed = risk.assess([])

    assert (assessed.score, assessed.band, assessed.route) == (
        0,
        RiskBand.LOW,
        TriageRoute.HOSPITAL_CONFIRMATION,
    )


def test_the_score_is_the_sum_of_the_weights() -> None:
    assert risk.assess(make("totals_mismatch", "font_inconsistency")).score == 25 + 10


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (0, RiskBand.LOW),
        (29, RiskBand.LOW),
        (30, RiskBand.MEDIUM),
        (69, RiskBand.MEDIUM),
        (70, RiskBand.HIGH),
        (100, RiskBand.HIGH),
    ],
)
def test_the_bands_change_at_30_and_70(score: int, band: RiskBand) -> None:
    assert risk.band_for(score) == band


def test_the_score_never_goes_above_100() -> None:
    everything = make(*WEIGHTS)

    assert sum(s.weight for s in everything) > 100
    assert risk.assess(everything).score == 100


def test_a_high_band_goes_to_manual_review() -> None:
    assessed = risk.assess(
        make("hidden_text_under_overlay", "totals_mismatch", "pdf_editor_software")
    )

    assert (assessed.score, assessed.band, assessed.route) == (
        70,
        RiskBand.HIGH,
        TriageRoute.MANUAL_REVIEW,
    )


def test_a_medium_score_without_a_forcing_signal_still_goes_to_the_hospital() -> None:
    assessed = risk.assess(make("totals_mismatch", "facility_not_in_register"))

    assert (assessed.band, assessed.route) == (RiskBand.MEDIUM, TriageRoute.HOSPITAL_CONFIRMATION)


@pytest.mark.parametrize("code", sorted(FORCES_MANUAL_REVIEW))
def test_a_duplicate_or_a_patient_in_another_open_case_forces_review_at_any_score(
    code: str,
) -> None:
    assessed = risk.assess(make(code))

    assert assessed.route == TriageRoute.MANUAL_REVIEW


def test_exactly_those_signals_force_review() -> None:
    assert {
        "duplicate_exact",
        "duplicate_near",
        "duplicate_fingerprint",
        "patient_active_elsewhere",
    } == FORCES_MANUAL_REVIEW


def test_no_signal_ever_rejects_a_bill() -> None:
    routes = {risk.assess(make(code)).route for code in WEIGHTS}

    assert routes <= {TriageRoute.HOSPITAL_CONFIRMATION, TriageRoute.MANUAL_REVIEW}


def test_the_specifications_weights_are_the_ones_in_use() -> None:
    assert {
        "duplicate_exact": 60,
        "duplicate_near": 45,
        "duplicate_fingerprint": 45,
        "patient_active_elsewhere": 30,
        "facility_not_in_register": 25,
        "facility_weak_match": 10,
        "facility_mismatch_selected": 20,
        "pdf_incremental_updates": 15,
        "pdf_editor_software": 15,
        "pdf_modified_after_creation": 10,
        "totals_mismatch": 25,
        "font_inconsistency": 10,
        "image_ela_high": 15,
        "exif_editor_software": 10,
        "low_extraction_confidence": 10,
        "extraction_disagreement": 10,
        "instruction_like_text": 15,
    }.items() <= WEIGHTS.items()


def test_a_signal_serialises_with_its_weight_and_whether_it_forces_review() -> None:
    assert Signal("duplicate_exact", {"x": 1}).as_json() == {
        "code": "duplicate_exact",
        "weight": 60,
        "forces_manual_review": True,
        "detail": {"x": 1},
    }


# --------------------------------------------------------------------------- reasons


def test_every_signal_has_a_reason_in_both_languages() -> None:
    assert set(reasons.REASONS) == set(WEIGHTS)
    for english, swahili in reasons.REASONS.values():
        assert english.endswith(".")
        assert swahili.endswith(".")
        assert english != swahili


def test_reasons_never_contain_figures_or_personal_details() -> None:
    for english, swahili in reasons.REASONS.values():
        for line in (english, swahili):
            assert not re.search(r"\d", line)
            assert "@" not in line


def test_the_top_three_signals_by_weight_are_explained_heaviest_first() -> None:
    signals = make(
        "font_inconsistency",
        "duplicate_exact",
        "totals_mismatch",
        "facility_weak_match",
        "pdf_editor_software",
    )

    english, swahili = reasons.reasons_for(signals)

    assert english == [
        reasons.REASONS["duplicate_exact"][0],
        reasons.REASONS["totals_mismatch"][0],
        reasons.REASONS["pdf_editor_software"][0],
    ]
    assert swahili == [
        reasons.REASONS["duplicate_exact"][1],
        reasons.REASONS["totals_mismatch"][1],
        reasons.REASONS["pdf_editor_software"][1],
    ]


def test_equal_weights_keep_the_order_the_checks_raised_them_in() -> None:
    english, _ = reasons.reasons_for(make("pdf_incremental_updates", "pdf_editor_software"))

    assert english == [
        reasons.REASONS["pdf_incremental_updates"][0],
        reasons.REASONS["pdf_editor_software"][0],
    ]


def test_with_nothing_found_the_family_is_told_the_hospital_still_confirms() -> None:
    english, swahili = reasons.reasons_for([])

    assert english == [reasons.NOTHING_FOUND[0]]
    assert "hospital will still confirm" in english[0]
    assert swahili == [reasons.NOTHING_FOUND[1]]


def test_fewer_than_three_signals_give_fewer_reasons() -> None:
    english, swahili = reasons.reasons_for(make("totals_mismatch"))

    assert len(english) == len(swahili) == 1
