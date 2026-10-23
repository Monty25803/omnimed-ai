"""Plain-language clinical translator (English and Hindi).

Deterministic: jargon is replaced from a reviewed glossary and lab values are
explained from reference ranges. Terms outside the glossary are left as-is and
reported, never invented.
"""
from __future__ import annotations

import re

GLOSSARY: dict[str, dict[str, str]] = {
    "hypertension": {"en": "high blood pressure", "hi": "उच्च रक्तचाप"},
    "hyperkalemia": {"en": "too much potassium in the blood", "hi": "खून में पोटैशियम की अधिकता"},
    "hyperkalaemia": {"en": "too much potassium in the blood", "hi": "खून में पोटैशियम की अधिकता"},
    "hyponatremia": {"en": "too little sodium (salt) in the blood", "hi": "खून में सोडियम (नमक) की कमी"},
    "type 2 diabetes mellitus": {"en": "type 2 diabetes (high blood sugar)", "hi": "टाइप 2 मधुमेह (शुगर)"},
    "diabetes mellitus": {"en": "diabetes (high blood sugar)", "hi": "मधुमेह (शुगर)"},
    "chronic kidney disease": {"en": "long-term reduced kidney function", "hi": "लंबे समय से कमज़ोर गुर्दे"},
    "renal": {"en": "kidney", "hi": "गुर्दे से संबंधित"},
    "egfr": {"en": "kidney filtering score (eGFR)", "hi": "गुर्दे की छानने की क्षमता (eGFR)"},
    "creatinine": {"en": "creatinine (a kidney waste marker)", "hi": "क्रिएटिनिन (गुर्दे का एक संकेतक)"},
    "myocardial infarction": {"en": "heart attack", "hi": "दिल का दौरा"},
    "heart failure": {"en": "the heart is not pumping as strongly as it should", "hi": "दिल ठीक से पंप नहीं कर पा रहा"},
    "atrial fibrillation": {"en": "an irregular heartbeat", "hi": "अनियमित धड़कन"},
    "pneumonia": {"en": "a lung infection", "hi": "फेफड़ों का संक्रमण (निमोनिया)"},
    "sepsis": {"en": "a serious body-wide reaction to infection", "hi": "संक्रमण से पूरे शरीर पर गंभीर असर (सेप्सिस)"},
    "dyspnea": {"en": "shortness of breath", "hi": "सांस फूलना"},
    "dyspnoea": {"en": "shortness of breath", "hi": "सांस फूलना"},
    "edema": {"en": "swelling from fluid build-up", "hi": "पानी जमा होने से सूजन"},
    "oedema": {"en": "swelling from fluid build-up", "hi": "पानी जमा होने से सूजन"},
    "tachycardia": {"en": "a fast heart rate", "hi": "तेज़ धड़कन"},
    "bradycardia": {"en": "a slow heart rate", "hi": "धीमी धड़कन"},
    "febrile": {"en": "having a fever", "hi": "बुखार होना"},
    "afebrile": {"en": "no fever", "hi": "बुखार नहीं"},
    "benign": {"en": "not cancer", "hi": "कैंसर नहीं"},
    "malignant": {"en": "cancerous", "hi": "कैंसरयुक्त"},
    "nodule": {"en": "a small lump or spot", "hi": "छोटी गांठ"},
    "lesion": {"en": "an area of abnormal tissue", "hi": "असामान्य ऊतक का हिस्सा"},
    "anemia": {"en": "low red blood cells (low hemoglobin)", "hi": "खून की कमी (कम हीमोग्लोबिन)"},
    "anaemia": {"en": "low red blood cells (low hemoglobin)", "hi": "खून की कमी (कम हीमोग्लोबिन)"},
    "hba1c": {"en": "average blood sugar over 3 months (HbA1c)", "hi": "पिछले 3 महीनों की औसत शुगर (HbA1c)"},
    "prn": {"en": "only when needed", "hi": "केवल ज़रूरत पड़ने पर"},
    "bd": {"en": "twice a day", "hi": "दिन में दो बार"},
    "od": {"en": "once a day", "hi": "दिन में एक बार"},
    "tds": {"en": "three times a day", "hi": "दिन में तीन बार"},
    "contraindicated": {"en": "should not be used", "hi": "इस्तेमाल नहीं करना चाहिए"},
    "prognosis": {"en": "the likely course of the illness", "hi": "बीमारी का संभावित परिणाम"},
    "idiopathic": {"en": "of unknown cause", "hi": "अज्ञात कारण से"},
    "acute": {"en": "sudden or short-term", "hi": "अचानक या कम समय का"},
    "chronic": {"en": "long-term", "hi": "लंबे समय का"},
    "potassium-sparing diuretic": {"en": "water tablet that keeps potassium in the body", "hi": "पेशाब की दवा जो पोटैशियम बनाए रखती है"},
    "diuretic": {"en": "water tablet", "hi": "पेशाब बढ़ाने वाली दवा"},
    "antihypertensive": {"en": "blood pressure medicine", "hi": "रक्तचाप की दवा"},
    "ecg": {"en": "heart tracing (ECG)", "hi": "दिल की जांच (ECG)"},
}

LEVEL_TEXT = {
    "en": {"N": "This is within the normal range.", "H": "This is higher than normal.", "L": "This is lower than normal.",
           "HH": "This is much higher than normal and needs urgent attention from your doctor.",
           "LL": "This is much lower than normal and needs urgent attention from your doctor.",
           "value": "Your {name} is {value} {unit}.", "range": "The usual range is {low} to {high}.",
           "disclaimer": "This explanation is for understanding only. Please discuss your results with your doctor."},
    "hi": {"N": "यह सामान्य सीमा में है।", "H": "यह सामान्य से अधिक है।", "L": "यह सामान्य से कम है।",
           "HH": "यह सामान्य से बहुत अधिक है, कृपया तुरंत अपने डॉक्टर से मिलें।",
           "LL": "यह सामान्य से बहुत कम है, कृपया तुरंत अपने डॉक्टर से मिलें।",
           "value": "आपका {name} {value} {unit} है।", "range": "सामान्य सीमा {low} से {high} है।",
           "disclaimer": "यह जानकारी केवल समझने के लिए है। कृपया अपनी रिपोर्ट के बारे में अपने डॉक्टर से बात करें।"},
}

LAB_NAMES_HI = {"Potassium": "पोटैशियम", "Sodium": "सोडियम", "Creatinine": "क्रिएटिनिन", "eGFR": "eGFR (गुर्दे की क्षमता)",
                "Glucose": "शुगर (ग्लूकोज़)", "Hemoglobin": "हीमोग्लोबिन", "Hemoglobin A1c": "HbA1c (3 महीने की औसत शुगर)",
                "WBC count": "सफेद रक्त कोशिकाएं (WBC)", "Platelet count": "प्लेटलेट्स", "C reactive protein": "CRP (सूजन का संकेतक)"}

SUPPORTED = ("en", "hi")


def translate_text(text: str, lang: str = "en") -> dict:
    if lang not in SUPPORTED:
        raise ValueError(f"language must be one of {SUPPORTED}")
    replaced = []
    out = text
    for term in sorted(GLOSSARY, key=len, reverse=True):
        pat = re.compile(r"\b" + re.escape(term) + r"\b", re.I)
        if pat.search(out):
            plain = GLOSSARY[term][lang]
            out = pat.sub(lambda m, p=plain: f"{m.group(0)} ({p})" if lang == "en" else p, out)
            replaced.append({"term": term, "plain": plain})
    unknown = sorted({w for w in re.findall(r"\b[a-z]{6,}(?:itis|osis|emia|aemia|pathy|oma)\b", text.lower()) if w not in GLOSSARY})
    return {"lang": lang, "text": out, "replaced": replaced, "unexplained_terms": unknown}


def explain_results(results: list[dict], lang: str = "en") -> dict:
    if lang not in SUPPORTED:
        raise ValueError(f"language must be one of {SUPPORTED}")
    t = LEVEL_TEXT[lang]
    lines = []
    for r in results:
        name = LAB_NAMES_HI.get(r["analyte"], r["analyte"]) if lang == "hi" else r["analyte"]
        parts = [t["value"].format(name=name, value=f"{r['value']:g}", unit=r.get("unit") or "")]
        if r.get("ref_low") is not None and r.get("ref_high") is not None:
            parts.append(t["range"].format(low=f"{r['ref_low']:g}", high=f"{r['ref_high']:g}"))
        parts.append(t.get(r.get("flag") or "N", ""))
        lines.append({"analyte": r["analyte"], "flag": r.get("flag"), "text": " ".join(p for p in parts if p).replace("  ", " ")})
    urgent = any((r.get("flag") in ("HH", "LL")) for r in results)
    return {"lang": lang, "lines": lines, "urgent": urgent, "disclaimer": t["disclaimer"]}
