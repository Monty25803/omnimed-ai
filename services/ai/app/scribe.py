"""Rule-based ambient scribe: transcript -> structured SOAP with standard codes."""
from __future__ import annotations

import re

from .extract import START_RE, STOP_RE, extract_vitals, find_entities, parse_sig, sentence_of
from .ontology import Ontology

SPEAKER_RE = re.compile(r"^\s*(doctor|dr|physician|clinician|patient|pt|caregiver|nurse|relative|mother|father|son|daughter)\s*[:\-]\s*", re.I)
DX_CUES = re.compile(r"\b(i think|likely|probably|diagnos\w*|this is|looks like|consistent with|you have|suspect\w*|impression|assessment|in keeping with|due to|caused by|shows|showed|confirms|confirmed|reveals|revealed)\b", re.I)
ORDER_CUES = re.compile(r"\b(check|order|ordering|send|get|test|tests|do an?|arrange|repeat|draw)\b", re.I)
HISTORY_CUES = re.compile(r"\b(history of|known|diagnosed with|i have|i've had|i had|suffer from|on treatment for|for years)\b", re.I)
EXAM_CUES = re.compile(r"\b(on examination|examination|exam|auscultation|crackles|wheeze heard|tender|tenderness|chest is|abdomen|pupils|oedema noted|edema noted|breath sounds)\b", re.I)
FOLLOWUP_RE = re.compile(r"\b(follow[- ]?up|come back|review|see you)\b[^.?!]*", re.I)
DURATION_SYMPTOM_RE = re.compile(r"\b(?:for|since|past|last)\s+(\d+|a|one|two|three|four|five|six|seven|few)\s+(hours?|days?|weeks?|months?)\b", re.I)


def split_turns(transcript: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        if not line.strip():
            continue
        m = SPEAKER_RE.match(line)
        if m:
            who = m.group(1).lower()
            role = "doctor" if who in ("doctor", "dr", "physician", "clinician") else "nurse" if who == "nurse" else "patient"
            turns.append((role, line[m.end():].strip()))
        elif turns:
            turns[-1] = (turns[-1][0], turns[-1][1] + " " + line.strip())
        else:
            turns.append(("patient", line.strip()))
    return turns


def build_soap(transcript: str, onto: Ontology) -> dict:
    turns = split_turns(transcript)
    patient_text = " ".join(t for r, t in turns if r == "patient")
    doctor_text = " ".join(t for r, t in turns if r in ("doctor", "nurse"))
    full = "\n".join(t for _, t in turns)

    symptoms: dict[str, dict] = {}
    history: dict[str, dict] = {}
    reported_meds: dict[str, dict] = {}
    for e in find_entities(patient_text, onto, kinds={"finding", "diagnosis", "drug", "allergy"}):
        sent = sentence_of(patient_text, e.start, e.end)
        if c := e.first(kind="finding"):
            symptoms.setdefault(c.code, {"display": c.display, "snomed": c.code, "negated": e.negated, "text": e.text})
        elif (c := e.first(system="SNOMED-CT", kind="diagnosis")) and not e.negated:
            icd = onto.get("ICD-10", c.attr("icd10"))
            history.setdefault(c.code, {"display": c.display, "snomed": c.code, "icd10": icd.code if icd else None, "evidence": sent})
        elif (c := e.first(system="RxNorm")) and not e.negated:
            reported_meds.setdefault(c.code, {"drug": c.display, "rxcui": c.code})
        elif c := e.first(kind="allergy"):
            history.setdefault(c.code, {"display": c.display, "snomed": c.code, "icd10": None, "evidence": sent})

    assessment: dict[str, dict] = {}
    meds: dict[str, dict] = {}
    tests: dict[str, dict] = {}
    for e in find_entities(doctor_text, onto, kinds={"finding", "diagnosis", "drug", "lab"}):
        sent = sentence_of(doctor_text, e.start, e.end)
        if (c := e.first(system="SNOMED-CT", kind="diagnosis")) and not e.negated:
            icd = onto.get("ICD-10", c.attr("icd10"))
            if DX_CUES.search(sent):
                certainty = "provisional" if re.search(r"\b(think|likely|probably|suspect|looks like|possible)\b", sent, re.I) else "confirmed"
                assessment.setdefault(c.code, {"display": c.display, "snomed": c.code, "icd10": icd.code if icd else None, "certainty": certainty, "evidence": sent})
            elif HISTORY_CUES.search(sent):
                history.setdefault(c.code, {"display": c.display, "snomed": c.code, "icd10": icd.code if icd else None, "evidence": sent})
        elif c := e.first(system="RxNorm"):
            window = doctor_text[e.start:min(len(doctor_text), e.end + 90)]
            window = re.split(r"(?<!\d)[.;](?!\d)|\band\s+(?:start|give|add|check|order)\b", window)[0]
            sig = parse_sig(window)
            pos = sent.lower().find(e.text.lower())
            before = sent[max(0, pos - 40):pos] if pos >= 0 else sent
            if STOP_RE.search(before):
                action = "stop"
            elif re.search(r"\bcontinu\w*\b", before, re.I):
                action = "continue"
            elif START_RE.search(before) or sig.strength:
                action = "start"
            else:
                continue
            meds[c.code] = {"drug": c.display, "rxcui": c.code, "strength_mg": sig.strength, "unit": sig.unit, "frequency": sig.frequency,
                            "duration_days": sig.duration_days, "action": action, "dose_units": 1, "evidence": sent}
        elif (c := e.first(system="LOINC")) and ORDER_CUES.search(sent) and not e.negated:
            tests.setdefault(c.code, {"display": c.display, "loinc": c.code})
        elif (c := e.first(kind="finding")) and not e.negated and c.code not in symptoms and EXAM_CUES.search(sent):
            symptoms.setdefault(c.code, {"display": c.display, "snomed": c.code, "negated": False, "text": e.text, "source": "exam"})

    vitals = extract_vitals(full)
    exam = [s.strip() for s in re.split(r"(?<!\d)[.?!](?!\d)", doctor_text) if EXAM_CUES.search(s)]
    duration = None
    if m := DURATION_SYMPTOM_RE.search(patient_text):
        duration = f"{m.group(1)} {m.group(2)}"
    follow = None
    if m := FOLLOWUP_RE.search(doctor_text):
        follow = m.group(0).strip()

    positives = [s for s in symptoms.values() if not s["negated"]]
    negatives = [s for s in symptoms.values() if s["negated"]]
    soap = {
        "subjective": {"chief_complaint": positives[0]["display"] if positives else None, "duration": duration, "symptoms": positives,
                       "pertinent_negatives": negatives, "history": list(history.values()), "reported_medications": list(reported_meds.values())},
        "objective": {"vitals": vitals, "exam": exam},
        "assessment": list(assessment.values()),
        "plan": {"medications": list(meds.values()), "tests": list(tests.values()), "follow_up": follow},
    }
    soap["narrative"] = render_narrative(soap)
    soap["codes"] = collect_codes(soap)
    return soap


def _join(items: list[str]) -> str:
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


FREQ_TEXT = {"OD": "once daily", "BD": "twice daily", "TDS": "three times daily", "QID": "four times daily", "HS": "at night", "PRN": "as needed", "STAT": "immediately"}


def render_narrative(soap: dict) -> dict:
    s, o, a, p = soap["subjective"], soap["objective"], soap["assessment"], soap["plan"]
    subj = []
    if s["symptoms"]:
        subj.append("Presents with " + _join([x["display"].lower() for x in s["symptoms"]]) + (f" for {s['duration']}" if s["duration"] else "") + ".")
    if s["pertinent_negatives"]:
        subj.append("Denies " + _join([x["display"].lower() for x in s["pertinent_negatives"]]) + ".")
    if s["history"]:
        subj.append("Background: " + _join([x["display"] for x in s["history"]]) + ".")
    if s["reported_medications"]:
        subj.append("Current medications: " + _join([m["drug"] for m in s["reported_medications"]]) + ".")
    v = o["vitals"]
    parts = []
    if "sbp" in v:
        parts.append(f"BP {v['sbp']}/{v['dbp']} mmHg")
    for k, label, unit in (("hr", "HR", "/min"), ("rr", "RR", "/min"), ("temp", "Temp", " C"), ("spo2", "SpO2", "%"), ("gcs", "GCS", "")):
        if k in v:
            parts.append(f"{label} {v[k]}{unit}")
    obj = ("Vitals: " + ", ".join(parts) + ".") if parts else "Vitals not stated in conversation."
    assess = " ".join(f"{x['display']} (ICD-10 {x['icd10']}, SNOMED {x['snomed']}), {x['certainty']}." for x in a) or "No diagnosis stated."
    plan = []
    for m in p["medications"]:
        if m["action"] == "stop":
            plan.append(f"Stop {m['drug']}.")
        else:
            dur = f" for {m['duration_days']} days" if m.get("duration_days") else ""
            st = f" {m['strength_mg']:g} mg" if m.get("strength_mg") else ""
            verb = "Continue" if m["action"] == "continue" else "Start"
            plan.append(f"{verb} {m['drug']}{st} {FREQ_TEXT.get(m.get('frequency') or '', '')}{dur} (RxNorm {m['rxcui']}).".replace("  ", " ").replace(" .", "."))
    if p["tests"]:
        plan.append("Investigations: " + _join([f"{t['display']} (LOINC {t['loinc']})" for t in p["tests"]]) + ".")
    if p["follow_up"]:
        plan.append("Follow-up arranged.")
    return {"S": " ".join(subj) or "No subjective history captured.", "O": obj, "A": assess, "P": " ".join(plan) or "No plan stated."}


def collect_codes(soap: dict) -> dict:
    return {
        "icd10": sorted({x["icd10"] for x in soap["assessment"] if x.get("icd10")}),
        "snomed": sorted({x["snomed"] for x in soap["assessment"]} | {x["snomed"] for x in soap["subjective"]["symptoms"]}),
        "rxnorm": sorted({m["rxcui"] for m in soap["plan"]["medications"]}),
        "loinc": sorted({t["loinc"] for t in soap["plan"]["tests"]}),
    }
