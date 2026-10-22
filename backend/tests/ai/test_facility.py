"""The facility check: name match, register code, letterhead contacts."""

import pytest

from app.ai import extract, facility, text
from app.models.hospital import normalize_facility_name
from app.synthetic_data import load_register
from tests.ai.helpers import DATA_DIR, document, labels, needs_dataset
from tests.factories import FacilityRegisterEntryFactory, HospitalFactory

KILELE = "Kilele Mission Hospital"


def entry(
    name: str = KILELE,
    code: str = "FR-NBI-0101",
    phone: str = "+254 20 555 0101",
    email: str = "billing@kilele-hospital.test",
):  # type: ignore[no-untyped-def]
    return FacilityRegisterEntryFactory(
        name=name,
        register_code=code,
        official_phone=phone,
        official_email=email,
        normalized_name=normalize_facility_name(name),
    )


def bill(name: str | None) -> extract.ExtractedBill:
    return extract.ExtractedBill(facility_name=name)


def codes(result: facility.FacilityResult) -> list[str]:
    return [s.code for s in result.signals]


# --------------------------------------------------------------------------- the match


def test_an_exact_name_is_a_perfect_match(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()
    entry("Highland Reed Hospital", "FR-UGN-0404")

    match = facility.best_match(db_session, KILELE)

    assert match.entry == real
    assert match.score == 100


@pytest.mark.parametrize(
    "variant",
    [
        "KILELE MISSION HOSPITAL",
        "kilele mission hospital.",
        "Kilele  Mission, Hospital",
        "Hospital Kilele Mission",
    ],
)
def test_case_punctuation_and_word_order_do_not_matter(db_session, variant: str) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    match = facility.best_match(db_session, variant)

    assert (match.entry, match.score) == (real, 100)


def test_one_typo_is_a_close_but_inexact_match(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    match = facility.best_match(db_session, "Kilele Mision Hospital")

    assert match.entry == real
    assert 85 <= match.score < 100


@pytest.mark.parametrize("name", [None, "", "   "])
def test_no_name_matches_nothing(db_session, name) -> None:  # type: ignore[no-untyped-def]
    entry()

    assert facility.best_match(db_session, name).entry is None


def test_an_empty_register_matches_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    assert facility.best_match(db_session, KILELE) == facility.FacilityMatch(None, 0.0)


# --------------------------------------------------------------------------- signals


def test_a_facility_in_the_register_and_chosen_by_the_family_raises_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()
    chosen = HospitalFactory(register_entry=real)

    assert codes(facility.check(db_session, bill(KILELE), f"{KILELE}\nTotal 1", chosen)) == []


def test_a_name_with_no_close_match_is_not_in_the_register(db_session) -> None:  # type: ignore[no-untyped-def]
    entry()

    result = facility.check(db_session, bill("Zebra Springs Private Clinic"), "", None)

    assert codes(result) == ["facility_not_in_register"]
    assert result.signals[0].detail["best_score"] < 60


def test_a_missing_name_is_not_in_the_register(db_session) -> None:  # type: ignore[no-untyped-def]
    entry()

    assert codes(facility.check(db_session, bill(None), "", None)) == ["facility_not_in_register"]


def test_a_weak_match_is_noted(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry("Baobab Grove Medical Centre", "FR-MCK-0808")
    chosen = HospitalFactory(register_entry=real)

    result = facility.check(db_session, bill("Baobab Medical Services"), "", chosen)

    assert "facility_weak_match" in codes(result)
    assert "facility_not_in_register" not in codes(result)


def test_a_best_match_that_is_not_the_hospital_the_family_chose_is_flagged(db_session) -> None:  # type: ignore[no-untyped-def]
    entry()
    other = HospitalFactory(register_entry=entry("Highland Reed Hospital", "FR-UGN-0404"))

    result = facility.check(db_session, bill(KILELE), "", other)

    assert codes(result) == ["facility_mismatch_selected"]


def test_no_chosen_hospital_means_no_mismatch(db_session) -> None:  # type: ignore[no-untyped-def]
    entry()

    assert codes(facility.check(db_session, bill(KILELE), "", None)) == []


# --------------------------------------------------------------------------- register code


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("Facility reg. no. FR-NBI-0101", "FR-NBI-0101"),
        ("REG NO FR-NYR-0707", "FR-NYR-0707"),
        ("Reg. FR-NKR-0505 admin@riftheights.test", "FR-NKR-0505"),
        ("Facility reg. no. FR-NBI-O101", "FR-NBI-0101"),  # OCR read zero as the letter O
        ("fr-nbi-0l01", "FR-NBI-0101"),
        ("Total 4,500.00", None),
    ],
)
def test_the_printed_register_code_is_found_and_ocr_slips_corrected(
    line: str, code: str | None
) -> None:
    assert facility.printed_register_code(f"{KILELE}\n{line}\nmore") == code


def test_a_matching_code_is_fine(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    result = facility.check(
        db_session,
        bill(KILELE),
        f"{KILELE}\nFacility reg. no. FR-NBI-0101",
        HospitalFactory(register_entry=real),
    )

    assert codes(result) == []


def test_another_hospitals_code_on_a_look_alike_name_is_flagged(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()
    entry("Highland Reed Hospital", "FR-UGN-0404")

    result = facility.check(
        db_session,
        bill("Kilele Mision Hospital"),
        "Kilele Mision Hospital\nFacility reg. no. FR-UGN-0404",
        HospitalFactory(register_entry=real),
    )

    assert "facility_code_mismatch" in codes(result)


def test_one_slipped_character_in_the_code_is_ocr_noise_not_a_forgery(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    result = facility.check(
        db_session,
        bill(KILELE),
        f"{KILELE}\nFacility reg. no. FR-NBI-0102",
        HospitalFactory(register_entry=real),
    )

    assert codes(result) == []


def test_two_slipped_characters_are_a_different_code(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    result = facility.check(
        db_session,
        bill(KILELE),
        f"{KILELE}\nFacility reg. no. FR-NBI-0199",
        HospitalFactory(register_entry=real),
    )

    assert codes(result) == ["facility_code_mismatch"]


# --------------------------------------------------------------------------- letterhead contacts


@pytest.mark.parametrize(
    "head",
    [
        "Tel: +254 20 555 0101 Email: billing@kilele-hospital.test",
        "Tel: 020 555 0101 Email: BILLING@Kilele-Hospital.test",
        "TEL +254205550101",
    ],
)
def test_the_registers_own_contacts_in_any_format_raise_nothing(db_session, head: str) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    result = facility.check(
        db_session, bill(KILELE), f"{KILELE}\n{head}\nTotal 1", HospitalFactory(register_entry=real)
    )

    assert codes(result) == []


@pytest.mark.parametrize(
    ("head", "differs"),
    [
        ("Tel: 0700 000 111 Email: billing@kilele-hospital.test", ["phone"]),
        ("Tel: 020 555 0101 Email: kilele.accounts@gmail.test", ["email"]),
        ("Tel: 0700 000 111 Email: kilele.accounts@gmail.test", ["phone", "email"]),
    ],
)
def test_a_phone_or_email_that_is_not_the_registers_is_flagged(
    db_session, head: str, differs: list[str]
) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    result = facility.check(
        db_session, bill(KILELE), f"{KILELE}\n{head}\nTotal 1", HospitalFactory(register_entry=real)
    )

    signal = next(s for s in result.signals if s.code == "contact_mismatch")
    assert signal.detail == {"differs": differs}


def test_a_contact_deep_in_the_text_is_not_a_letterhead_contact(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()
    body = (
        KILELE
        + "\n"
        + "\n".join(f"line {i}" for i in range(12))
        + "\nCall 0700 000 111 for queries"
    )

    assert (
        codes(facility.check(db_session, bill(KILELE), body, HospitalFactory(register_entry=real)))
        == []
    )


def test_no_contact_printed_raises_nothing(db_session) -> None:  # type: ignore[no-untyped-def]
    real = entry()

    assert (
        codes(
            facility.check(db_session, bill(KILELE), KILELE, HospitalFactory(register_entry=real))
        )
        == []
    )


# --------------------------------------------------------------------------- the synthetic bills

PDF_ROWS = [r for r in labels() if r["filename"].endswith(".pdf")] if DATA_DIR.exists() else []
EXPECTED = {
    "facility_unknown": "facility_not_in_register",
    "facility_fuzzy_match": "facility_code_mismatch",
    "contact_mismatch": "contact_mismatch",
}


@needs_dataset
def test_the_facility_signals_match_the_labels_on_every_digital_bill(db_session) -> None:  # type: ignore[no-untyped-def]
    entries = {}
    hospitals = {}
    for row in load_register():
        entries[row.register_code] = FacilityRegisterEntryFactory(
            register_code=row.register_code,
            name=row.name,
            normalized_name=normalize_facility_name(row.name),
            official_email=row.official_email,
            official_phone=row.official_phone,
            county=row.county,
        )
    wrong = []
    for row in PDF_ROWS:
        raw, mime = document(row["doc_id"])
        original = text.extract(raw, mime).text
        parsed = extract.extract(original).bill
        chosen_entry = entries.get(row["register_code"])
        if chosen_entry and row["register_code"] not in hospitals:
            hospitals[row["register_code"]] = HospitalFactory(register_entry=chosen_entry)
        chosen = hospitals.get(row["register_code"])
        raised = {s.code for s in facility.check(db_session, parsed, original, chosen).signals}
        expected = {EXPECTED[c] for c in row["expected_signals"].split("|") if c in EXPECTED}
        if raised != expected:
            wrong.append((row["doc_id"], row["tamper_type"], sorted(raised), sorted(expected)))
    assert wrong == []
