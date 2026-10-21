"""Fictional facility register for TibaDirect synthetic data.

Every facility, code, phone number and e-mail address here is invented. E-mail domains use the
reserved ".test" TLD so they can never resolve and are captured by the local mail catcher. Names
were checked by web search on 2026-10-19: none matches a real Kenyan facility.

The first eight facilities (HOSPITALS) also print invoices, so each carries a "style" block that
only controls how its documents look; the style is NOT part of the register. The other
facilities (EXTRA_FACILITIES) exist only in the register. facility_register.csv holds all of them
and is what `flask seed` loads and what the facility check (AI-06) matches against.
"""

REGISTER_SOURCE = "synthetic-register-v1"

REGISTER_COLUMNS = [
    "register_code", "name", "aliases", "county", "facility_level", "ownership",
    "official_email", "official_phone", "source",
]

# phone_variant: how the letterhead prints the official phone number
#   0 -> "+254 20 555 0101"   1 -> "020 555 0101"   2 -> "(020) 5550101"
HOSPITALS = [
    dict(
        id=1, name="Kilele Mission Hospital", aliases="Kilele Mission|KMH",
        register_code="FR-NBI-0101", county="Nairobi", level=5, ownership="faith-based",
        official_phone="+254 20 555 0101", official_email="billing@kilele-hospital.test",
        address="12 Kilele Avenue, Nairobi",
        style=dict(template="classic", color=(0.05, 0.33, 0.55), num_fmt="{:,.2f}", cur="KES",
                   date_fmt="%d/%m/%Y", inv_fmt="KMH/{yy}/{n:06d}", system="MediBill HMS 4.2",
                   phone_variant=0),
    ),
    dict(
        id=2, name="Mto Mwema Hospital", aliases="Mto Mwema|MMH",
        register_code="FR-MSA-0202", county="Mombasa", level=5, ownership="private",
        official_phone="+254 41 555 0202", official_email="billing@mtomwema-hospital.test",
        address="Shore Road, Mombasa",
        style=dict(template="modern", color=(0.0, 0.45, 0.42), num_fmt="{:,.2f}", cur="Ksh",
                   date_fmt="%d %b %Y", inv_fmt="MMH-{yyyy}-{n:05d}", system="CarePoint Billing 2.8",
                   phone_variant=1),
    ),
    dict(
        id=3, name="Ziwa Bluu General Hospital", aliases="Ziwa Bluu|ZBGH",
        register_code="FR-KSM-0303", county="Kisumu", level=4, ownership="faith-based",
        official_phone="+254 57 555 0303", official_email="billing@ziwabluu-hospital.test",
        address="Lakeview Drive, Kisumu",
        style=dict(template="compact", color=(0.15, 0.15, 0.15), num_fmt="{:,.0f}", cur="KES",
                   date_fmt="%Y-%m-%d", inv_fmt="ZB{yy}{n:07d}", system="HospiTrack Finance 7.1",
                   phone_variant=2),
    ),
    dict(
        id=4, name="Highland Reed Hospital", aliases="Highland Reed|HRH",
        register_code="FR-UGN-0404", county="Uasin Gishu", level=5, ownership="private",
        official_phone="+254 53 555 0404", official_email="billing@highlandreed.test",
        address="Reed Close, Eldoret",
        style=dict(template="classic", color=(0.10, 0.45, 0.20), num_fmt="{:,.2f}", cur="KSh",
                   date_fmt="%d-%m-%Y", inv_fmt="HRH/INV/{n:05d}/{yy}", system="MediBill HMS 4.2",
                   phone_variant=1),
    ),
    dict(
        id=5, name="Rift Heights Nursing Home", aliases="Rift Heights|RHNH",
        register_code="FR-NKR-0505", county="Nakuru", level=3, ownership="private",
        official_phone="+254 51 555 0505", official_email="admin@riftheights.test",
        address="Heights Lane, Nakuru",
        style=dict(template="modern", color=(0.55, 0.12, 0.20), num_fmt="{:.2f}", cur="KES",
                   date_fmt="%d.%m.%Y", inv_fmt="RH-{n:06d}", system="CarePoint Billing 2.8",
                   phone_variant=0),
    ),
    dict(
        id=6, name="Kijani Valley Hospital", aliases="Kijani Valley|KVH",
        register_code="FR-KBU-0606", county="Kiambu", level=4, ownership="private",
        official_phone="+254 20 555 0606", official_email="billing@kijanivalley.test",
        address="Valley Road, Kiambu",
        style=dict(template="classic", color=(0.78, 0.38, 0.05), num_fmt="{:,.2f}", cur="KES",
                   date_fmt="%d %B %Y", inv_fmt="KV/{yyyy}/{n:05d}", system="HospiTrack Finance 7.1",
                   phone_variant=2),
    ),
    dict(
        id=7, name="Mlima Kuu Referral Hospital", aliases="Mlima Kuu|MKRH",
        register_code="FR-NYR-0707", county="Nyeri", level=5, ownership="public",
        official_phone="+254 61 555 0707", official_email="revenue@mlimakuu.test",
        address="Hill Street, Nyeri",
        style=dict(template="compact", color=(0.1, 0.1, 0.1), num_fmt="{:,.2f}", cur="Ksh",
                   date_fmt="%d/%m/%y", inv_fmt="MKRH{n:08d}", system="MediBill HMS 4.2",
                   phone_variant=1),
    ),
    dict(
        id=8, name="Baobab Grove Medical Centre", aliases="Baobab Grove|BGMC",
        register_code="FR-MCK-0808", county="Machakos", level=4, ownership="private",
        official_phone="+254 44 555 0808", official_email="billing@baobabgrove.test",
        address="Grove Way, Machakos",
        style=dict(template="modern", color=(0.35, 0.20, 0.55), num_fmt="{:,.0f}", cur="KSh",
                   date_fmt="%d %b %Y", inv_fmt="BG/{yy}/{n:05d}", system="CarePoint Billing 2.8",
                   phone_variant=0),
    ),
]

# Register-only facilities: they appear in the register and the hospital directory but print no
# invoices in the dataset.
EXTRA_FACILITIES = [
    dict(register_code="FR-KLF-0909", name="Pwani Mwangaza Hospital", aliases="Pwani Mwangaza|PMH",
         county="Kilifi", level=4, ownership="private",
         official_phone="+254 41 555 0909", official_email="billing@pwanimwangaza.test"),
    dict(register_code="FR-KKG-1010", name="Nuru Valley Mission Hospital", aliases="Nuru Valley|NVMH",
         county="Kakamega", level=4, ownership="faith-based",
         official_phone="+254 56 555 1010", official_email="accounts@nuruvalley.test"),
    dict(register_code="FR-NRK-1111", name="Tembo Ridge Hospital", aliases="Tembo Ridge|TRH",
         county="Narok", level=4, ownership="private",
         official_phone="+254 50 555 1111", official_email="billing@temboridge.test"),
    dict(register_code="FR-KJD-1212", name="Simba Plains Hospital", aliases="Simba Plains|SPH",
         county="Kajiado", level=4, ownership="private",
         official_phone="+254 45 555 1212", official_email="billing@simbaplains.test"),
    dict(register_code="FR-LMU-1313", name="Bahari Breeze Medical Centre", aliases="Bahari Breeze|BBMC",
         county="Lamu", level=3, ownership="private",
         official_phone="+254 42 555 1313", official_email="admin@baharibreeze.test"),
    dict(register_code="FR-MRU-1414", name="Mwanga Hills Hospital", aliases="Mwanga Hills|MHH",
         county="Meru", level=4, ownership="faith-based",
         official_phone="+254 64 555 1414", official_email="billing@mwangahills.test"),
    dict(register_code="FR-LKP-1515", name="Ndovu Springs Hospital", aliases="Ndovu Springs|NSH",
         county="Laikipia", level=4, ownership="private",
         official_phone="+254 62 555 1515", official_email="billing@ndovusprings.test"),
    dict(register_code="FR-HMB-1616", name="Upendo Lakeside Hospital", aliases="Upendo Lakeside|ULH",
         county="Homa Bay", level=4, ownership="faith-based",
         official_phone="+254 59 555 1616", official_email="accounts@upendolakeside.test"),
    dict(register_code="FR-KSI-1717", name="Taifa Heights Referral Hospital", aliases="Taifa Heights|THRH",
         county="Kisii", level=5, ownership="public",
         official_phone="+254 58 555 1717", official_email="revenue@taifaheights.test"),
    dict(register_code="FR-NBI-1818", name="Zawadi Gardens Hospital", aliases="Zawadi Gardens|ZGH",
         county="Nairobi", level=4, ownership="private",
         official_phone="+254 20 555 1818", official_email="billing@zawadigardens.test"),
    dict(register_code="FR-KCH-1919", name="Mbega Forest Hospital", aliases="Mbega Forest|MFH",
         county="Kericho", level=4, ownership="private",
         official_phone="+254 52 555 1919", official_email="billing@mbegaforest.test"),
    dict(register_code="FR-GRS-2020", name="Chui Rocks Medical Centre", aliases="Chui Rocks|CRMC",
         county="Garissa", level=3, ownership="public",
         official_phone="+254 46 555 2020", official_email="admin@chuirocks.test"),
    dict(register_code="FR-TTV-2121", name="Tai Valley Mission Hospital", aliases="Tai Valley|TVMH",
         county="Taita Taveta", level=4, ownership="faith-based",
         official_phone="+254 43 555 2121", official_email="billing@taivalley.test"),
    dict(register_code="FR-KTU-2222", name="Pendo Savannah Hospital", aliases="Pendo Savannah|PSH",
         county="Kitui", level=4, ownership="private",
         official_phone="+254 44 555 2222", official_email="billing@pendosavannah.test"),
    dict(register_code="FR-NKR-2323", name="Safari Gate Nursing Home", aliases="Safari Gate|SGNH",
         county="Nakuru", level=3, ownership="private",
         official_phone="+254 51 555 2323", official_email="admin@safarigate.test"),
    dict(register_code="FR-EMB-2424", name="Mawingu Hills Hospital", aliases="Mawingu Hills|MWH",
         county="Embu", level=4, ownership="private",
         official_phone="+254 68 555 2424", official_email="billing@mawinguhills.test"),
    dict(register_code="FR-SYA-2525", name="Nyota Bay Hospital", aliases="Nyota Bay|NBH",
         county="Siaya", level=4, ownership="public",
         official_phone="+254 57 555 2525", official_email="revenue@nyotabay.test"),
]

# Names that are close to a real register entry but not exact (facility fuzzy-match negatives).
LOOKALIKES = {
    1: ("Kilele Mision Hospital", "FR-NBI-0110"),
    2: ("Mto Mwema Hospitals Ltd", "FR-MSA-0220"),
    4: ("Highland Reed Hospitals Ltd", "FR-UGN-0440"),
    6: ("Kijani Vally Hospital", "FR-KBU-0660"),
}

# Facilities that appear nowhere in the register.
UNKNOWN_FACILITIES = [
    dict(name="Neema Family Care Clinic", register_code="FR-NBI-0999", county="Nairobi",
         address="Hope Street, Nairobi", email="info@neemafamilycare.test", phone="+254 20 555 0999"),
    dict(name="Amani Crest Specialist Hospital", register_code="FR-KSM-0777", county="Kisumu",
         address="Peace Road, Kisumu", email="billing@amanicrest.test", phone="+254 57 555 0777"),
    dict(name="Tumaini Heights Hospital", register_code="FR-NKR-0888", county="Nakuru",
         address="Heights Close, Nakuru", email="accounts@tumainiheights.test", phone="+254 51 555 0888"),
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
    """Every register entry as a dict keyed by REGISTER_COLUMNS, in register-code order."""
    rows = []
    for h in [*HOSPITALS, *EXTRA_FACILITIES]:
        rows.append(dict(
            register_code=h["register_code"], name=h["name"], aliases=h["aliases"],
            county=h["county"], facility_level=f"Level {h['level']}", ownership=h["ownership"],
            official_email=h["official_email"], official_phone=h["official_phone"],
            source=REGISTER_SOURCE,
        ))
    return sorted(rows, key=lambda r: r["register_code"])
