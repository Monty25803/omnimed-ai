"""Prescription OCR: Tesseract text recognition + fuzzy RxNorm matching + sig parsing."""
from __future__ import annotations

import io
import random
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from rapidfuzz import fuzz, process

from .extract import parse_sig
from .ontology import Ontology

SAMPLE_LINES = [
    "Dr. A. Rao  MBBS MD   Reg 71-0001",
    "Pt: MRN-100002     27/09/2026",
    "Rx",
    "1) Tab Azithral 500 mg once daily x 5 days",
    "2) Tab Dolo 650 mg TDS for 3 days",
    "3) Tab Pan 40 mg BD before food for 7 days",
    "Review after 5 days",
]


def sample_image() -> bytes:
    """A synthetic, slightly distorted prescription image to exercise the OCR path."""
    rnd = random.Random(42)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    img = Image.new("L", (1200, 620), 250)
    d = ImageDraw.Draw(img)
    y = 30
    for line in SAMPLE_LINES:
        x = 40 + rnd.randint(-6, 6)
        for word in line.split(" "):
            d.text((x, y + rnd.randint(-2, 2)), word, fill=rnd.randint(10, 60), font=font)
            x += int(d.textlength(word + " ", font=font))
        y += 80
    img = img.rotate(-0.8, expand=False, fillcolor=250).filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _preprocess(img: Image.Image) -> Image.Image:
    img = ImageOps.grayscale(img)
    # Tesseract is markedly more accurate on ~2x upscaled camera/scan text; thresholding hurt on slanted script.
    if img.width < 2400:
        scale = min(2.0, 2400 / img.width) if img.width >= 1200 else 2400 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return ImageOps.autocontrast(img)


def ocr_lines(data: bytes) -> list[dict]:
    import pytesseract

    img = _preprocess(Image.open(io.BytesIO(data)))
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 6")
    lines: dict[tuple, dict] = {}
    for i, word in enumerate(d["text"]):
        if not word.strip():
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        ln = lines.setdefault(key, {"words": [], "conf": []})
        ln["words"].append(word)
        conf = float(d["conf"][i])
        if conf >= 0:
            ln["conf"].append(conf)
    return [{"text": " ".join(v["words"]), "ocr_confidence": round(sum(v["conf"]) / len(v["conf"]) / 100, 2) if v["conf"] else 0.0} for v in lines.values()]


FORM_RE = re.compile(r"\b(tab|tablet|cap|capsule|syp|syrup|inj|injection|inh|inhaler)\b\.?", re.I)


def parse_prescription_lines(lines: list[dict], onto: Ontology) -> list[dict]:
    names = {}
    for key, concepts in onto.phrases.items():
        for c in concepts:
            if c.system == "RxNorm" and len(key) >= 3:
                names[key] = c
    choices = list(names)
    items = []
    for ln in lines:
        text = ln["text"]
        cleaned = re.sub(r"^\s*\d+[).]\s*", "", text)
        cleaned = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", cleaned)
        form = FORM_RE.search(cleaned)
        body = FORM_RE.sub(" ", cleaned).strip()
        words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", body)
        best = None
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                cand = " ".join(words[i:i + n]).lower()
                m = process.extractOne(cand, choices, scorer=fuzz.ratio, score_cutoff=78)
                if m and (best is None or m[1] > best[1]):
                    best = (cand, m[1], m[0])
            if best and best[1] >= 95:
                break
        if not best:
            continue
        concept = names[best[2]]
        sig = parse_sig(body.replace(" x ", " for "))
        dur = re.search(r"\bx\s*(\d+)\s*(days?|weeks?)", body, re.I)
        if dur and not sig.duration_days:
            sig.duration_days = int(dur.group(1)) * (7 if dur.group(2).lower().startswith("week") else 1)
        strengths = [float(s) for s in (concept.attr("strengths_mg") or [])]
        strength_ok = sig.strength is None or not strengths or sig.strength in strengths
        conf = ln["ocr_confidence"] * (best[1] / 100) * (1.0 if strength_ok else 0.6) * (1.0 if sig.frequency else 0.85)
        items.append({
            "line": text, "matched_text": best[0], "brand_or_name": best[2], "rxcui": concept.code, "molecule": concept.display,
            "form": form.group(1).lower() if form else None, "strength_mg": sig.strength, "strength_valid": strength_ok,
            "frequency": sig.frequency, "duration_days": sig.duration_days,
            "match_score": round(best[1] / 100, 2), "ocr_confidence": ln["ocr_confidence"], "confidence": round(conf, 2),
            "needs_review": conf < 0.75 or not strength_ok or sig.frequency is None,
        })
    return items
