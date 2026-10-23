"""Predictive differential diagnosis engine.

A transparent weighted-evidence model: each condition has a prior log-odds and
weighted features over findings (SNOMED), vitals, labs (LOINC) and history.
Scores are heuristic weights, not calibrated probabilities.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Feature:
    label: str
    weight: float
    kind: str  # finding | vital | lab | history
    key: str
    op: str = "present"  # present | gt | lt | ge | le
    threshold: float = 0.0


def f(label, weight, kind, key, op="present", thr=0.0):
    return Feature(label, weight, kind, key, op, thr)


KB: dict[str, dict] = {
    "A41.9": {"display": "Sepsis", "prior": -3.0, "features": [
        f("fever", 0.9, "finding", "386661006"), f("chills or rigors", 0.7, "finding", "43724002"), f("new confusion", 1.2, "finding", "419284004"),
        f("HR > 90", 0.7, "vital", "hr", "gt", 90), f("RR >= 22", 1.0, "vital", "rr", "ge", 22), f("SBP <= 100", 1.2, "vital", "sbp", "le", 100),
        f("temp >= 38", 0.7, "vital", "temp", "ge", 38), f("WBC > 12", 0.8, "lab", "6690-2", "gt", 12), f("lactate > 2", 1.5, "lab", "2524-7", "gt", 2),
        f("CRP > 50", 0.5, "lab", "1988-5", "gt", 50), f("known infection", 0.8, "history", "J18.9")]},
    "J18.9": {"display": "Pneumonia", "prior": -2.5, "features": [
        f("cough", 1.0, "finding", "49727002"), f("productive cough", 1.5, "finding", "28743005"), f("fever", 1.0, "finding", "386661006"),
        f("dyspnea", 0.9, "finding", "267036007"), f("chest pain", 0.3, "finding", "29857009"), f("SpO2 < 94", 1.0, "vital", "spo2", "lt", 94),
        f("RR > 20", 0.6, "vital", "rr", "gt", 20), f("temp >= 38", 0.6, "vital", "temp", "ge", 38), f("WBC > 11", 0.6, "lab", "6690-2", "gt", 11),
        f("CRP > 50", 0.8, "lab", "1988-5", "gt", 50)]},
    "E87.5": {"display": "Hyperkalemia", "prior": -3.5, "features": [
        f("potassium > 5.5", 3.5, "lab", "2823-3", "gt", 5.5), f("potassium >= 6.5", 2.0, "lab", "2823-3", "ge", 6.5),
        f("palpitations", 0.5, "finding", "80313002"), f("fatigue or weakness", 0.3, "finding", "84229001"), f("CKD", 1.0, "history", "N18.3"),
        f("eGFR < 45", 0.8, "lab", "33914-3", "lt", 45)]},
    "I21.9": {"display": "Acute myocardial infarction", "prior": -3.5, "features": [
        f("chest pain", 2.2, "finding", "29857009"), f("dyspnea", 0.4, "finding", "267036007"), f("nausea", 0.4, "finding", "422587007"),
        f("troponin raised", 3.5, "lab", "10839-9", "gt", 0.04), f("HR > 100", 0.3, "vital", "hr", "gt", 100), f("diabetes", 0.3, "history", "E11.9"),
        f("hypertension", 0.3, "history", "I10")]},
    "I50.9": {"display": "Heart failure", "prior": -3.0, "features": [
        f("dyspnea", 1.2, "finding", "267036007"), f("edema", 1.5, "finding", "267038008"), f("fatigue", 0.4, "finding", "84229001"),
        f("NT-proBNP > 125", 2.0, "lab", "33762-6", "gt", 125), f("SpO2 < 94", 0.5, "vital", "spo2", "lt", 94), f("known heart failure", 1.5, "history", "I50.9")]},
    "N39.0": {"display": "Urinary tract infection", "prior": -3.0, "features": [
        f("dysuria", 2.5, "finding", "49650001"), f("frequent urination", 0.8, "finding", "28442001"), f("fever", 0.4, "finding", "386661006"),
        f("abdominal pain", 0.5, "finding", "21522001")]},
    "E11.9": {"display": "Type 2 diabetes mellitus", "prior": -3.5, "features": [
        f("polyuria", 1.3, "finding", "28442001"), f("excessive thirst", 1.3, "finding", "17173007"), f("fatigue", 0.3, "finding", "84229001"),
        f("glucose > 200", 2.0, "lab", "2345-7", "gt", 200), f("HbA1c >= 6.5", 2.5, "lab", "4548-4", "ge", 6.5), f("known diabetes", 1.5, "history", "E11.9")]},
    "J45.909": {"display": "Asthma", "prior": -3.0, "features": [
        f("wheezing", 2.0, "finding", "56018004"), f("dyspnea", 0.9, "finding", "267036007"), f("cough", 0.5, "finding", "49727002"),
        f("known asthma", 1.5, "history", "J45.909"), f("SpO2 < 94", 0.4, "vital", "spo2", "lt", 94)]},
    "J44.9": {"display": "Chronic obstructive pulmonary disease", "prior": -3.5, "features": [
        f("productive cough", 0.9, "finding", "28743005"), f("dyspnea", 1.0, "finding", "267036007"), f("wheezing", 0.8, "finding", "56018004"),
        f("known COPD", 1.8, "history", "J44.9")]},
    "J06.9": {"display": "Upper respiratory tract infection", "prior": -2.0, "features": [
        f("sore throat", 1.6, "finding", "267102003"), f("cough", 0.6, "finding", "49727002"), f("fever", 0.4, "finding", "386661006"),
        f("headache", 0.3, "finding", "25064002"), f("SpO2 < 94", -1.5, "vital", "spo2", "lt", 94)]},
    "K52.9": {"display": "Gastroenteritis", "prior": -2.5, "features": [
        f("diarrhea", 2.0, "finding", "62315008"), f("vomiting", 1.0, "finding", "422400008"), f("nausea", 0.7, "finding", "422587007"),
        f("abdominal pain", 0.8, "finding", "21522001"), f("fever", 0.3, "finding", "386661006")]},
    "A90": {"display": "Dengue fever", "prior": -3.5, "features": [
        f("fever", 1.2, "finding", "386661006"), f("headache", 0.6, "finding", "25064002"), f("muscle pain", 1.1, "finding", "68962001"),
        f("rash", 0.9, "finding", "271807003"), f("platelets < 150", 1.6, "lab", "777-3", "lt", 150)]},
    "E87.1": {"display": "Hyponatremia", "prior": -3.5, "features": [
        f("sodium < 135", 3.0, "lab", "2951-2", "lt", 135), f("sodium < 125", 1.5, "lab", "2951-2", "lt", 125), f("confusion", 0.8, "finding", "419284004"),
        f("nausea", 0.4, "finding", "422587007"), f("headache", 0.3, "finding", "25064002")]},
    "I48.91": {"display": "Atrial fibrillation", "prior": -3.5, "features": [
        f("palpitations", 1.8, "finding", "80313002"), f("HR > 110", 0.8, "vital", "hr", "gt", 110), f("dizziness", 0.4, "finding", "404640003"),
        f("known AF", 1.8, "history", "I48.91")]},
    "G43.909": {"display": "Migraine", "prior": -3.0, "features": [
        f("headache", 2.0, "finding", "25064002"), f("nausea", 0.6, "finding", "422587007"), f("vomiting", 0.3, "finding", "422400008")]},
    "D64.9": {"display": "Anemia", "prior": -3.5, "features": [
        f("fatigue", 0.8, "finding", "84229001"), f("dizziness", 0.5, "finding", "404640003"), f("hemoglobin < 12", 2.8, "lab", "718-7", "lt", 12)]},
}


def _cmp(value: float, op: str, thr: float) -> bool:
    return {"gt": value > thr, "lt": value < thr, "ge": value >= thr, "le": value <= thr}.get(op, False)


def rank(findings: set[str], negated: set[str], vitals: dict, labs: dict[str, float], history: set[str], top: int = 5) -> list[dict]:
    out = []
    for icd, cond in KB.items():
        z = cond["prior"]
        evidence, against = [], []
        for feat in cond["features"]:
            hit = False
            if feat.kind == "finding":
                if feat.key in findings:
                    hit = True
                elif feat.key in negated and feat.weight >= 1.0:
                    z -= 0.5 * feat.weight
                    against.append(f"no {feat.label}")
            elif feat.kind == "vital" and feat.key in vitals and vitals[feat.key] is not None:
                hit = _cmp(float(vitals[feat.key]), feat.op, feat.threshold)
            elif feat.kind == "lab" and feat.key in labs:
                hit = _cmp(float(labs[feat.key]), feat.op, feat.threshold)
            elif feat.kind == "history":
                hit = feat.key in history
            if hit:
                z += feat.weight
                (evidence if feat.weight > 0 else against).append(feat.label)
        if not evidence:
            continue
        p = 1 / (1 + math.exp(-z))
        out.append({"icd10": icd, "display": cond["display"], "score": round(p, 3), "evidence": evidence, "against": against})
    out.sort(key=lambda x: -x["score"])
    return [x for x in out if x["score"] >= 0.05][:top]
