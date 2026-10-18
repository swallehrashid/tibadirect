"""Invoice content for the synthetic dataset: price catalog, clinical scenarios, fake identities.

Everything is invented. Prices are plausible-looking KES ranges, not real tariffs.
Phone numbers use an unassigned-looking 0700 00x block; random 8-digit IDs may
coincide with real ones by chance, so never treat them as real.
"""

FIRST_NAMES = [
    "Amina", "Brian", "Cynthia", "Daniel", "Esther", "Felix", "Grace", "Hassan", "Irene", "Joseph",
    "Karen", "Liam", "Mercy", "Nelson", "Olive", "Peter", "Queen", "Robert", "Sarah", "Timothy",
    "Umi", "Victor", "Winnie", "Yusuf", "Zawadi", "Faith", "Kevin", "Lilian", "Moses", "Naomi",
]
LAST_NAMES = [
    "Kamau", "Otieno", "Wanjiru", "Mwangi", "Achieng", "Kiptoo", "Njeri", "Omondi", "Chebet", "Mutua",
    "Wafula", "Akinyi", "Kariuki", "Hussein", "Njoroge", "Cheruiyot", "Atieno", "Maina", "Barasa", "Wekesa",
    "Mohamed", "Kilonzo", "Nduta", "Rotich", "Owino", "Karanja", "Mumbi", "Kimani", "Nyambura", "Juma",
]

# key -> description, price range (KES), qty range, rounding, kind, term (what a redactor must remove)
# per_day=True means qty equals the length of stay. {procedure} is filled in from the scenario.
CATALOG = {
    "consult_spec": dict(desc="Specialist consultation", price=(2500, 5000), qty=(1, 2), rnd=50, kind="other"),
    "bed_general": dict(desc="General ward bed (per day)", price=(3500, 8000), qty=None, rnd=50, kind="other", per_day=True),
    "bed_private": dict(desc="Private ward bed (per day)", price=(9000, 18000), qty=None, rnd=100, kind="other", per_day=True),
    "bed_hdu": dict(desc="High dependency unit (per day)", price=(18000, 32000), qty=None, rnd=100, kind="other", per_day=True),
    "bed_icu": dict(desc="Intensive care unit (per day)", price=(38000, 70000), qty=None, rnd=100, kind="other", per_day=True),
    "nursing": dict(desc="Nursing care (per day)", price=(1500, 3500), qty=None, rnd=50, kind="other", per_day=True),
    "oxygen": dict(desc="Oxygen therapy (per day)", price=(3000, 6000), qty=None, rnd=50, kind="proc", per_day=True,
                   term="Oxygen therapy"),
    "theatre": dict(desc="Theatre charges - {procedure}", price=(25000, 80000), qty=(1, 1), rnd=500, kind="proc"),
    "anaes": dict(desc="Anaesthesia fee", price=(15000, 45000), qty=(1, 1), rnd=500, kind="other"),
    "surgeon": dict(desc="Surgeon professional fee - {procedure}", price=(35000, 140000), qty=(1, 1), rnd=500, kind="proc"),
    "implant": dict(desc="Orthopaedic implant set", price=(60000, 180000), qty=(1, 1), rnd=500, kind="other"),
    "newborn_care": dict(desc="Newborn care and nursery", price=(6000, 14000), qty=(1, 1), rnd=100, kind="other"),
    # laboratory
    "cbc": dict(desc="Full haemogram", price=(800, 1500), qty=(1, 3), rnd=50, kind="other"),
    "malaria": dict(desc="Malaria parasite test", price=(400, 800), qty=(1, 3), rnd=50, kind="other"),
    "uec": dict(desc="Urea, electrolytes and creatinine", price=(1500, 2800), qty=(1, 3), rnd=50, kind="other"),
    "lft": dict(desc="Liver function tests", price=(2000, 3600), qty=(1, 2), rnd=50, kind="other"),
    "crp": dict(desc="C-reactive protein", price=(1500, 2500), qty=(1, 2), rnd=50, kind="other"),
    "bloodculture": dict(desc="Blood culture and sensitivity", price=(2500, 4800), qty=(1, 2), rnd=50, kind="other"),
    "crossmatch": dict(desc="Blood grouping and crossmatch", price=(2000, 4200), qty=(1, 3), rnd=50, kind="other"),
    "glucose": dict(desc="Random blood glucose", price=(300, 700), qty=(2, 6), rnd=10, kind="other"),
    "abg": dict(desc="Arterial blood gas analysis", price=(2500, 4000), qty=(1, 3), rnd=50, kind="other"),
    # imaging
    "usg_abd": dict(desc="Abdominal ultrasound", price=(3500, 6500), qty=(1, 1), rnd=50, kind="other"),
    "usg_obs": dict(desc="Obstetric ultrasound", price=(3500, 6000), qty=(1, 1), rnd=50, kind="other"),
    "cxr": dict(desc="Chest X-ray", price=(2000, 4000), qty=(1, 2), rnd=50, kind="other"),
    "xray_limb": dict(desc="X-ray limb (2 views)", price=(2500, 4500), qty=(1, 3), rnd=50, kind="other"),
    "ct_head": dict(desc="CT scan - head", price=(16000, 30000), qty=(1, 1), rnd=100, kind="other"),
    "ct_abd": dict(desc="CT scan - abdomen and pelvis", price=(22000, 42000), qty=(1, 1), rnd=100, kind="other"),
    # pharmacy (term = what must be redacted)
    "ceftriaxone": dict(desc="Ceftriaxone 1g injection", price=(350, 750), qty=(4, 14), rnd=10, kind="drug", term="Ceftriaxone"),
    "metronidazole": dict(desc="Metronidazole 500mg IV", price=(200, 450), qty=(6, 18), rnd=10, kind="drug", term="Metronidazole"),
    "paracetamol_iv": dict(desc="Paracetamol 1g IV infusion", price=(250, 600), qty=(4, 12), rnd=10, kind="drug", term="Paracetamol"),
    "artesunate": dict(desc="Artesunate 60mg injection", price=(700, 1400), qty=(3, 9), rnd=10, kind="drug", term="Artesunate"),
    "insulin": dict(desc="Soluble insulin 100IU/ml", price=(1800, 3200), qty=(1, 3), rnd=10, kind="drug", term="insulin"),
    "omeprazole": dict(desc="Omeprazole 40mg IV", price=(350, 800), qty=(3, 10), rnd=10, kind="drug", term="Omeprazole"),
    "tramadol": dict(desc="Tramadol 100mg injection", price=(150, 400), qty=(3, 9), rnd=10, kind="drug", term="Tramadol"),
    "oxytocin": dict(desc="Oxytocin 10IU injection", price=(200, 500), qty=(2, 6), rnd=10, kind="drug", term="Oxytocin"),
    "amoxiclav": dict(desc="Amoxicillin-clavulanate 1.2g IV", price=(500, 1100), qty=(6, 15), rnd=10, kind="drug", term="Amoxicillin"),
    "enoxaparin": dict(desc="Enoxaparin 40mg injection", price=(900, 1700), qty=(3, 8), rnd=10, kind="drug", term="Enoxaparin"),
    "ringer": dict(desc="Ringers lactate 1L", price=(180, 350), qty=(6, 16), rnd=10, kind="other"),
    "dextrose": dict(desc="Dextrose 5% 500ml", price=(150, 300), qty=(4, 12), rnd=10, kind="other"),
}

SCENARIOS = {
    "appendicitis": dict(
        diagnosis="Acute appendicitis", procedure="Appendicectomy", days=(2, 4),
        items=["consult_spec", "bed_general", "nursing", "cbc", "crp", "usg_abd", "theatre", "anaes",
               "surgeon", "ceftriaxone", "metronidazole", "paracetamol_iv", "tramadol"]),
    "caesarean": dict(
        diagnosis="Obstructed labour", procedure="Caesarean section", days=(3, 5),
        items=["consult_spec", "bed_private", "nursing", "cbc", "crossmatch", "usg_obs", "theatre", "anaes",
               "surgeon", "oxytocin", "ceftriaxone", "paracetamol_iv", "newborn_care"]),
    "malaria": dict(
        diagnosis="Severe malaria", procedure="Intravenous antimalarial therapy", days=(3, 6),
        items=["consult_spec", "bed_general", "nursing", "malaria", "cbc", "uec", "artesunate",
               "paracetamol_iv", "ringer", "dextrose"]),
    "fracture": dict(
        diagnosis="Fracture of the femur", procedure="Open reduction and internal fixation", days=(4, 8),
        items=["consult_spec", "bed_general", "nursing", "xray_limb", "cbc", "crossmatch", "theatre", "anaes",
               "surgeon", "implant", "ceftriaxone", "enoxaparin", "tramadol"]),
    "pneumonia": dict(
        diagnosis="Severe pneumonia", procedure="Oxygen therapy", days=(4, 8),
        items=["consult_spec", "bed_general", "nursing", "cxr", "cbc", "bloodculture", "oxygen", "amoxiclav",
               "paracetamol_iv", "omeprazole"]),
    "head_trauma": dict(
        diagnosis="Road traffic injury with head trauma", procedure="Decompressive craniotomy", days=(5, 12),
        items=["consult_spec", "bed_icu", "nursing", "ct_head", "cxr", "cbc", "crossmatch", "theatre", "anaes",
               "surgeon", "ceftriaxone", "omeprazole", "ringer"]),
    "dka": dict(
        diagnosis="Diabetic ketoacidosis", procedure="Insulin infusion", days=(3, 6),
        items=["consult_spec", "bed_hdu", "nursing", "glucose", "abg", "uec", "insulin", "ringer", "omeprazole"]),
    "kidney_stone": dict(
        diagnosis="Ureteric calculus", procedure="Ureteroscopic lithotripsy", days=(1, 3),
        items=["consult_spec", "bed_general", "nursing", "ct_abd", "cbc", "theatre", "anaes", "surgeon",
               "ceftriaxone", "tramadol"]),
    "typhoid": dict(
        diagnosis="Typhoid fever", procedure="Intravenous antibiotic therapy", days=(4, 7),
        items=["consult_spec", "bed_general", "nursing", "cbc", "bloodculture", "lft", "ceftriaxone",
               "paracetamol_iv", "ringer"]),
}

# Flat-priced package (identical totals at one hospital = hard negative for duplicate detection).
PACKAGE = dict(
    key="delivery_package", diagnosis="Normal delivery", procedure="Spontaneous vaginal delivery",
    lines=[("Maternity consultation", 1, 3500), ("Maternity ward bed (per day)", 2, 6000),
           ("Normal delivery package - {procedure}", 1, 45000), ("Newborn care and nursery", 1, 8000),
           ("Laboratory bundle", 1, 2500)],
)


def round_to(x, base):
    return int(round(x / base) * base)


def fake_phone(rng):
    return f"0700 00{rng.randint(0, 9)} {rng.randint(0, 999):03d}"


def fake_name(rng):
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def fake_patient(rng):
    """Fictional patient block. Every field is a redaction target (S4/AI-03)."""
    p = {"name": fake_name(rng)}
    p["id_no"] = str(rng.randint(20000000, 39999999)) if rng.random() < 0.6 else None
    p["sha_no"] = f"SHA-{rng.randint(100000000, 999999999)}" if rng.random() < 0.5 else None
    p["phone"] = fake_phone(rng) if rng.random() < 0.6 else None
    if rng.random() < 0.5:
        p["guarantor"] = fake_name(rng)
        p["guarantor_phone"] = fake_phone(rng)
    else:
        p["guarantor"] = p["guarantor_phone"] = None
    return p


def build_items(rng, scenario, factor):
    days = rng.randint(*scenario["days"])
    items = []
    for key in scenario["items"]:
        it = CATALOG[key]
        qty = days if it.get("per_day") else rng.randint(*it["qty"])
        price = max(it["rnd"], round_to(rng.uniform(*it["price"]) * factor, it["rnd"]))
        desc = it["desc"].format(procedure=scenario["procedure"])
        items.append(dict(description=desc, qty=qty, unit_price=price, amount=qty * price,
                          kind=it["kind"], term=it.get("term")))
    return items, days
