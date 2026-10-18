# TibaDirect synthetic dataset (AI-01)

Fictional hospital invoices and fundraising appeals for building and testing the AI verification
engine (OCR, extraction, redaction, tamper and duplicate checks, facility match, risk score, appeal checker).

**Everything here is invented.** No real patients, hospitals, documents or numbers. Hospital e-mail
domains use the reserved `.example` TLD. Phone numbers sit in an unassigned-looking `0700 00x` block and
8-digit ID numbers are random, so a chance collision with a real number is possible, never intended.

```bash
pip install -r requirements.txt
python generate.py --out . --seed 42      # rebuilds everything, byte-for-byte reproducible
python validate.py --ocr 20               # 700+ consistency checks + an OCR legibility spot-check
```

`--scale 2` doubles every count. Poppler (`pdftoppm`) must be installed for PDF rasterising
(`apt install poppler-utils`, already present in the Docker images). `--ocr` also needs `tesseract`.

## What is in the box

| File | Contents |
| --- | --- |
| `invoices/INV-###.{pdf,jpg,png}` | 90 documents: 36 genuine, 30 tampered, 24 duplicates. File names carry no label. |
| `labels.csv` | One row per document: label, tamper type, duplicate link, split, expected signals, perceptual hash. |
| `ground_truth.jsonl` | Fields exactly as shown on the document, the original fields for tampered ones, and `must_redact` strings. |
| `hospital_register.csv/.json` | 8 fictional hospitals: official phone and e-mail, PayPal sandbox merchant ids (hospitals 1 and 2), settlement adapter. Seed data for S1, reference list for AI-06. |
| `appeals.csv`, `appeal_screenshots/` | 100 appeals (50 genuine, 50 scam) in English, Swahili and code-switched text; 12 generic chat screenshots. |
| `generate.py`, `render.py`, `catalog.py`, `hospitals.py`, `appeals.py` | The generator. Change `TAMPER_COUNTS` / `DUP_COUNTS` in `generate.py` to reshape the dataset. |
| `validate.py` | Dataset self-checks, safe to run in CI. |

## Documents

Eight hospitals, three layouts (classic, modern, compact monospace), different number formats, date
formats, invoice-number patterns and phone formats. Five are pre-bill estimates rather than invoices. Captured as digital
PDF (43), phone photo (36, rotated, shadowed, noisy, JPEG), flatbed scan (7) or screenshot (4).
The compact layout is 7.8 pt monospace, which is small on purpose.

**Tamper types** (`tamper_type` in `labels.csv`)

| Type | n | What was done | What a good checker can see |
| --- | --- | --- | --- |
| `total_mismatch_only` | 4 | Total inflated, drawn natively, clean metadata | Lines do not sum to the total |
| `overlay_total` | 5 | Total and balance covered by a white box, retyped in another font | Arithmetic, hidden text under the box, font change, editor metadata (PDF) |
| `overlay_line` | 5 | One line amount covered and retyped, total updated to match | `qty x unit != amount`, plus the overlay traces |
| `metadata_only` | 4 | Values changed consistently, drawn natively, PDF only | Only the metadata: editor producer, ModDate long after CreationDate, sometimes a second `%%EOF` |
| `date_altered` | 2 | Old invoice date covered and replaced with a recent one | Overlay traces |
| `contact_mismatch` | 3 | Letterhead phone and e-mail differ from the register | Register comparison (SEC-06) |
| `lookalike_facility` | 2 | Name one typo away from a real register entry, wrong register code | Fuzzy match that is not exact |
| `unknown_facility` | 2 | Facility absent from the register | No match |
| `image_splice` | 3 | Photo with line, total and balance patched in from a cleaner render, arithmetic consistent | Only noise and sharpness inconsistency |

**Duplicate variants** (`dup_variant`, `dup_of` points at a genuine original, always submitted later)

| Variant | n | Detectable by |
| --- | --- | --- |
| `exact_copy` | 4 | sha256 |
| `resaved_pdf` | 5 | Same page content, new metadata: perceptual hash or text fingerprint |
| `phone_photo` | 6 | Re-photographed original: perceptual hash (distance 6 to 22 of 64 bits, so threshold tuning matters) |
| `screenshot_crop` | 4 | Cropped and rescaled: hash distance 12 to 14, or fingerprint |
| `reformatted` | 5 | Same facility, invoice number and amount in a different layout (hash distance 20 to 32): only the fingerprint works |

**Hard negatives:** 6 genuine delivery-package invoices have the same flat total at the same hospital
(`hard_negative=true`). An amount-only duplicate rule will wrongly flag them; facility plus invoice number plus amount will not.

## Signals

`expected_signals` lists what a perfect checker would raise for that file. PDF-only traces are dropped
for photos and scans. Vocabulary: `total_mismatch`, `line_math_mismatch`, `overlay_box`,
`hidden_text_under_overlay`, `font_inconsistent`, `metadata_edited`, `incremental_update`,
`metadata_generic_producer`, `noise_inconsistency`, `contact_mismatch`, `facility_unknown`,
`facility_fuzzy_match`, `duplicate_exact_hash`, `duplicate_perceptual`, `duplicate_fingerprint`.
Per-check precision and recall (AI-10) can be computed by comparing these against your detectors.

## How to use it properly

- **Splits:** each document has `split` = `dev` (70%) or `test` (30%). Tune on `dev`, report on `test`. A duplicate always shares its original's split.
- **Order matters for duplicates:** process documents in `submitted_at` order and index each one after checking it. Only `label=duplicate` files should be flagged.
- **Extraction accuracy (AI-02):** compare against `fields` in `ground_truth.jsonl` for `genuine` documents. `balance_due` is the amount a case would raise.
- **Redaction (AI-03, with S4):** after redaction, none of a document's `must_redact` strings may remain.
- **Facility check (AI-06):** `hospital_register.csv` has the official contacts. `aliases` is `|` separated.
- **Appeals (AI-08):** `difficulty=hard` rows are calm scams that name a real register hospital and genuine appeals that mention a paybill. `story_group` marks recycled scam stories, which should be caught by similarity. `matches_doc_id` links 15 genuine appeals to an invoice. Recycled stories share a split.

## Known limits (say these in the README and the Devpost write-up)

- Synthetic images are cleaner than real Kenyan hospital paperwork: no stamps, handwriting, or multi-page bills. Reported accuracy is an upper bound.
- Photos have no EXIF data. PDF metadata signals therefore exist only for digital PDFs.
- Plain Tesseract finds the invoice number in roughly 3 of 4 raster documents with no preprocessing, which leaves room for AI-02 to show improvement.
- The Swahili and code-switched appeal text has not been reviewed by a native speaker (UX-06 needs this).
- Prices are plausible ranges, not real tariffs.
