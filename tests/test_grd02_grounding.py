"""TC-GRD-02: invalid terms injected into model output are rejected and never reach the saved note."""
from __future__ import annotations

import json

import pytest

TRANSCRIPT = ("Patient: Fever and productive cough for three days.\n"
              "Doctor: Crackles at the right base. I think this is pneumonia. Start amoxicillin 500 mg three times daily for 5 days. Check a CBC.")

FAKES = {
    "drug": "Zentravir",
    "diagnosis": "Hyperglobulinic fever syndrome",
    "test": "Serum flarbonate",
    "narrative_drug": "Quinoxafen 250 mg",
}

INJECTED_DRAFTS = {
    "fake-drug": {"assessment": [{"display": "Pneumonia"}], "medications": [{"drug": FAKES["drug"], "strength_mg": 200, "frequency": "BD"}], "tests": []},
    "fake-diagnosis": {"assessment": [{"display": FAKES["diagnosis"]}], "medications": [{"drug": "amoxicillin", "strength_mg": 500, "frequency": "TDS"}], "tests": []},
    "fake-test": {"assessment": [{"display": "Pneumonia"}], "medications": [], "tests": [{"display": FAKES["test"]}]},
    "bad-strength": {"assessment": [{"display": "Pneumonia"}], "medications": [{"drug": "amoxicillin", "strength_mg": 5000, "frequency": "TDS"}], "tests": []},
    "narrative-only": {"assessment": [{"display": "Pneumonia"}], "medications": [{"drug": "amoxicillin", "strength_mg": 500, "frequency": "TDS"}], "tests": [],
                       "narrative": {"S": "", "O": "", "A": "Pneumonia.", "P": f"Start amoxicillin 500 mg TDS and {FAKES['narrative_drug']} BD."}},
}


def _note_text(soap: dict) -> str:
    return json.dumps(soap).lower()


@pytest.mark.parametrize("name", list(INJECTED_DRAFTS))
def test_injected_terms_rejected_and_absent(as_user, record, name):
    doctor = as_user("dr.rao")
    draft = INJECTED_DRAFTS[name]
    out = doctor.ok("POST", "ai/scribe", json={"transcript": TRANSCRIPT, "patient_id": "P0002", "draft_override": draft})
    assert out["generator"] == "rule-based-fallback", f"{name}: draft was accepted: {out['generator']}"
    assert out["rejected_draft"]["rejected"], f"{name}: nothing listed as rejected"
    soap = out["soap"]
    assert soap["grounding"]["accepted"] is True
    text = _note_text(soap)
    for fake in FAKES.values():
        assert fake.lower() not in text, f"{name}: '{fake}' leaked into the note"
    assert "5000" not in json.dumps(soap["plan"]["medications"]), f"{name}: out-of-range strength leaked"

    saved = doctor.ok("POST", "clinical/patients/P0002/encounters", json={"reason": f"TC-GRD-02 {name}", "note": soap}, expect=201)
    timeline = doctor.ok("GET", "clinical/patients/P0002/timeline")
    stored = [e for e in timeline["events"] if e["kind"].startswith("encounter") and e["title"] == f"TC-GRD-02 {name}"]
    assert stored, "saved encounter missing from timeline"
    for fake in FAKES.values():
        assert fake.lower() not in _note_text(stored[0].get("data") or {}), f"{name}: '{fake}' present in the stored note"
    record("TC-GRD-02", **{f"{name}_rejected": [r["text"] for r in out["rejected_draft"]["rejected"]], f"{name}_encounter": saved["encounter_id"]})


def test_forged_grounding_flag_cannot_save_ungrounded_note(as_user, record):
    """A client that skips the AI service and sets grounding.accepted itself must be refused."""
    doctor = as_user("dr.rao")
    forged = {
        "assessment": [{"display": FAKES["diagnosis"], "icd10": "Z99.9", "snomed": "000000"}],
        "plan": {"medications": [{"drug": FAKES["drug"], "rxcui": "999999", "strength_mg": 200, "frequency": "BD"}], "tests": []},
        "narrative": {"S": "", "O": "", "A": FAKES["diagnosis"], "P": f"Start {FAKES['drug']} 200 mg BD."},
        "grounding": {"accepted": True, "rejected": []},
    }
    resp = doctor.post("clinical/patients/P0002/encounters", json={"reason": "TC-GRD-02 forged", "note": forged})
    record("TC-GRD-02", forged_note_status=resp.status_code)
    assert resp.status_code == 422, f"forged note accepted with {resp.status_code}: {resp.text[:200]}"


def test_guardrail_endpoint_rate(as_user, record):
    """Across every injected draft, the share of ungrounded terms that survive must be 0%."""
    doctor = as_user("dr.rao")
    injected, survived = 0, 0
    for draft in INJECTED_DRAFTS.values():
        rep = doctor.ok("POST", "ai/guardrail/check", json={"proposal": draft})
        assert rep["accepted"] is False
        injected += 1
        survived += 0 if rep["rejected"] else 1
    record("TC-GRD-02", injected_drafts=injected, ungrounded_survival_rate=survived / injected)
    assert survived == 0
