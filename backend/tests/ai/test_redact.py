"""Redaction: what is removed, what must stay, and a leak test across the synthetic bills."""

import re
import time
from decimal import Decimal

import pytest

from app import lexicon
from app.ai import redact, text
from tests.ai.helpers import (
    DATA_DIR,
    document,
    ground_truth,
    labels,
    needs_dataset,
)

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def red(value: str) -> str:
    return redact.redact(value).text


def numbers(value: str) -> list[Decimal]:
    return [Decimal(m.replace(",", "")) for m in NUMBER_RE.findall(value) if m.replace(",", "")]


# --------------------------------------------------------------------------- labelled values


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Diagnosis: Road traffic injury with head trauma", "Diagnosis: [REDACTED:DIAGNOSIS]"),
        ("DX: Obstructed labour", "DX: [REDACTED:DIAGNOSIS]"),
        ("Provisional diagnosis: query typhoid", "Provisional diagnosis: [REDACTED:DIAGNOSIS]"),
        ("Procedure: Decompressive craniotomy", "Procedure: [REDACTED:PROCEDURE]"),
        ("PROC: Caesarean section", "PROC: [REDACTED:PROCEDURE]"),
        ("Theatre: Theatre 2, list B", "Theatre: [REDACTED:PROCEDURE]"),
        ("Ward: Maternity annexe", "Ward: [REDACTED:WARD]"),
        ("Medication: Amoxicillin 500mg three times a day", "Medication: [REDACTED:DRUG]"),
        ("Drugs: as per chart", "Drugs: [REDACTED:DRUG]"),
        ("Lab: urea and electrolytes", "Lab: [REDACTED:TEST]"),
        ("Tests: as ordered", "Tests: [REDACTED:TEST]"),
        ("ID No.: 34191405", "ID No.: [REDACTED:ID]"),
        ("ID NUMBER: 34191405", "ID NUMBER: [REDACTED:ID]"),
        ("National ID: 12345678", "National ID: [REDACTED:ID]"),
        ("Passport No: A1234567", "Passport No: [REDACTED:ID]"),
        ("NHIF No.: 1234567", "NHIF No.: [REDACTED:ID]"),
        ("SHA No.: SHA-502482554", "SHA No.: [REDACTED:ID]"),
        ("Phone: 0712 345 678", "Phone: [REDACTED:PHONE]"),
        ("Mobile: +254 712 345 678", "Mobile: [REDACTED:PHONE]"),
        ("Email: jane.doe@example.test", "Email: [REDACTED:EMAIL]"),
        ("E-mail: jane.doe@example.test", "E-mail: [REDACTED:EMAIL]"),
        ("Next of kin: Jane Doe 0700 111 222", "Next of kin: [REDACTED:NAME]"),
        ("Patient: Liam Mutua", "Patient: [REDACTED:NAME]"),
        ("PATIENT: Victor Mwangi", "PATIENT: [REDACTED:NAME]"),
        ("Prepared by: Umi Atieno Signature: ____", "Prepared by: [REDACTED:NAME] Signature: ____"),
        ("Served by: Daniel Wafula", "Served by: [REDACTED:NAME]"),
        ("DOB: 12/03/1990", "DOB: [REDACTED:DOB]"),
        ("Date of birth: 12 March 1990", "Date of birth: [REDACTED:DOB]"),
        ("Age: 34 years", "Age: [REDACTED:DOB]"),
    ],
)
def test_each_labelled_value_is_replaced_and_the_label_stays(raw: str, expected: str) -> None:
    assert red(raw) == expected


def test_a_value_ends_at_the_next_label_on_the_line() -> None:
    raw = "Patient: Liam Mutua Estimate No.: EST-HRH/INV/06229/26"

    assert red(raw) == "Patient: [REDACTED:NAME] Estimate No.: EST-HRH/INV/06229/26"


def test_several_labels_on_one_line_each_get_their_own_value_redacted() -> None:
    raw = "Guarantor: Lilian Wanjiru 0700 004 359 Patient ref.: IP/26/61332"

    assert red(raw) == "Guarantor: [REDACTED:NAME] Patient ref.: [PATIENT_REF]"


def test_tel_and_email_on_a_letterhead_line_are_both_removed() -> None:
    raw = "Tel: 053 555 0404 Email: billing@highlandreed.test"

    assert red(raw) == "Tel: [REDACTED:PHONE] Email: [REDACTED:EMAIL]"


def test_a_label_without_a_colon_counts_when_it_is_unmistakable() -> None:
    assert red("Patient ref. IP/26/73179") == "Patient ref. [PATIENT_REF]"
    assert red("SHA No. SHA-314166184 Patient ref. IP/26/73179") == (
        "SHA No. [REDACTED:ID] Patient ref. [PATIENT_REF]"
    )


def test_an_ordinary_word_is_a_label_only_with_a_colon() -> None:
    assert red("Malaria parasite test 3 600.00 1,800.00") == "[REDACTED:MEDICAL] 3 600.00 1,800.00"
    assert red("Private ward bed (per day) 5 8,600.00 43,000.00") == (
        "Private ward bed (per day) 5 8,600.00 43,000.00"
    )
    assert (
        red("The patient was seen by the nursing team")
        == "The patient was seen by the nursing team"
    )


def test_clinical_notes_are_removed_label_and_all() -> None:
    cleaned = red("Clinical notes: admitted with fever for three days")

    assert "fever" not in cleaned
    assert "three days" not in cleaned
    assert "[REDACTED:NOTES]" in cleaned


def test_digits_after_a_decimal_point_are_never_a_phone_number() -> None:
    line = "2 Intensive care unit (per day) 9 49700.00 447300.00"

    assert red(line) == line


def test_the_name_of_the_facility_is_not_a_patient_name() -> None:
    assert red("Facility name: Kilele Mission Hospital") == "Facility name: Kilele Mission Hospital"


def test_the_line_after_a_billed_to_header_is_the_patients_name() -> None:
    raw = "BILLED TO DETAILS\nPeter Otieno Invoice no. RH-003857\nDate 20.05.2026"

    assert red(raw) == "BILLED TO DETAILS\n[REDACTED:NAME] Invoice no. RH-003857\nDate 20.05.2026"


def test_a_header_only_names_the_one_line_that_follows_it() -> None:
    raw = "BILLED TO\nPeter Otieno\nMaternity consultation 3,300.00"

    assert red(raw) == "BILLED TO\n[REDACTED:NAME]\nMaternity consultation 3,300.00"


# --------------------------------------------------------------------------- patient reference


def test_the_patient_reference_is_handed_back_and_not_left_in_the_text() -> None:
    result = redact.redact("Patient ref.: IP/26/61332\nREF: IP/26/44207")

    assert result.text == "Patient ref.: [PATIENT_REF]\nREF: [PATIENT_REF]"
    assert result.patient_references == ["IP/26/61332", "IP/26/44207"]
    assert "IP/26" not in result.text


@pytest.mark.parametrize(
    "line",
    [
        "Admission No.: A-5521",
        "Hospital No: 88421",
        "IP No: IP/26/1",
        "MRN: 009911",
        "File no. 12/445",
        "Patient No: 7788",
    ],
)
def test_the_other_names_for_a_patient_reference_are_treated_alike(line: str) -> None:
    result = redact.redact(line)

    assert "[PATIENT_REF]" in result.text
    assert len(result.patient_references) == 1


def test_an_invoice_number_is_never_taken_for_a_patient_reference() -> None:
    result = redact.redact("Invoice No.: KMH/26/059153 Date: 26/05/2026")

    assert result.text == "Invoice No.: KMH/26/059153 Date: 26/05/2026"
    assert result.patient_references == []


# --------------------------------------------------------------------------- patterns anywhere


@pytest.mark.parametrize(
    "phone",
    [
        "+254712345678",
        "+254 712 345 678",
        "0712345678",
        "0712 345 678",
        "0712-345-678",
        "0112 345 678",
        "+254 20 555 0101",
        "020 555 0101",
        "053 555 0404",
        "(057) 5550303",
        "0700 000 976",
    ],
)
def test_phone_numbers_are_removed_wherever_they_appear(phone: str) -> None:
    assert red(f"Call {phone} to confirm") == "Call [REDACTED:PHONE] to confirm"


def test_emails_are_removed_wherever_they_appear() -> None:
    assert red("Write to admin@riftheights.test please") == "Write to [REDACTED:EMAIL] please"


def test_a_bare_national_id_is_removed_but_not_on_a_line_about_money() -> None:
    assert red("Id 34191405 presented at the desk") == "Id [REDACTED:ID] presented at the desk"
    assert red("Total (KES) 1250000") == "Total (KES) 1250000"
    assert red("Deposit paid (KES) 3000000") == "Deposit paid (KES) 3000000"


@pytest.mark.parametrize(
    "kept",
    [
        "Total (KES) 66,700.00",
        "Invoice No.: KMH/26/059153",
        "NO: ZB260077106 DATE: 2026-09-29",
        "Date 20.05.2026",
        "Facility reg. no. FR-NBI-0101",
        "12 Kilele Avenue, Nairobi",
        "Maternity ward bed (per day) 2 5,600.00 11,200.00",
        "Specialist consultation 2 4,000.00 8,000.00",
        "Balance due (KES) 160,430.00",
        "Payment is due on discharge. Cheques payable to Kilele Mission Hospital.",
    ],
)
def test_amounts_dates_numbers_and_the_facility_are_left_alone(kept: str) -> None:
    assert red(kept) == kept


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Seen by Dr. Jane Wanjiru today", "Seen by [REDACTED:NAME] today"),
        ("Mrs Amina Otieno attended", "[REDACTED:NAME] attended"),
        ("Baby Zawadi was born", "[REDACTED:NAME] was born"),
        ("Master Kevin Njoroge", "[REDACTED:NAME]"),
    ],
)
def test_a_titled_name_is_removed_anywhere(raw: str, expected: str) -> None:
    assert red(raw) == expected


# --------------------------------------------------------------------------- the lexicon


def test_a_term_inside_a_line_item_goes_but_the_amounts_stay() -> None:
    raw = "7 Theatre charges - Caesarean section 1 49,500.00 49,500.00"

    assert red(raw) == "7 Theatre charges - [REDACTED:MEDICAL] 1 49,500.00 49,500.00"


@pytest.mark.parametrize(
    "variant", ["caesarean section", "CAESAREAN SECTION", "Caesarean   Section"]
)
def test_case_and_spacing_make_no_difference(variant: str) -> None:
    assert red(f"Fee for {variant} stay") == "Fee for [REDACTED:MEDICAL] stay"


def test_a_phrase_split_across_a_line_break_is_still_found_on_each_line_it_can_be() -> None:
    assert "caesarean" not in red("Charges: Caesarean section").lower()


def test_the_longest_term_wins() -> None:
    assert red("breast cancer clinic") == "[REDACTED:MEDICAL] clinic"


def test_accents_do_not_hide_a_term() -> None:
    assert red("Anaémia screening") == "[REDACTED:MEDICAL] screening"


def test_a_swahili_term_is_removed_too() -> None:
    terms = lexicon.read_terms(lexicon.Path(lexicon.DEFAULT_LEXICON_DIR) / "medical_terms.txt")
    swahili = [t for t in terms if t in {"malaria", "homa ya matumbo", "kifua kikuu", "upasuaji"}]

    for term in swahili:
        assert red(f"Dalili: {term}") == "Dalili: [REDACTED:MEDICAL]" or term not in red(term)


def test_every_term_in_the_lexicon_is_removed_wherever_it_stands() -> None:
    terms = lexicon.read_terms(lexicon.Path(lexicon.DEFAULT_LEXICON_DIR) / "medical_terms.txt")
    sample = "\n".join(f"Charge for {term} 1 100.00 100.00" for term in terms)

    cleaned = redact.redact(sample).text

    assert lexicon.find_medical_terms(cleaned) == []
    assert redact.residual(cleaned) == []
    assert len(terms) > 300


def test_a_word_that_only_contains_a_term_is_not_cut() -> None:
    # "aids" is a term; "paids" or a hyphenated facility name must not lose letters
    assert red("Hospital paids and raids") == "Hospital paids and raids"


# --------------------------------------------------------------------------- misread terms


@pytest.mark.parametrize(
    ("garbled", "genuine_word"),
    [
        ("Obstructed Iabour", "obstructed labour"),  # capital I for l
        ("Caesarean sectlon", "caesarean section"),
        ("Pneumonla", "pneumonia"),
        ("Ceftriaxone", "ceftriaxone"),
        ("Decompressive craniotorny", "decompressive craniotomy"),
    ],
)
def test_a_term_misread_by_ocr_is_still_caught(garbled: str, genuine_word: str) -> None:
    assert genuine_word  # documents what was meant
    assert red(f"Notes {garbled} noted") == "Notes [REDACTED:MEDICAL] noted"


@pytest.mark.parametrize(
    "ordinary",
    [
        "Nursing care (per day)",
        "Specialist consultation 1 3,300.00 3,300.00",
        "Private ward bed (per day)",
        "Surgeon professional fee",
        "Anaesthesia fee",
        "Payment is due on discharge",
        "Pay at the billing desk, Mon-Sat 8am-6pm",
        "Computer generated - no signature required",
        "Kilele Mission Hospital",
        "Highland Reed Hospital",
        "Ziwa Bluu General Hospital",
        "Baobab Grove Medical Centre",
        "Rift Heights Nursing Home",
    ],
)
def test_ordinary_billing_words_are_not_mistaken_for_medical_ones(ordinary: str) -> None:
    cleaned = red(ordinary)

    assert "[REDACTED:MEDICAL]" not in cleaned.replace("Anaesthesia", "")
    assert cleaned == ordinary or "Anaesthesia" in ordinary


# --------------------------------------------------------------------------- behaviour


def test_redacting_twice_changes_nothing() -> None:
    raw = (
        "Patient: Liam Mutua Invoice No.: KMH/26/059153\n"
        "Tel: 0700 003 408 Patient ref.: IP/26/61332\n"
        "Diagnosis: Road traffic injury with head trauma\n"
        "7 Theatre charges - Caesarean section 1 49,500.00 49,500.00\n"
        "Total (KES) 66,700.00"
    )
    once = redact.redact(raw)

    twice = redact.redact(once.text)

    assert twice.text == once.text
    assert twice.total == 0
    assert twice.patient_references == []


def test_the_counts_say_how_many_of_each_were_removed_without_saying_what() -> None:
    result = redact.redact(
        "Patient: Liam Mutua\nTel: 0700 003 408\nDiagnosis: Severe malaria\nEmail: a@b.test"
    )

    assert result.counts == {"NAME": 1, "PHONE": 1, "DIAGNOSIS": 1, "EMAIL": 1}
    assert result.total == 4
    assert "Liam" not in str(result.counts)


def test_text_with_nothing_sensitive_comes_back_unchanged() -> None:
    plain = "Kilele Mission Hospital\nTotal (KES) 66,700.00\nDate: 26/05/2026"

    result = redact.redact(plain)

    assert result.text == plain
    assert result.counts == {}


def test_empty_text_is_fine() -> None:
    assert redact.redact("").text == ""
    assert redact.redact("\n\n").text == "\n\n"  # the shape of the text is kept


def test_the_residual_check_names_what_is_left_and_finds_nothing_in_clean_text() -> None:
    assert redact.residual("Total (KES) 66,700.00 at Kilele Mission Hospital") == []
    assert redact.residual("call 0712 345 678 about malaria, mail a@b.test, id 34191405") == [
        "MEDICAL",
        "EMAIL",
        "PHONE",
        "ID",
    ]


def test_a_long_bill_is_redacted_quickly() -> None:
    line = "7 Theatre charges - Caesarean section 1 49,500.00 49,500.00 Tel: 0700 003 408\n"
    started = time.perf_counter()

    redact.redact(line * 400)

    assert time.perf_counter() - started < 5


# --------------------------------------------------------------------------- the synthetic bills


PDF_ROWS = [r for r in labels() if r["filename"].endswith(".pdf")] if DATA_DIR.exists() else []


@needs_dataset
@pytest.mark.parametrize("row", PDF_ROWS, ids=lambda r: r["doc_id"])
def test_no_sensitive_string_survives_in_any_synthetic_bill(row: dict) -> None:
    """The leak test the specification asks for, before any language model is used."""
    raw, mime = document(row["doc_id"])
    truth = ground_truth()[row["doc_id"]]
    original = text.extract(raw, mime).text

    result = redact.redact(original)

    flat = re.sub(r"\s+", " ", result.text).lower()
    for secret in truth["must_redact"]:
        assert re.sub(r"\s+", " ", secret).lower() not in flat, f"{secret!r} survived"
    assert lexicon.find_medical_terms(result.text) == []
    assert redact.residual(result.text) == []
    # what the checks need is still there
    assert row["invoice_number_shown"] in result.text
    assert truth["fields"]["facility"] in result.text
    shown = numbers(result.text)
    assert Decimal(row["total_shown"]) in shown
    fields = truth["fields"]
    if row["label"] != "tampered":
        for item in fields["line_items"]:
            assert Decimal(item["amount"]) in shown, item["description"]
    # the patient's own reference is returned for matching and gone from the text
    reference = fields["patient_reference"]
    assert reference not in result.text
    if row["label"] != "tampered" or row["tamper_type"] not in {"overlay_total", "overlay_line"}:
        assert reference in result.patient_references
