#!/usr/bin/env python3
"""Sanity-check the synthetic dataset. Exit code 1 if any check fails (safe to run in CI).

    python validate.py            # checks the dataset next to this script
    python validate.py --ocr 20   # also OCR 20 raster invoices (needs tesseract)
"""
import argparse
import csv
import difflib
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).parent
fails, passes = [], 0


def check(ok, msg):
    global passes
    if ok:
        passes += 1
    else:
        fails.append(msg)
        print(f"  FAIL  {msg}")


def read_csv(p):
    with open(ROOT / p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_jsonl(p):
    with open(ROOT / p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", type=int, default=0, help="OCR this many raster invoices as a legibility check")
    a = ap.parse_args()

    labels = read_csv("labels.csv")
    gt = {j["doc_id"]: j for j in map(json.loads, open(ROOT / "ground_truth.jsonl", encoding="utf-8"))}
    register_rows = read_csv("facility_register.csv")
    reg = {r["name"]: r for r in register_rows}
    L = {r["doc_id"]: r for r in labels}
    print(f"{len(labels)} invoices, {len(reg)} register entries")

    # ---- the register itself
    check(len(register_rows) == 25, "the register lists 25 facilities")
    check(len({r["register_code"] for r in register_rows}) == 25, "register codes are unique")
    check(len(reg) == 25, "register names are unique")
    check(all(r["official_email"].endswith(".test") for r in register_rows),
          "every official e-mail uses the reserved .test domain")
    check(len({r["official_email"] for r in register_rows}) == 25, "official e-mails are unique")
    check(all(r["source"] == "synthetic-register-v1" for r in register_rows), "register source is labelled")

    # ---- shape and files
    check(len(labels) >= 60, "AI-01: at least 60 invoices")
    check(set(L) == set(gt), "labels.csv and ground_truth.jsonl list the same documents")
    check({r["label"] for r in labels} == {"genuine", "tampered", "duplicate"}, "all three labels present")
    for r in labels:
        p = ROOT / "invoices" / r["filename"]
        check(p.exists() and p.suffix in (".pdf", ".jpg", ".png"), f"{r['doc_id']}: file exists with allowed type")
        check(p.exists() and p.stat().st_size <= 10 * 1024 * 1024, f"{r['doc_id']}: under 10 MB (PLT-06)")
    check(len({r["case_id"] for r in labels}) == len(labels), "one case_id per document")

    # ---- arithmetic and signals
    for d, g in gt.items():
        f, sig = g["fields"], set(g["expected_signals"])
        lines = f["line_items"]
        s = sum(i["amount"] for i in lines)
        math_ok = all(i["qty"] * i["unit_price"] == i["amount"] for i in lines)
        check(f["balance_due"] == f["total"] - f["deposit_paid"], f"{d}: balance = total - deposit")
        if g["label"] in ("genuine", "duplicate"):
            check(s == f["total"] and math_ok, f"{d}: genuine arithmetic is consistent")
        if "total_mismatch" in sig:
            check(s != f["total"], f"{d}: total_mismatch signal means lines do not sum to total")
        if "line_math_mismatch" in sig:
            check(not math_ok and s == f["total"], f"{d}: line_math_mismatch means qty*unit != amount only")
        if g["tamper_type"] in ("metadata_only", "image_splice"):
            check(s == f["total"] and math_ok, f"{d}: {g['tamper_type']} is arithmetically clean (hard case)")
        if g["label"] == "tampered":
            check("fields_original" in g and g["fields_original"] != f or g["tamper_type"] in
                  ("contact_mismatch", "lookalike_facility", "unknown_facility", "prompt_injection"),
                  f"{d}: tampered record keeps its original fields")

    # ---- facility register
    for d, g in gt.items():
        name, r = g["fields"]["facility"], L[d]
        best = max(difflib.SequenceMatcher(None, name.lower(), n.lower()).ratio() for n in reg)
        if g["tamper_type"] == "lookalike_facility":
            check(name not in reg and best >= 0.85, f"{d}: lookalike is close to, but not in, the register")
        elif g["tamper_type"] == "unknown_facility":
            check(name not in reg and best < 0.75, f"{d}: unknown facility is far from every register name")
        else:
            check(name in reg, f"{d}: facility is in the register")
            if name in reg and g["tamper_type"] != "contact_mismatch":
                check(g["fields"]["register_code"] == reg[name]["register_code"], f"{d}: register code matches")
        if g["tamper_type"] == "contact_mismatch":
            check(g["fields"]["letterhead_email"] != reg[name]["official_email"], f"{d}: letterhead email differs from register")

    # ---- uniqueness and hard negatives
    seen = defaultdict(list)
    for r in labels:
        if r["label"] != "duplicate":
            seen[(r["facility_shown"], r["invoice_number_shown"])].append(r["doc_id"])
    check(all(len(v) == 1 for v in seen.values()), "no invoice number repeats within a facility outside duplicates")
    hard = [r for r in labels if r["hard_negative"] == "true"]
    totals = defaultdict(set)
    for r in hard:
        totals[r["register_code"]].add(r["total_shown"])
    check(hard and all(len(v) == 1 for v in totals.values()), "hard negatives share one flat price per hospital")

    # ---- duplicates
    dist = defaultdict(list)
    for r in labels:
        if r["label"] != "duplicate":
            continue
        o = L.get(r["dup_of"])
        check(o is not None and o["label"] == "genuine", f"{r['doc_id']}: dup_of points to a genuine document")
        if not o:
            continue
        fp = lambda x: (x["facility_shown"], x["invoice_number_shown"], x["balance_shown"])
        check(fp(r) == fp(o), f"{r['doc_id']}: same facility, invoice number and balance as original")
        check(r["submitted_at"] > o["submitted_at"], f"{r['doc_id']}: submitted after its original")
        check(r["split"] == o["split"], f"{r['doc_id']}: same split as original")
        po, pr = ROOT / "invoices" / o["filename"], ROOT / "invoices" / r["filename"]
        if r["dup_variant"] == "exact_copy":
            check(sha(po) == sha(pr), f"{r['doc_id']}: exact copy has identical sha256")
        else:
            check(sha(po) != sha(pr), f"{r['doc_id']}: non-exact duplicate differs in sha256")
        dist[r["dup_variant"]].append(int(r["phash_dist_to_original"]))
    check(all(max(v) == 0 for k, v in dist.items() if k in ("exact_copy", "resaved_pdf")),
          "exact and re-saved duplicates have identical perceptual hashes")

    # ---- redaction targets present in the digital PDFs (so S4 can test AI-03 against them)
    miss = 0
    for r in labels:
        if r["capture"] == "digital_pdf":
            text = " ".join(PdfReader(ROOT / "invoices" / r["filename"]).pages[0].extract_text().split())
            for t in gt[r["doc_id"]]["must_redact"]:
                if t not in text:
                    miss += 1
    check(miss == 0, f"all must_redact strings appear in the PDF text ({miss} missing)")

    # ---- prompt-injection documents
    injected = [g for g in gt.values() if g["tamper_type"] == "prompt_injection"]
    check(len(injected) >= 1, "AI-01: at least one prompt-injection invoice")
    for g in injected:
        r = L[g["doc_id"]]
        text = " ".join(PdfReader(ROOT / "invoices" / r["filename"]).pages[0].extract_text().split())
        check(g["injected_text"] in text, f"{g['doc_id']}: injected text is in the PDF text layer")
        check(g["expected_signals"] == ["instruction_like_text"], f"{g['doc_id']}: expects instruction_like_text")
        check(g["fields"]["total"] == sum(i["amount"] for i in g["fields"]["line_items"]),
              f"{g['doc_id']}: the injected document still has honest arithmetic")

    # ---- every generated file is labelled, and every label has a file
    on_disk = {p.name for p in (ROOT / "invoices").iterdir()}
    check(on_disk == {r["filename"] for r in labels}, "labels.csv covers every file in invoices/ and nothing else")

    # ---- appeals
    ap_rows = read_jsonl("appeals.jsonl")
    check(len(ap_rows) >= 100, "AI-01: at least 100 appeals")
    check({r["label"] for r in ap_rows} == {"genuine", "scam"}, "appeals have both labels")
    check(len({r["text"] for r in ap_rows}) == len(ap_rows), "no identical appeal texts")
    check({r["language"] for r in ap_rows} >= {"en", "sw", "mixed"}, "appeals cover en, sw and mixed")
    for r in ap_rows:
        if r["matches_doc_id"]:
            g = gt.get(r["matches_doc_id"])
            check(bool(g) and g["label"] == "genuine" and g["fields"]["facility"] in r["text"],
                  f"{r['appeal_id']}: linked appeal names the invoice's hospital")
        if r["screenshot_file"]:
            check((ROOT / r["screenshot_file"]).exists(), f"{r['appeal_id']}: screenshot exists")

    # ---- optional OCR legibility
    if a.ocr:
        import pytesseract
        from PIL import Image
        digits = lambda s: re.sub(r"\D", "", s)
        rs = [r for r in labels if r["capture"] != "digital_pdf"][: a.ocr]
        hits = 0
        for r in rs:
            t = pytesseract.image_to_string(Image.open(ROOT / "invoices" / r["filename"]).convert("L"))
            hits += digits(gt[r["doc_id"]]["fields"]["invoice_number"])[-5:] in digits(t)
        print(f"  OCR (plain tesseract, no preprocessing): invoice number found in {hits}/{len(rs)} raster documents")

    # ---- summary
    print("\nlabel mix:", dict(Counter(r["label"] for r in labels)),
          "| capture:", dict(Counter(r["capture"] for r in labels)))
    print("tamper types:", dict(Counter(r["tamper_type"] for r in labels if r["tamper_type"])))
    print("perceptual-hash distance to original (0-64 bits) by duplicate variant:")
    for k, v in sorted(dist.items()):
        print(f"  {k:16s} min {min(v):2d}  median {sorted(v)[len(v) // 2]:2d}  max {max(v):2d}")
    print(f"\n{passes} checks passed, {len(fails)} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
