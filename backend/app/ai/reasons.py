"""Plain-language reasons for a risk score, in English and Swahili.

Families see the band and up to three reasons, never the weights or the signal codes; hospitals
and admins see the codes too. The wording says what was noticed, not what it means: the
automatic checks only point at things, and the hospital's own confirmation decides.
"""

from app.ai.signals import Signal

TOP = 3

# code -> (English, Swahili). The Swahili is unreviewed.  # needs native review
REASONS: dict[str, tuple[str, str]] = {
    "duplicate_exact": (
        "This exact file has already been used for another case.",
        "Faili hii hii imeshatumika kwenye kesi nyingine.",
    ),
    "duplicate_near": (
        "This bill looks the same as one already used for another case.",
        "Bili hii inafanana na nyingine iliyotumika kwenye kesi nyingine.",
    ),
    "duplicate_fingerprint": (
        "The same invoice number and total already appear on another case.",
        "Nambari ya ankara na jumla hiyo hiyo tayari ziko kwenye kesi nyingine.",
    ),
    "patient_active_elsewhere": (
        "This patient already has another open case at the same hospital.",
        "Mgonjwa huyu tayari ana kesi nyingine iliyo wazi katika hospitali hiyo hiyo.",
    ),
    "facility_not_in_register": (
        "We could not find this hospital in the facility register.",
        "Hatukuipata hospitali hii kwenye rejista ya vituo vya afya.",
    ),
    "facility_weak_match": (
        "The hospital name on the bill only partly matches the facility register.",
        "Jina la hospitali kwenye bili linafanana kwa sehemu tu na rejista ya vituo vya afya.",
    ),
    "facility_mismatch_selected": (
        "The hospital on the bill is not the one chosen for this case.",
        "Hospitali iliyo kwenye bili si ile iliyochaguliwa kwa kesi hii.",
    ),
    "facility_code_mismatch": (
        "The registration number on the bill does not match the hospital's entry in the register.",
        "Nambari ya usajili kwenye bili hailingani na ya hospitali kwenye rejista.",
    ),
    "contact_mismatch": (
        "The phone number or email at the top of the bill is not the hospital's registered one.",
        "Simu au barua pepe iliyo juu ya bili si ile iliyosajiliwa ya hospitali.",
    ),
    "pdf_incremental_updates": (
        "The file was saved again after it was first made.",
        "Faili ilihifadhiwa tena baada ya kutengenezwa mara ya kwanza.",
    ),
    "pdf_editor_software": (
        "The file was made or changed with editing software.",
        "Faili ilitengenezwa au kubadilishwa kwa programu ya kuhariri.",
    ),
    "pdf_modified_after_creation": (
        "The file was changed more than a day after it was created.",
        "Faili ilibadilishwa zaidi ya siku moja baada ya kutengenezwa.",
    ),
    "totals_mismatch": (
        "The line items do not add up to the total on the bill.",
        "Vipengee vya bili havijumlishi hadi jumla iliyoandikwa.",
    ),
    "line_math_mismatch": (
        "On some lines, the quantity times the price is not the amount shown.",
        "Kwenye mistari mingine, idadi mara bei si kiasi kilichoonyeshwa.",
    ),
    "hidden_text_under_overlay": (
        "Part of the bill has been covered over and typed again.",
        "Sehemu ya bili imefunikwa na kuandikwa upya.",
    ),
    "font_inconsistency": (
        "Some amounts on the bill are in a different typeface from the rest.",
        "Baadhi ya kiasi kwenye bili kiko kwenye aina tofauti ya herufi na kingine.",
    ),
    "image_ela_high": (
        "Parts of the picture look as if they were edited.",
        "Sehemu za picha zinaonekana kuhaririwa.",
    ),
    "exif_editor_software": (
        "The photo's details say it was edited with an image editor.",
        "Maelezo ya picha yanasema ilihaririwa kwa programu ya kuhariri picha.",
    ),
    "low_extraction_confidence": (
        "We could not read all of the bill clearly.",
        "Hatukuweza kusoma bili yote kwa uwazi.",
    ),
    "extraction_disagreement": (
        "Two readings of the bill gave different totals.",
        "Usomaji miwili wa bili ulitoa jumla tofauti.",
    ),
    "instruction_like_text": (
        "The bill contains text that looks written to give instructions to a computer.",
        "Bili ina maandishi yanayoonekana kuandikwa kutoa maagizo kwa kompyuta.",
    ),
}

NOTHING_FOUND = (
    "The automatic checks found nothing unusual. The hospital will still confirm the bill.",
    "Ukaguzi wa kiotomatiki haukuona kitu cha kushangaza. Hospitali bado itathibitisha bili.",
)


def reasons_for(signals: list[Signal]) -> tuple[list[str], list[str]]:
    """The English and Swahili reasons for the top three signals by weight (ties keep the order
    the checks raised them in). With no signals, a single reassuring line."""
    ranked = sorted(enumerate(signals), key=lambda pair: (-pair[1].weight, pair[0]))[:TOP]
    if not ranked:
        return [NOTHING_FOUND[0]], [NOTHING_FOUND[1]]
    return (
        [REASONS[signal.code][0] for _, signal in ranked],
        [REASONS[signal.code][1] for _, signal in ranked],
    )
