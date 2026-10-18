"""Rendering for synthetic invoices: PDF layouts, overlay edits, metadata, photo/scan effects.

A record ("rec") is a plain dict (see generate.py). The renderer only reads
what is *shown* on the document. For overlay-style tampering it also receives
the original record: the original text is drawn first, then covered by a white
box, then the edited text is drawn in a different font - the same trace a real
PDF editor leaves behind (hidden text under a rectangle, mismatched fonts).
"""
import math
import os
from datetime import datetime

import numpy as np
from PIL import Image, ImageFilter
from pdf2image import convert_from_path
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

W, H = A4
DPI = 150
PX = DPI / 72.0
GREY = (0.35, 0.35, 0.35)
ALT_FONT = {"Helvetica": "Times-Roman", "Helvetica-Bold": "Times-Bold",
            "Courier": "Helvetica", "Courier-Bold": "Helvetica-Bold"}


# --------------------------------------------------------------------------- text helpers
def field_texts(rec):
    st, lh = rec["style"], rec["letterhead"]
    n = lambda v: st["num_fmt"].format(v)
    T = {
        "hospital_name": lh["name"], "register_code": lh["register_code"],
        "letterhead_phone": lh["phone"], "letterhead_email": lh["email"],
        "invoice_number": rec["invoice_number"],
        "invoice_date": rec["invoice_date"].strftime(st["date_fmt"]),
        "patient_ref": rec["patient_ref"],
        "total": n(rec["total_shown"]), "deposit": n(rec["deposit_shown"]),
        "balance": n(rec["balance_shown"]),
    }
    for i, it in enumerate(rec["items"]):
        T[f"line{i}_qty"] = str(it["qty"])
        T[f"line{i}_unit"] = n(it["unit_price"])
        T[f"line{i}_amount"] = n(it["amount"])
    return T


class Pen:
    """Thin wrapper over a reportlab canvas that records where each field was drawn."""

    def __init__(self, c, T, OT=None, overlay=()):
        self.c, self.T, self.OT, self.overlay = c, T, OT or {}, set(overlay)
        self.pos = {}

    @staticmethod
    def _x0(x, w, align):
        return x - w if align == "r" else (x - w / 2 if align == "c" else x)

    def _draw(self, text, x, y, font, size, align, color):
        self.c.setFillColorRGB(*color)
        self.c.setFont(font, size)
        w = stringWidth(text, font, size)
        x0 = self._x0(x, w, align)
        self.c.drawString(x0, y, text)
        return x0, x0 + w

    def label(self, text, x, y, font="Helvetica", size=9, color=(0, 0, 0), align="l"):
        self._draw(text, x, y, font, size, align, color)

    def put(self, field, x, y, font="Helvetica", size=9, align="l", color=(0, 0, 0), prefix=""):
        text = prefix + self.T[field]
        if field in self.overlay:
            otext = prefix + self.OT[field]
            ox0, ox1 = self._draw(otext, x, y, font, size, align, color)   # stays in the content stream
            font2 = ALT_FONT.get(font, "Helvetica")
            w2 = stringWidth(text, font2, size)
            nx0 = self._x0(x, w2, align)
            left, right = min(ox0, nx0) - 2, max(ox1, nx0 + w2) + 2
            self.c.setFillColorRGB(1, 1, 1)
            self.c.rect(left, y - 2.5, right - left, size + 3.5, stroke=0, fill=1)
            x0, x1 = self._draw(text, x, y + 0.6, font2, size, align, color)
            self.pos[field] = (x0, y - 2, x1, y + size)
        else:
            x0, x1 = self._draw(text, x, y, font, size, align, color)
            self.pos[field] = (x0, y - 2, x1, y + size)


# --------------------------------------------------------------------------- shared blocks
def title_of(rec):
    return "IN-PATIENT INVOICE" if rec["doc_type"] == "invoice" else "COST ESTIMATE (PRO-FORMA)"


def patient_rows(rec):
    p = rec["patient"]
    rows = [("Patient", p["name"])]
    if p["id_no"]:
        rows.append(("ID No.", p["id_no"]))
    if p["sha_no"]:
        rows.append(("SHA No.", p["sha_no"]))
    if p["phone"]:
        rows.append(("Tel", p["phone"]))
    if p["guarantor"]:
        rows.append(("Guarantor", f'{p["guarantor"]}  {p["guarantor_phone"]}'))
    return rows


def draw_table(pen, rec, x0, y, mode, col, widths=(24, 270, 40, 85, 96), rowh=15):
    c = pen.c
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    font, bold = ("Courier", "Courier-Bold") if mode == "mono" else ("Helvetica", "Helvetica-Bold")
    size = 7.8 if mode == "mono" else 9
    heads = ["#", "Description", "Qty", "Unit price", "Amount"]
    cur = rec["style"]["cur"]
    heads[3], heads[4] = f"Unit ({cur})", f"Amount ({cur})"
    n = len(rec["items"])
    if mode == "grid":
        c.setFillColorRGB(*col)
        c.rect(xs[0], y - rowh + 3, xs[-1] - xs[0], rowh, stroke=0, fill=1)
    hc = (1, 1, 1) if mode == "grid" else (0, 0, 0)
    pen.label(heads[0], xs[0] + 4, y - 8, bold, size, hc)
    pen.label(heads[1], xs[1] + 4, y - 8, bold, size, hc)
    for k in (2, 3, 4):
        pen.label(heads[k], xs[k + 1] - 5, y - 8, bold, size, hc, "r")
    if mode == "lines":
        for i in range(n):
            if i % 2 == 0:
                c.setFillColorRGB(0.95, 0.95, 0.97)
                c.rect(xs[0], y - rowh * (i + 2) + 3, xs[-1] - xs[0], rowh, stroke=0, fill=1)
        c.setStrokeColorRGB(*col)
        c.setLineWidth(1)
        c.line(xs[0], y - rowh + 3, xs[-1], y - rowh + 3)
    if mode == "mono":
        c.setStrokeColorRGB(0, 0, 0)
        c.setDash(2, 2)
        c.line(xs[0], y + 9, xs[-1], y + 9)
        c.line(xs[0], y - rowh + 3, xs[-1], y - rowh + 3)
        c.setDash()
    for i, it in enumerate(rec["items"]):
        ry = y - rowh * (i + 1) - 8
        pen.label(str(i + 1), xs[0] + 4, ry, font, size)
        pen.label(it["description"], xs[1] + 4, ry, font, size)
        pen.put(f"line{i}_qty", xs[3] - 5, ry, font, size, "r")
        pen.put(f"line{i}_unit", xs[4] - 5, ry, font, size, "r")
        pen.put(f"line{i}_amount", xs[5] - 5, ry, font, size, "r")
    bottom = y - rowh * (n + 1) + 3
    if mode == "grid":
        c.setStrokeColorRGB(0.6, 0.6, 0.6)
        c.setLineWidth(0.5)
        c.rect(xs[0], bottom, xs[-1] - xs[0], (y + 3) - bottom, stroke=1, fill=0)
        for i in range(1, n + 1):
            c.line(xs[0], y - rowh * i + 3, xs[-1], y - rowh * i + 3)
        for k in (1, 3, 4):
            c.line(xs[k], bottom, xs[k], y + 3)
    elif mode == "lines":
        c.setStrokeColorRGB(*col)
        c.line(xs[0], bottom, xs[-1], bottom)
    else:
        c.setDash(2, 2)
        c.line(xs[0], bottom, xs[-1], bottom)
        c.setDash()
    return bottom, xs[-1]


def draw_totals(pen, rec, y, right, font="Helvetica", size=10, col=(0, 0, 0)):
    cur = rec["style"]["cur"]
    bold = font + "-Bold"
    est = rec["doc_type"] != "invoice"
    pen.label(f"Total ({cur})" if not est else f"Estimated total ({cur})", right - 130, y, bold, size, col, "r")
    pen.put("total", right - 5, y, bold, size, "r", col)
    if not est:
        pen.label(f"Deposit paid ({cur})", right - 130, y - 16, font, size, (0, 0, 0), "r")
        pen.put("deposit", right - 5, y - 16, font, size, "r")
        pen.label(f"Balance due ({cur})", right - 130, y - 34, bold, size + 1, col, "r")
        pen.put("balance", right - 5, y - 34, bold, size + 1, "r", col)
        return y - 34
    return y


def draw_footer(pen, rec, y, font="Helvetica", size=8):
    lh = rec["letterhead"]
    if rec["doc_type"] == "invoice":
        pen.label(f"Payment is due on discharge. Pay at the billing desk, Mon-Sat 8am-6pm. Cheques payable to {lh['name']}.",
                  40, y, font, size, GREY)
    else:
        pen.label("This estimate is not a final bill and is valid for 14 days from the date shown.", 40, y, font, size, GREY)
    pen.label(f"Prepared by: {rec['cashier']}", 40, y - 14, font, size, GREY)
    pen.label("Signature: ____________________", 330, y - 14, font, size, GREY)


# --------------------------------------------------------------------------- templates
def classic(pen, rec):
    c, st, lh = pen.c, rec["style"], rec["letterhead"]
    col = st["color"]
    c.setFillColorRGB(*col)
    c.rect(0, H - 72, W, 72, stroke=0, fill=1)
    pen.put("hospital_name", 40, H - 34, "Helvetica-Bold", 17, color=(1, 1, 1))
    pen.put("register_code", 40, H - 50, "Helvetica", 8.5, color=(1, 1, 1), prefix="Facility reg. no. ")
    pen.label(lh["address"], 40, H - 63, "Helvetica", 8.5, (1, 1, 1))
    pen.put("letterhead_phone", 40, H - 90, "Helvetica", 8.5, prefix="Tel: ")
    pen.put("letterhead_email", 200, H - 90, "Helvetica", 8.5, prefix="Email: ")
    pen.label(title_of(rec), 40, H - 120, "Helvetica-Bold", 13, col)
    y = H - 142
    for lab, val in patient_rows(rec):
        pen.label(lab + ":", 40, y, "Helvetica-Bold", 8.5)
        pen.label(val, 100, y, "Helvetica", 9)
        y -= 14
    right = [("Invoice No.", "invoice_number"), ("Date", "invoice_date"), ("Patient ref.", "patient_ref")]
    if rec["doc_type"] != "invoice":
        right[0], right[1] = ("Estimate No.", "invoice_number"), ("Estimate date", "invoice_date")
    ry = H - 142
    for lab, f in right:
        pen.label(lab + ":", 360, ry, "Helvetica-Bold", 8.5)
        pen.put(f, 440, ry, "Helvetica", 9)
        ry -= 14
    y = min(y, ry) - 4
    pen.label("Diagnosis:", 40, y, "Helvetica-Bold", 8.5)
    pen.label(rec["diagnosis"], 100, y, "Helvetica", 9)
    pen.label("Procedure:", 40, y - 14, "Helvetica-Bold", 8.5)
    pen.label(rec["procedure"], 100, y - 14, "Helvetica", 9)
    bottom, right_x = draw_table(pen, rec, 40, y - 38, "grid", col)
    last = draw_totals(pen, rec, bottom - 22, right_x, "Helvetica", 10, col)
    draw_footer(pen, rec, last - 50)


def modern(pen, rec):
    c, st, lh = pen.c, rec["style"], rec["letterhead"]
    col = st["color"]
    c.setFillColorRGB(*col)
    c.rect(0, 0, 14, H, stroke=0, fill=1)
    pen.put("hospital_name", 40, H - 48, "Helvetica-Bold", 15, color=col)
    pen.put("register_code", 40, H - 62, "Helvetica", 8, color=GREY, prefix="Reg. ")
    pen.label(lh["address"], 40, H - 74, "Helvetica", 8, GREY)
    pen.put("letterhead_phone", W - 40, H - 48, "Helvetica", 8.5, "r", GREY)
    pen.put("letterhead_email", W - 40, H - 60, "Helvetica", 8.5, "r", GREY)
    pen.label("INVOICE" if rec["doc_type"] == "invoice" else "ESTIMATE", 40, H - 118, "Helvetica-Bold", 26, col)
    c.setStrokeColorRGB(*col)
    c.setLineWidth(1.2)
    c.line(40, H - 128, W - 40, H - 128)
    pen.label("BILLED TO", 40, H - 148, "Helvetica-Bold", 7.5, GREY)
    y = H - 162
    for lab, val in patient_rows(rec):
        pen.label(val if lab == "Patient" else f"{lab}: {val}", 40, y, "Helvetica-Bold" if lab == "Patient" else "Helvetica", 9.5)
        y -= 13
    pen.label("DETAILS", 330, H - 148, "Helvetica-Bold", 7.5, GREY)
    est = rec["doc_type"] != "invoice"
    rows = [("Estimate no." if est else "Invoice no.", "invoice_number"), ("Date", "invoice_date"), ("Patient ref.", "patient_ref")]
    ry = H - 162
    for lab, f in rows:
        pen.label(lab, 330, ry, "Helvetica", 8.5, GREY)
        pen.put(f, W - 40, ry, "Helvetica-Bold", 9.5, "r")
        ry -= 13
    y = min(y, ry) - 6
    pen.label(f"Diagnosis: {rec['diagnosis']}", 40, y, "Helvetica", 9, GREY)
    pen.label(f"Procedure: {rec['procedure']}", 40, y - 13, "Helvetica", 9, GREY)
    bottom, right_x = draw_table(pen, rec, 40, y - 38, "lines", col)
    last = draw_totals(pen, rec, bottom - 22, right_x, "Helvetica", 10, col)
    draw_footer(pen, rec, last - 50)


def compact(pen, rec):
    c, lh = pen.c, rec["letterhead"]
    cx = W / 2
    pen.put("hospital_name", cx, H - 40, "Courier-Bold", 12, "c")
    pen.label(lh["address"], cx, H - 53, "Courier", 8.5, align="c")
    pen.put("letterhead_phone", cx, H - 65, "Courier", 8.5, "c", prefix="TEL ")
    pen.put("register_code", cx, H - 77, "Courier", 8.5, "c", prefix="REG NO ")
    c.setDash(2, 2)
    c.line(40, H - 86, W - 40, H - 86)
    c.setDash()
    pen.label(title_of(rec), cx, H - 102, "Courier-Bold", 10, align="c")
    est = rec["doc_type"] != "invoice"
    y = H - 122
    pen.label("NO:" if not est else "EST NO:", 40, y, "Courier-Bold", 8.5)
    pen.put("invoice_number", 100, y, "Courier", 8.5)
    pen.label("DATE:", 330, y, "Courier-Bold", 8.5)
    pen.put("invoice_date", 375, y, "Courier", 8.5)
    y -= 12
    pen.label("REF:", 40, y, "Courier-Bold", 8.5)
    pen.put("patient_ref", 100, y, "Courier", 8.5)
    y -= 12
    for lab, val in patient_rows(rec):
        pen.label(lab.upper() + ":", 40, y, "Courier-Bold", 8.5)
        pen.label(val, 100, y, "Courier", 8.5)
        y -= 12
    pen.label("DX:", 40, y, "Courier-Bold", 8.5)
    pen.label(rec["diagnosis"], 100, y, "Courier", 8.5)
    pen.label("PROC:", 40, y - 12, "Courier-Bold", 8.5)
    pen.label(rec["procedure"], 100, y - 12, "Courier", 8.5)
    bottom, right_x = draw_table(pen, rec, 40, y - 36, "mono", (0, 0, 0), widths=(20, 300, 30, 80, 85), rowh=13)
    last = draw_totals(pen, rec, bottom - 20, right_x, "Courier", 9, (0, 0, 0))
    pen.label("** COMPUTER GENERATED - NO SIGNATURE REQUIRED **", cx, last - 40, "Courier", 8, GREY, "c")
    pen.label(f"Served by: {rec['cashier']}", cx, last - 52, "Courier", 8, GREY, "c")


TEMPLATES = {"classic": classic, "modern": modern, "compact": compact}


def render_pdf(path, rec, template=None, orig=None, overlay=()):
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pen = Pen(c, field_texts(rec), field_texts(orig) if orig else None, overlay)
    TEMPLATES[template or rec["style"]["template"]](pen, rec)
    c.showPage()
    c.save()
    return pen.pos


# --------------------------------------------------------------------------- PDF metadata
def pdf_date(dt):
    return dt.strftime("D:%Y%m%d%H%M%S") + "+03'00'"


def write_meta(path, rec, producer, created, creator=None, modified=None, edit=None):
    """Rewrite document info. `edit=(producer, moddate)` appends an incremental update,
    leaving a second %%EOF in the file like a re-saved edit would."""
    path = str(path)
    w = PdfWriter(clone_from=path)
    w.add_metadata({
        "/Title": f"{rec['doc_type'].title()} {rec['invoice_number']}",
        "/Author": rec["letterhead"]["name"], "/Subject": "", "/Keywords": "",
        "/Creator": creator or producer, "/Producer": producer,
        "/CreationDate": pdf_date(created), "/ModDate": pdf_date(modified or created),
    })
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        w.write(f)
    os.replace(tmp, path)
    if edit:
        w2 = PdfWriter(path, incremental=True)
        w2.add_metadata({"/Producer": edit[0], "/ModDate": pdf_date(edit[1])})
        w2.write(path)


# --------------------------------------------------------------------------- raster effects
def rasterize(path):
    return convert_from_path(str(path), dpi=DPI, first_page=1, last_page=1)[0].convert("RGB")


def bbox_px(b, pad=4):
    x0, y0, x1, y1 = b
    return (int(x0 * PX) - pad, int((H - y1) * PX) - pad, int(x1 * PX) + pad, int((H - y0) * PX) + pad)


def degrade(img, np_rng, noise, blur, light, angle, gamma):
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    a = np.asarray(img, dtype=np.float32) / 255.0
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    proj = (xx / w - 0.5) * math.cos(angle) + (yy / h - 0.5) * math.sin(angle)
    shade = 1.0 - light * (proj + 0.5)
    vign = 1.0 - 0.7 * (((xx / w - 0.5) ** 2) + ((yy / h - 0.5) ** 2))
    a = np.clip(a * (shade * vign)[..., None], 0, 1) ** gamma
    a = a + np_rng.normal(0, noise / 255.0, (h, w, 1)).astype(np.float32) \
          + np_rng.normal(0, noise / 600.0, (h, w, 3)).astype(np.float32)
    return np.clip(a * 255, 0, 255).astype(np.uint8)


def geometric(img, rng, kind):
    if kind == "photo":
        m = int(img.width * rng.uniform(0.02, 0.05))
        bg = rng.choice([(58, 52, 47), (88, 76, 64), (40, 44, 52), (110, 104, 96)])
        cv = Image.new("RGB", (img.width + 2 * m, img.height + 2 * m), bg)
        cv.paste(img, (m, m))
        out = cv.rotate(rng.uniform(1.0, 3.5) * rng.choice([-1, 1]), resample=Image.BICUBIC, expand=True, fillcolor=bg)
        return out.resize((1300, int(out.height * 1300 / out.width)), Image.LANCZOS)
    ang = rng.uniform(0.2, 0.8) * rng.choice([-1, 1])
    out = img.rotate(ang, resample=Image.BICUBIC, expand=True, fillcolor=(246, 246, 244))
    return out.convert("L").convert("RGB") if rng.random() < 0.5 else out


def make_raster(base, rng, np_rng, kind="photo", splice=None):
    """Phone photo or flatbed scan of `base`. `splice=(edited_img, [bbox_px...])` pastes
    regions from a separately degraded render (lower noise), the signature of a pixel edit."""
    if kind == "photo":
        P = dict(noise=rng.uniform(4, 9), blur=rng.uniform(0.5, 1.1), light=rng.uniform(0.05, 0.22),
                 angle=rng.uniform(0, 6.28), gamma=rng.uniform(0.9, 1.15))
    else:
        P = dict(noise=rng.uniform(1.5, 3.5), blur=rng.uniform(0.2, 0.5), light=rng.uniform(0.0, 0.05),
                 angle=rng.uniform(0, 6.28), gamma=1.0)
    main = degrade(base, np_rng, **P)
    if splice:
        edited, boxes = splice
        alt = degrade(edited, np_rng, **{**P, "noise": P["noise"] * 0.25, "blur": P["blur"] * 0.3})
        hh, ww = main.shape[:2]
        for x0, y0, x1, y1 in boxes:
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(ww, x1), min(hh, y1)
            main[y0:y1, x0:x1] = alt[y0:y1, x0:x1]
    return geometric(Image.fromarray(main), rng, kind)


def screenshot_variant(img, rng):
    w, h = img.size
    l, t = int(w * rng.uniform(0, 0.06)), int(h * rng.uniform(0, 0.05))
    r, b = int(w * rng.uniform(0, 0.06)), int(h * rng.uniform(0, 0.05))
    out = img.crop((l, t, w - r, h - b))
    s = rng.uniform(0.7, 1.0)
    return out.resize((int(out.width * s), int(out.height * s)), Image.LANCZOS)


def save_raster(img, path, kind, rng):
    if str(path).endswith(".jpg"):
        img.save(path, "JPEG", quality=rng.randint(58, 84) if kind == "photo" else 90)
    else:
        img.save(path, "PNG", optimize=True)
