import pytest

from app.differential import rank
from app.extract import extract_vitals, find_entities, parse_sig
from app.grounding import ground_proposal
from app.ocr import parse_prescription_lines
from app.ontology import Ontology
from app.scribe import build_soap
from app.translator import explain_results, translate_text

ROWS = [
    {"system": "SNOMED-CT", "code": "233604007", "display": "Pneumonia", "synonyms": ["chest infection", "CAP"], "kind": "diagnosis", "attrs": {"icd10": "J18.9"}},
    {"system": "ICD-10", "code": "J18.9", "display": "Pneumonia", "synonyms": ["chest infection"], "kind": "diagnosis", "attrs": {"snomed": "233604007"}},
    {"system": "SNOMED-CT", "code": "44054006", "display": "Type 2 diabetes mellitus", "synonyms": ["diabetes"], "kind": "diagnosis", "attrs": {"icd10": "E11.9"}},
    {"system": "ICD-10", "code": "E11.9", "display": "Type 2 diabetes mellitus", "synonyms": ["diabetes"], "kind": "diagnosis", "attrs": {"snomed": "44054006"}},
    {"system": "SNOMED-CT", "code": "386661006", "display": "Fever", "synonyms": ["fever", "febrile"], "kind": "finding", "attrs": {}},
    {"system": "SNOMED-CT", "code": "49727002", "display": "Cough", "synonyms": ["cough"], "kind": "finding", "attrs": {}},
    {"system": "SNOMED-CT", "code": "29857009", "display": "Chest pain", "synonyms": ["chest pain"], "kind": "finding", "attrs": {}},
    {"system": "RxNorm", "code": "723", "display": "amoxicillin", "synonyms": ["amoxicillin", "novamox"], "kind": "drug", "attrs": {"strengths_mg": [250, 500, 875], "max_daily_mg": 3000}},
    {"system": "RxNorm", "code": "6809", "display": "metformin", "synonyms": ["metformin", "glycomet"], "kind": "drug", "attrs": {"strengths_mg": [500, 850, 1000], "max_daily_mg": 2550}},
    {"system": "RxNorm", "code": "18631", "display": "azithromycin", "synonyms": ["azithromycin", "azithral"], "kind": "drug", "attrs": {"strengths_mg": [250, 500], "max_daily_mg": 500}},
    {"system": "LOINC", "code": "6690-2", "display": "WBC count", "synonyms": ["wbc", "white cell count"], "kind": "lab", "attrs": {}},
    {"system": "LOINC", "code": "36643-5", "display": "Chest X-ray", "synonyms": ["chest x-ray", "chest xray"], "kind": "lab", "attrs": {}},
]


@pytest.fixture(scope="module")
def onto():
    return Ontology.from_rows(ROWS)


def test_negation(onto):
    ents = find_entities("Patient has cough but no fever.", onto)
    by = {e.first().display: e.negated for e in ents}
    assert by == {"Cough": False, "Fever": True}


def test_short_forms_need_uppercase(onto):
    assert any(e.first().code == "233604007" for e in find_entities("Likely CAP on the left", onto))
    assert not find_entities("put your cap on", onto)


def test_sig_parsing():
    s = parse_sig("amoxicillin 500 mg three times a day for 7 days")
    assert (s.strength, s.frequency, s.duration_days) == (500, "TDS", 7)
    assert parse_sig("azithromycin 0.5 g once daily for five days").strength == 500


def test_vitals():
    v = extract_vitals("BP 150/95, pulse 102, temp 101.3 F, saturation 93%, RR 24")
    assert v == {"sbp": 150, "dbp": 95, "hr": 102, "rr": 24, "spo2": 93, "temp": 38.5}


TRANSCRIPT = """Doctor: What brings you in today?
Patient: I have had a cough and fever for 3 days. No chest pain. I have diabetes and take metformin.
Doctor: Your temperature is 38.6, pulse 104, saturation 94%. On examination there are crackles at the right base.
Doctor: I think this is pneumonia. I will start amoxicillin 500 mg three times a day for 7 days. Let's check a WBC and get a chest x-ray. Follow up in 3 days."""


def test_scribe_soap(onto):
    soap = build_soap(TRANSCRIPT, onto)
    assert [a["icd10"] for a in soap["assessment"]] == ["J18.9"]
    med = soap["plan"]["medications"][0]
    assert (med["rxcui"], med["strength_mg"], med["frequency"], med["duration_days"]) == ("723", 500, "TDS", 7)
    assert {t["loinc"] for t in soap["plan"]["tests"]} == {"6690-2", "36643-5"}
    assert {s["display"] for s in soap["subjective"]["pertinent_negatives"]} == {"Chest pain"}
    assert "E11.9" in {h["icd10"] for h in soap["subjective"]["history"]}
    assert ground_proposal({"assessment": soap["assessment"], "medications": soap["plan"]["medications"],
                            "tests": soap["plan"]["tests"], "narrative": soap["narrative"]}, onto).accepted


def test_scribe_investigation_finding_is_assessment(onto):
    soap = build_soap("Doctor: The chest x-ray shows pneumonia.\nDoctor: Known diabetes for years.", onto)
    assert [a["icd10"] for a in soap["assessment"]] == ["J18.9"]
    assert "E11.9" in {h["icd10"] for h in soap["subjective"]["history"]}


@pytest.mark.parametrize("proposal,bad", [
    ({"assessment": [{"display": "Flurbonic syndrome"}]}, "Flurbonic syndrome"),
    ({"medications": [{"drug": "Zyntharex", "strength_mg": 50, "frequency": "OD"}]}, "Zyntharex"),
    ({"medications": [{"drug": "amoxicillin", "strength_mg": 5000, "frequency": "TDS"}]}, "amoxicillin 5000"),
    ({"medications": [{"drug": "azithromycin", "strength_mg": 500, "frequency": "BD"}]}, "azithromycin"),
    ({"assessment": [{"display": "Pneumonia", "icd10": "J45.909"}]}, "Pneumonia"),
    ({"narrative": {"P": "Start Zyntharex 50 mg daily."}}, "Zyntharex"),
    ({"narrative": {"A": "Likely neurocardiolitis."}}, "neurocardiolitis"),
    ({"narrative": {"P": "Add flurbocillin for the chest."}}, "flurbocillin"),
])
def test_grounding_rejects(onto, proposal, bad):
    rep = ground_proposal(proposal, onto)
    assert not rep.accepted
    assert any(bad.lower() in r["text"].lower() for r in rep.rejected)


def test_grounding_accepts_clean_narrative(onto):
    rep = ground_proposal({"narrative": {"A": "Pneumonia, provisional.", "P": "Start amoxicillin 500 mg three times daily. Diagnosis discussed."}}, onto)
    assert rep.accepted, rep.rejected


def test_differential_ranks_pneumonia():
    out = rank({"49727002", "386661006", "28743005", "267036007"}, set(), {"spo2": 91, "temp": 38.7, "rr": 24}, {"6690-2": 15}, set())
    assert out[0]["icd10"] == "J18.9"


def test_translator():
    t = translate_text("Hypertension and hyperkalemia noted", "en")
    assert "high blood pressure" in t["text"]
    r = explain_results([{"analyte": "Potassium", "value": 6.8, "unit": "mmol/L", "ref_low": 3.5, "ref_high": 5.1, "flag": "HH"}], "hi")
    assert r["urgent"] and "पोटैशियम" in r["lines"][0]["text"]


def test_prescription_text_parse(onto):
    items = parse_prescription_lines([{"text": "1) Tab Azithral 500 mg once daily x 5 days", "ocr_confidence": 0.9}], onto)
    assert items[0]["rxcui"] == "18631" and items[0]["frequency"] == "OD" and items[0]["duration_days"] == 5
