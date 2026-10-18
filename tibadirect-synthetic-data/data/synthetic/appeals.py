"""Synthetic fundraising appeals for the appeal checker (AI-08).

100+ short messages in English, Swahili and code-switched ("mixed") text, labelled
genuine or scam. Some genuine appeals reference a genuine synthetic invoice
(matches_doc_id) so the checker's "link to a verified case" path can be tested.
Hard cases are deliberate: genuine appeals that mention M-Pesa paybills or are very
short, and scams that are calm, formal and name a registered hospital.

Swahili and Sheng-style text was written for testing only and needs a native-speaker
review before it is shown to users (UX-06).
"""
import csv
import random
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from catalog import fake_name, fake_phone
from hospitals import HOSPITALS

RELATIONS = [  # en, en-cap, sw, sw-cap, mixed
    ("my mother", "My mother", "mama yangu", "Mama yangu", "Mama"),
    ("my brother", "My brother", "kaka yangu", "Kaka yangu", "Kaka yangu"),
    ("my daughter", "My daughter", "binti yangu", "Binti yangu", "Binti yangu"),
    ("my father", "My father", "baba yangu", "Baba yangu", "Baba"),
    ("my sister", "My sister", "dada yangu", "Dada yangu", "Dada yangu"),
    ("my husband", "My husband", "mume wangu", "Mume wangu", "Mume wangu"),
]
COUNTIES = ["Nakuru", "Kisii", "Meru", "Embu", "Kakamega", "Bungoma", "Kitale", "Garissa", "Nyeri", "Machakos"]

# ---- genuine templates: (lang, difficulty, needs_deposit, text)
GENUINE = [
    ("en", "easy", True, "Hello family and friends. {rel_cap} has been admitted at {hospital} and the bill so far is KES {total}. We have already paid KES {deposit} and the balance is KES {balance}. Please do not send money to any phone number - the hospital billing desk ({hphone}) can confirm invoice {inv} and receive payment directly. I can share a copy of the invoice if you ask. Thank you for your prayers and support."),
    ("en", "easy", False, "Dear all, we need help clearing a hospital bill for {rel} at {hospital}. Invoice no. {inv}, balance KES {balance}. The hospital will confirm the amount if you call their billing office on {hphone}. We would prefer payments go straight to the hospital, not to a person. I will post the receipt here once it is paid."),
    ("en", "easy", False, "Good evening everyone. {rel_cap} is recovering at {hospital} but the bill has reached KES {total}. Insurance covered part of it and the remaining balance is KES {balance}. If you can contribute, we are collecting through a verified page where the money goes to the hospital directly. Happy to answer questions or share the bill with anyone who wants to check."),
    ("en", "hard", False, "Please help if you can. {hospital}, invoice {inv}, balance KES {balance}. Pay the hospital directly, billing desk {hphone}. Receipts will be shared."),
    ("en", "easy", False, "Hi cousins, many of you have asked how to help with {rel}'s hospital costs. {hospital} has given us an estimate of KES {total}. Please verify with their accounts office on {hphone} before sending anything. We will keep a list of who contributed and share the receipts."),
    ("en", "hard", True, "It has been a very hard week. {rel_cap} is still at {hospital}. We have paid KES {deposit} but still owe KES {balance} (invoice {inv}). I know many of you are far away, so please only pay through the hospital accounts office on {hphone} or the verified link, never to me personally."),
    ("en", "hard", False, "You can pay using the hospital paybill {paybill}, account number {inv}. Please confirm with the billing office on {hphone} first. {hospital}, balance KES {balance} for {rel}."),
    ("sw", "easy", True, "Habari wapendwa. {rel_sw_cap} amelazwa katika {hospital} na bili ya hospitali sasa ni KSh {total}. Tumeshalipa KSh {deposit}, salio ni KSh {balance}. Tafadhali msitume pesa kwa namba ya simu ya mtu yeyote. Dawati la malipo la hospitali ({hphone}) linaweza kuthibitisha ankara namba {inv}. Nitawatumia nakala ya ankara mkitaka. Asanteni kwa upendo wenu."),
    ("sw", "easy", False, "Wapendwa, tunaomba msaada wa kulipa bili ya hospitali ya {rel_sw} katika {hospital}. Ankara {inv}, salio ni KSh {balance}. Mnaweza kupiga simu ofisi ya malipo ya hospitali kwa {hphone} kuthibitisha. Tunapendelea malipo yaende moja kwa moja hospitalini. Nitaweka risiti hapa baada ya kulipa."),
    ("sw", "easy", False, "Salamu kwa wote. Bili ya matibabu katika {hospital} imefika KSh {total}. Bima imelipa sehemu, na salio ni KSh {balance}. Kama mnaweza kuchangia, tunatumia ukurasa uliothibitishwa ambapo pesa huenda hospitalini moja kwa moja. Mko huru kuuliza au kuona ankara."),
    ("sw", "hard", False, "Tafadhali thibitisheni kwanza na hospitali kabla ya kutuma chochote ({hphone}). {hospital}, ankara {inv}, salio KSh {balance}. Tutashiriki risiti zote."),
    ("mixed", "easy", False, "Guys, tunaomba msaada. {rel_mx} ako admitted {hospital}, bill ni KSh {total}, balance ni KSh {balance}. Tafadhali lipa direct kwa hospital, billing office {hphone}, invoice {inv}. Tutashare receipt kila mtu."),
    ("mixed", "hard", True, "Hello fam, {rel_mx} bado yuko {hospital}. Tumelipa KSh {deposit} lakini balance ni KSh {balance}. Mnaweza confirm na billing desk ({hphone}) before kutuma. Asanteni sana."),
    ("mixed", "hard", False, "Wasee, naomba mchango wa hospital bill. {hospital}, invoice {inv}. Pesa inaenda hospitali directly, si kwa mtu. Confirm na {hphone}."),
]

# ---- scam templates: (scam_type, lang, difficulty, text)
SCAM = [
    ("personal_mpesa_collection", "en", "easy", "PLEASE HELP!! My {child} is very sick in hospital. Send any amount to M-Pesa {phone} ({name}). God bless you."),
    ("personal_mpesa_collection", "en", "easy", "Dear friends, I am collecting money for a hospital bill. Kindly send to {phone} name {name}. Even KES 100 will help. Thank you and God bless."),
    ("personal_mpesa_collection", "sw", "easy", "Naomba msaada ndugu zangu. Mgonjwa wangu yuko hospitali. Tuma mchango kwa {phone} jina {name}. Mungu awabariki."),
    ("personal_mpesa_collection", "mixed", "easy", "Guys msaada tafadhali, {child} ni mgonjwa. Tuma kwa {phone} ({name}), any amount inasaidia."),
    ("urgency_pressure", "en", "easy", "URGENT!!! Surgery tonight or {rel} will die. No time to verify, send money NOW to {phone}. Every minute counts!!!"),
    ("urgency_pressure", "sw", "easy", "HARAKA SANA!!! Upasuaji ni usiku wa leo la sivyo {rel_sw} atafariki. Hakuna muda wa kuuliza, tuma sasa hivi kwa {phone}."),
    ("urgency_pressure", "mixed", "easy", "Guys URGENT!! kuna upasuaji leo usiku, tuma pesa sasa kwa {phone}. Hakuna time ya kuuliza maswali."),
    ("no_verifiable_details", "en", "easy", "Please I need your help. My family is in a very bad situation and the hospital needs a lot of money. I cannot say more here. Please send what you can."),
    ("no_verifiable_details", "sw", "easy", "Ndugu zangu nina shida kubwa sana. Hospitali inataka pesa nyingi. Siwezi kueleza zaidi hapa. Tuma chochote ulicho nacho kwa {phone}."),
    ("no_verifiable_details", "mixed", "easy", "Wasee nimekwama sana, hospital inataka pesa nyingi. Siwezi explain hapa. Tuma chochote kwa {phone}."),
    ("impersonation_of_hospital", "en", "hard", "{hospital_upper} BILLING OFFICE: Dear well-wisher, the patient's bill of KES {balance} must be settled today. Please send payment to our agent on {phone} and quote the patient name. Do not call the main line, it is overloaded."),
    ("impersonation_of_hospital", "sw", "hard", "OFISI YA MALIPO {hospital_upper}: Mfadhili mpendwa, bili ya mgonjwa KSh {balance} lazima ilipwe leo. Tuma malipo kwa wakala wetu {phone}. Usipige simu ya hospitali, laini ina shughuli nyingi."),
    ("impersonation_of_hospital", "en", "easy", "Hospital accounts: send {balance} to till or {phone} before 5pm, otherwise patient will be discharged without treatment!!"),
    ("advance_fee", "en", "easy", "Congratulations. A donor has approved KES {balance} for your hospital bill. To release the funds, send a processing fee of KES {fee} to {phone}."),
    ("advance_fee", "sw", "easy", "Hongera. Mfadhili ameidhinisha KSh {balance} kwa bili yako ya hospitali. Ili kutoa pesa, tuma ada ya usindikaji KSh {fee} kwa {phone}."),
    ("fake_charity_link", "en", "easy", "Support {child}'s treatment. Donate here: http://give-now.example/{code} (limited time, 100% goes to the patient). Share with 10 groups."),
    ("fake_charity_link", "sw", "easy", "Changia matibabu ya {child}. Changia hapa: http://changia-sasa.example/{code}. Sambaza kwa makundi 10."),
    ("refuses_verification", "en", "easy", "I have been told not to share hospital details for privacy. Just trust me and send to {phone}. Calling the hospital will only delay treatment."),
    ("refuses_verification", "sw", "easy", "Nimeambiwa nisitoe maelezo ya hospitali kwa sababu ya faragha. Niaminini tu, tuma kwa {phone}. Kupiga simu hospitali kutachelewesha matibabu."),
    ("calm_personal_payee", "en", "hard", "Hello everyone. {rel_cap} is admitted at {hospital} and the balance is KES {balance}. The hospital accounts system is offline this week, so my cousin {name} is paying on our behalf. Please send to {phone} and I will share the receipt afterwards."),
    ("calm_personal_payee", "sw", "hard", "Habari wapendwa. {rel_sw_cap} amelazwa {hospital}, salio ni KSh {balance}. Mfumo wa malipo wa hospitali haufanyi kazi wiki hii, kwa hivyo binamu yangu {name} analipa kwa niaba yetu. Tuma kwa {phone}, nitawatumia risiti baadaye."),
    ("calm_personal_payee", "en", "hard", "Good morning. We are raising KES {balance} for {rel} at {hospital}. Invoice {inv}. To avoid delays, please send via M-Pesa to {name} ({phone}); the hospital accepts payment from one person only."),
    ("calm_personal_payee", "mixed", "hard", "Hello fam, {rel_mx} yuko {hospital}, balance ni KSh {balance}. Hospital haikubali payments from many people, so send to {phone} ({name}), nitalipa mara moja."),
]
RECYCLED = [
    ("en", "I am {name}, a single mother from {county}. My son was knocked down by a motorbike and needs an operation. The hospital says KES {amt} must be paid before they start. Please send to {phone}. May God reward you."),
    ("sw", "Mimi ni {name}, mama wa watoto watatu kutoka {county}. Mume wangu amepata ajali na anahitaji upasuaji. Hospitali inataka KSh {amt}. Tuma kwa {phone}. Mungu awabariki."),
]
SCAM_COUNTS = {  # how many to draw from the template pool, per type
    "personal_mpesa_collection": 8, "urgency_pressure": 7, "no_verifiable_details": 5,
    "impersonation_of_hospital": 5, "advance_fee": 3, "fake_charity_link": 4,
    "refuses_verification": 4, "calm_personal_payee": 6,
}
N_GENUINE, N_LINKED, N_RECYCLED = 50, 15, 8


def _fmt(n):
    return f"{n:,}"


def _context(rng, hosp=None, fields=None):
    h = hosp or rng.choice(HOSPITALS)
    rel = rng.choice(RELATIONS)
    if fields:
        total, balance, deposit, inv = fields["total"], fields["balance_due"], fields["deposit_paid"], fields["invoice_number"]
    else:
        total = rng.randrange(60000, 900000, 500)
        deposit = rng.randrange(10000, 120000, 1000) if rng.random() < 0.5 else 0
        balance, inv = total - deposit, f"{h['style']['inv_fmt'].format(yy=26, yyyy=2026, n=rng.randint(1000, 98000))}"
    return dict(hospital=h["name"], hospital_upper=h["name"].upper(), hphone=h["official_phone"], inv=inv,
                total=_fmt(total), deposit=_fmt(deposit), balance=_fmt(balance), _deposit=deposit,
                rel=rel[0], rel_cap=rel[1], rel_sw=rel[2], rel_sw_cap=rel[3], rel_mx=rel[4],
                paybill=f"5{rng.randint(10000, 99999)}", phone=fake_phone(rng), name=fake_name(rng),
                fee=_fmt(rng.randrange(1500, 9000, 500)), code="".join(rng.choices("abcdefghjkmnpqrstuvwxyz23456789", k=6)),
                child=rng.choice(["son", "daughter", "baby", "mother", "father"]),
                amt=_fmt(rng.randrange(80000, 350000, 1000)), county=rng.choice(COUNTIES))


def _decorate(rng, text, channel):
    if rng.random() < 0.25:
        text += " \U0001F64F"
    if channel == "facebook_post" and rng.random() < 0.5:
        text += "\n\nPlease share."
    return text


def generate(out: Path, seed: int, scale: int, invoice_gt):
    rng = random.Random(seed + 1)
    by_name = {h["name"]: h for h in HOSPITALS}
    linkable = [g for g in invoice_gt if g["label"] == "genuine" and g["fields"]["document_type"] == "invoice"
                and g["fields"]["facility"] in by_name]
    rng.shuffle(linkable)
    rows, seen = [], set()

    def add(text, lang, label, scam_type, difficulty, group="", match=""):
        channel = rng.choices(["whatsapp_text", "facebook_post", "sms"], weights=[0.6, 0.25, 0.15])[0]
        text = _decorate(rng, text, channel)
        if text in seen:
            return False
        seen.add(text)
        rows.append(dict(text=text, language=lang, label=label, scam_type=scam_type, difficulty=difficulty,
                         channel=channel, story_group=group, matches_doc_id=match))
        return True

    # genuine
    for i in range(N_GENUINE * scale):
        linked = linkable[i] if i < N_LINKED * scale and i < len(linkable) else None
        for _ in range(50):
            ctx = _context(rng, by_name[linked["fields"]["facility"]] if linked else None,
                           linked["fields"] if linked else None)
            lang, diff, needs_dep, tpl = rng.choice(GENUINE)
            if needs_dep and not ctx["_deposit"]:
                continue
            if add(tpl.format(**ctx), lang, "genuine", "", diff, match=linked["doc_id"] if linked else ""):
                break
    # scam
    for stype, n in SCAM_COUNTS.items():
        pool = [t for t in SCAM if t[0] == stype]
        for _ in range(n * scale):
            for _ in range(50):
                _, lang, diff, tpl = rng.choice(pool)
                if add(tpl.format(**_context(rng)), lang, "scam", stype, diff):
                    break
    for g in range(2 * scale):                     # recycled stories: same text, new names/numbers
        lang, tpl = RECYCLED[g % len(RECYCLED)]
        for _ in range(N_RECYCLED // 2):
            add(tpl.format(**_context(rng)), lang, "scam", "recycled_story", "hard", group=f"story_{g + 1}")

    rng.shuffle(rows)
    group_split = {}
    for i, r in enumerate(rows):
        r["appeal_id"] = f"APL-{i + 1:03d}"
        key = r["story_group"] or r["appeal_id"]
        group_split.setdefault(key, "test" if rng.random() < 0.3 else "dev")
        r["split"] = group_split[key]
        r["screenshot_file"] = ""
    shots = [r for r in rows if r["label"] == "genuine"][:6] + [r for r in rows if r["label"] == "scam"][:6]
    shot_dir = out / "appeal_screenshots"
    shot_dir.mkdir(exist_ok=True)
    for r in shots:
        r["screenshot_file"] = f"appeal_screenshots/{r['appeal_id']}.png"
        screenshot(r["text"], out / r["screenshot_file"], rng)
    cols = ["appeal_id", "text", "language", "label", "scam_type", "difficulty", "channel", "story_group",
            "matches_doc_id", "screenshot_file", "split"]
    with open(out / "appeals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows([{c: r[c] for c in cols} for r in sorted(rows, key=lambda r: r["appeal_id"])])
    n_s = sum(r["label"] == "scam" for r in rows)
    print(f"appeals:  {len(rows)}   genuine={len(rows) - n_s} scam={n_s}  screenshots={len(shots)}")


def screenshot(text, path, rng):
    """Generic chat-bubble screenshot (no real app branding)."""
    text = re.sub(r"[^\u0000-\uFFFF]", "", text).strip()
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except OSError:
        font = small = ImageFont.load_default()
    lines = [ln for para in text.split("\n") for ln in (textwrap.wrap(para, 34) or [""])]
    w, lh = 520, 30
    h = 120 + lh * len(lines) + 50
    bg = rng.choice([(236, 229, 221), (226, 232, 238), (238, 236, 228)])
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, 64], fill=(70, 90, 100))
    d.text((18, 20), "Family & friends group", font=small, fill=(255, 255, 255))
    d.rounded_rectangle([20, 90, w - 40, 90 + lh * len(lines) + 40], radius=14, fill=(255, 255, 255))
    for i, ln in enumerate(lines):
        d.text((38, 104 + i * lh), ln, font=font, fill=(25, 25, 25))
    d.text((w - 110, 100 + lh * len(lines) + 12), f"{rng.randint(6, 22)}:{rng.randint(0, 59):02d}", font=small, fill=(130, 130, 130))
    img.save(path, "PNG", optimize=True)
