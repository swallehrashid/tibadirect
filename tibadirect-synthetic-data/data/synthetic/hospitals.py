"""Fictional hospital register for TibaDirect synthetic data.

Every hospital, code, phone number and e-mail address here is invented.
E-mail domains use the reserved ".example" TLD so they can never resolve.
The public columns (see REGISTER_COLUMNS) are what ends up in
hospital_register.csv/json, which S1 loads as seed data and AI-06 (facility
check) matches against. The "style" block only controls how that hospital's
invoices look and is NOT part of the register.
"""

REGISTER_COLUMNS = [
    "id", "name", "aliases", "register_code", "county", "level",
    "official_phone", "official_email", "paypal_merchant_id", "settlement_method",
]

# phone_variant: how the letterhead prints the official phone number
#   0 -> "+254 20 555 0101"   1 -> "020 555 0101"   2 -> "(020) 5550101"
HOSPITALS = [
    dict(
        id=1, name="Savannah Crest Medical Centre", aliases="Savannah Crest MC|SCMC",
        register_code="FR-NBI-0101", county="Nairobi", level=5,
        official_phone="+254 20 555 0101", official_email="billing@savannahcrest.example",
        paypal_merchant_id="SANDBOX_MERCHANT_A", settlement_method="paypal_payouts",
        address="12 Crest Avenue, Nairobi",
        style=dict(template="classic", color=(0.05, 0.33, 0.55), num_fmt="{:,.2f}", cur="KES",
                   date_fmt="%d/%m/%Y", inv_fmt="SCM/{yy}/{n:06d}", system="MediBill HMS 4.2",
                   phone_variant=0),
    ),
    dict(
        id=2, name="Pwani Sands Hospital", aliases="Pwani Sands|PSH",
        register_code="FR-MSA-0202", county="Mombasa", level=5,
        official_phone="+254 41 555 0202", official_email="accounts@pwanisands.example",
        paypal_merchant_id="SANDBOX_MERCHANT_B", settlement_method="paypal_payouts",
        address="Shore Road, Mombasa",
        style=dict(template="modern", color=(0.0, 0.45, 0.42), num_fmt="{:,.2f}", cur="Ksh",
                   date_fmt="%d %b %Y", inv_fmt="PSH-{yyyy}-{n:05d}", system="CarePoint Billing 2.8",
                   phone_variant=1),
    ),
    dict(
        id=3, name="Ziwa Bright Hospital", aliases="Ziwa Bright|ZBH",
        register_code="FR-KSM-0303", county="Kisumu", level=4,
        official_phone="+254 57 555 0303", official_email="finance@ziwabright.example",
        paypal_merchant_id="", settlement_method="mock_partner",
        address="Lakeview Drive, Kisumu",
        style=dict(template="compact", color=(0.15, 0.15, 0.15), num_fmt="{:,.0f}", cur="KES",
                   date_fmt="%Y-%m-%d", inv_fmt="ZB{yy}{n:07d}", system="HospiTrack Finance 7.1",
                   phone_variant=2),
    ),
    dict(
        id=4, name="Highland Reed Hospital", aliases="Highland Reed|HRH",
        register_code="FR-UGN-0404", county="Uasin Gishu", level=5,
        official_phone="+254 53 555 0404", official_email="billing@highlandreed.example",
        paypal_merchant_id="", settlement_method="jenga_stub",
        address="Reed Close, Eldoret",
        style=dict(template="classic", color=(0.10, 0.45, 0.20), num_fmt="{:,.2f}", cur="KSh",
                   date_fmt="%d-%m-%Y", inv_fmt="HRH/INV/{n:05d}/{yy}", system="MediBill HMS 4.2",
                   phone_variant=1),
    ),
    dict(
        id=5, name="Rift Heights Nursing Home", aliases="Rift Heights|RHNH",
        register_code="FR-NKR-0505", county="Nakuru", level=3,
        official_phone="+254 51 555 0505", official_email="admin@riftheights.example",
        paypal_merchant_id="", settlement_method="mock_partner",
        address="Heights Lane, Nakuru",
        style=dict(template="modern", color=(0.55, 0.12, 0.20), num_fmt="{:.2f}", cur="KES",
                   date_fmt="%d.%m.%Y", inv_fmt="RH-{n:06d}", system="CarePoint Billing 2.8",
                   phone_variant=0),
    ),
    dict(
        id=6, name="Kijani Valley Hospital", aliases="Kijani Valley|KVH",
        register_code="FR-KBU-0606", county="Kiambu", level=4,
        official_phone="+254 20 555 0606", official_email="billing@kijanivalley.example",
        paypal_merchant_id="", settlement_method="jenga_stub",
        address="Valley Road, Kiambu",
        style=dict(template="classic", color=(0.78, 0.38, 0.05), num_fmt="{:,.2f}", cur="KES",
                   date_fmt="%d %B %Y", inv_fmt="KV/{yyyy}/{n:05d}", system="HospiTrack Finance 7.1",
                   phone_variant=2),
    ),
    dict(
        id=7, name="Mlima Kuu Referral Hospital", aliases="Mlima Kuu|MKRH",
        register_code="FR-NYR-0707", county="Nyeri", level=5,
        official_phone="+254 61 555 0707", official_email="revenue@mlimakuu.example",
        paypal_merchant_id="", settlement_method="mock_partner",
        address="Hill Street, Nyeri",
        style=dict(template="compact", color=(0.1, 0.1, 0.1), num_fmt="{:,.2f}", cur="Ksh",
                   date_fmt="%d/%m/%y", inv_fmt="MKRH{n:08d}", system="MediBill HMS 4.2",
                   phone_variant=1),
    ),
    dict(
        id=8, name="Baobab Grove Medical Centre", aliases="Baobab Grove|BGMC",
        register_code="FR-MCK-0808", county="Machakos", level=4,
        official_phone="+254 44 555 0808", official_email="billing@baobabgrove.example",
        paypal_merchant_id="", settlement_method="jenga_stub",
        address="Grove Way, Machakos",
        style=dict(template="modern", color=(0.35, 0.20, 0.55), num_fmt="{:,.0f}", cur="KSh",
                   date_fmt="%d %b %Y", inv_fmt="BG/{yy}/{n:05d}", system="CarePoint Billing 2.8",
                   phone_variant=0),
    ),
]

# Names that are close to a real register entry but not exact (facility fuzzy-match negatives).
LOOKALIKES = {
    1: ("Savanna Crest Medical Center", "FR-NBI-0110"),
    2: ("Pwani Sand Hospital", "FR-MSA-0220"),
    4: ("Highland Reed Hospitals Ltd", "FR-UGN-0440"),
    6: ("Kijani Vally Hospital", "FR-KBU-0660"),
}

# Facilities that appear nowhere in the register.
UNKNOWN_FACILITIES = [
    dict(name="Neema Family Care Clinic", register_code="FR-NBI-0999", county="Nairobi",
         address="Hope Street, Nairobi", email="info@neemafamilycare.example", phone="+254 20 555 0999"),
    dict(name="Amani Crest Specialist Hospital", register_code="FR-KSM-0777", county="Kisumu",
         address="Peace Road, Kisumu", email="billing@amanicrest.example", phone="+254 57 555 0777"),
    dict(name="Tumaini Heights Hospital", register_code="FR-NKR-0888", county="Nakuru",
         address="Heights Close, Nakuru", email="accounts@tumainiheights.example", phone="+254 51 555 0888"),
]

BILLING_SYSTEMS = sorted({h["style"]["system"] for h in HOSPITALS})


def format_phone(phone: str, variant: int) -> str:
    """Render '+254 20 555 0101' the way a letterhead might print it."""
    parts = phone.split()          # ['+254', '20', '555', '0101']
    if variant == 0 or len(parts) != 4:
        return phone
    if variant == 1:
        return f"0{parts[1]} {parts[2]} {parts[3]}"
    return f"(0{parts[1]}) {parts[2]}{parts[3]}"


def register_rows():
    return [{k: h[k] for k in REGISTER_COLUMNS} for h in HOSPITALS]
