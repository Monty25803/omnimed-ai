"""One happy path per persona, through the same gateway routes the portals and mobile app use."""
from __future__ import annotations

import base64
import struct
import time
import zlib
from urllib.parse import parse_qs, urlparse

import httpx

from conftest import GATEWAY, hl7_potassium


def _png(w: int = 160, h: int = 48) -> str:
    raw = b"".join(b"\x00" + bytes([(x * 37 + y * 101) % 251 for x in range(w)]) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def test_doctor(as_user):
    dr = as_user("dr.rao")
    me = dr.ok("GET", "clinical/me")
    assert me["username"] == "dr.rao"
    patients = dr.ok("GET", "clinical/patients")
    assert {p["id"] for p in patients} >= {"P0001", "P0002"} and "P0101" not in {p["id"] for p in patients}
    summary = dr.ok("GET", "clinical/patients/P0001")
    assert summary["care_team"], "care team should not be empty"
    assert any(a["substance"] == "Penicillin" for a in summary["allergies"])

    check = dr.ok("POST", "clinical/prescriptions/check", json={"patient_id": "P0001", "rxcui": "723", "strength_mg": 500, "frequency": "TDS", "duration_days": 5})
    assert check["blocking"] and any(a["kind"] == "allergy" for a in check["alerts"]), check["alerts"]
    blocked = dr.post("clinical/prescriptions", json={"patient_id": "P0001", "rxcui": "723", "strength_mg": 500, "frequency": "TDS", "duration_days": 5})
    assert blocked.status_code == 409

    as_user("lab.suresh").ok("POST", "diag/lims/hl7", json={"message": hl7_potassium("MRN-100001", 6.8)})
    dx = dr.ok("POST", "ai/differential", json={"patient_id": "P0001", "symptoms_text": "weakness and palpitations"})
    ranked = next(v for k, v in dx.items() if isinstance(v, list) and v and isinstance(v[0], dict) and "icd10" in v[0])
    assert ranked[0]["display"] == "Hyperkalemia", ranked[:3]
    fhir = dr.ok("GET", "clinical/patients/P0001/fhir")
    assert fhir["resourceType"] == "Bundle" and any(e["resource"]["resourceType"] == "Patient" for e in fhir["entry"])
    timeline = dr.ok("GET", "clinical/patients/P0001/timeline")
    assert timeline["events"]


def test_nurse(as_user):
    nurse = as_user("nurse.anita")
    grid = nurse.ok("GET", "clinical/ward/grid")
    rows = grid if isinstance(grid, list) else grid.get("patients", grid.get("beds", []))
    assert rows, "ward grid is empty"
    v = nurse.ok("POST", "clinical/patients/P0004/vitals", json={"hr": 88, "sbp": 118, "dbp": 72, "rr": 18, "temp": 36.8, "spo2": 96, "gcs": 15})
    assert v["score"]["mews"] >= 0 and v["score"]["red_alert"] is False
    assert isinstance(nurse.ok("GET", "clinical/tasks"), list)
    due = nurse.ok("GET", "clinical/emar")
    assert isinstance(due, list)
    bad = nurse.post("clinical/emar/administer", json={"wristband": "MRN-100002", "med_barcode": "8901000000021"})
    assert bad.status_code in (409, 422), f"wrong-patient scan should be refused, got {bad.status_code}"


def test_pharmacist(as_user):
    ph = as_user("pharm.kiran")
    queue = ph.ok("GET", "clinical/prescriptions")
    assert queue
    subs = ph.ok("GET", "clinical/pharmacy/substitutes", params={"rxcui": "9997", "strength_mg": 25})
    assert subs["rxcui"] == "9997" and any(isinstance(v, list) and v and "brand" in v[0] for v in subs.values()), subs
    inv = ph.ok("GET", "clinical/pharmacy/inventory")
    assert any(i["rxcui"] == "29046" for i in inv)
    assert isinstance(ph.ok("GET", "clinical/pharmacy/refill-risk"), list)


def test_lab_report_signing_and_public_verify(as_user, record):
    lab = as_user("lab.suresh")
    ts = time.strftime("%Y%m%d%H%M%S")
    astm = "\r".join([f"H|\\^&|ASTM-{ts}||OMNI-CHEM^1.0|||||||P|1|{ts}", "P|1||MRN-100002", "O|1|S1||^^^CBC",
                      "R|1|^^^WBC|14.2|10*3/uL|4.0-11.0|H", "R|2|^^^HGB|11.1|g/dL|12.0-17.5|L", "R|3|^^^PLT|210|10*3/uL|150-450|N", "L|1|N"])
    ingest = lab.ok("POST", "diag/lims/astm", json={"message": astm})
    ids = [r["result_id"] for r in ingest["results"]]
    assert len(ids) == 3 and not ingest["unknown_codes"]
    rep = lab.ok("POST", "diag/reports", json={"patient_id": "P0002", "result_ids": ids, "title": "Complete blood count (smoke)"})
    signed = lab.ok("POST", f"diag/reports/{rep['id']}/sign")
    q = {k: v[0] for k, v in parse_qs(urlparse(signed["verify_url"]).query).items()}

    public = httpx.get(f"{GATEWAY}/api/diag/verify", params=q, timeout=30).json()
    assert public["valid"] is True and all(public["checks"].values())
    tampered = httpx.get(f"{GATEWAY}/api/diag/verify", params={**q, "h": "0" * 64}, timeout=30).json()
    assert tampered["valid"] is False
    pdf = lab.get(f"diag/reports/{rep['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    record("SMOKE", signed_report=rep["id"], verify_checks=public["checks"])


def test_medical_rep(as_user):
    mr = as_user("mr.vikram")
    clinics = mr.ok("GET", "life/clinics")
    assert {c["id"] for c in clinics} >= {"C1", "C3"}
    far = mr.post("life/checkins", json={"clinic_id": "C1", "lat": 12.99, "lon": 77.70, "accuracy_m": 10})
    assert far.status_code == 403
    no_consent = mr.post("life/checkins", json={"clinic_id": "C3", "lat": 12.2958, "lon": 76.6394, "accuracy_m": 10})
    assert no_consent.status_code == 403, "doctor without detailing consent must not be visitable"
    chk = mr.ok("POST", "life/checkins", json={"clinic_id": "C1", "lat": 12.9717, "lon": 77.5947, "accuracy_m": 12})
    assert chk["accepted"]
    stock = mr.ok("GET", "life/samples/stock")
    product = max(stock, key=lambda s: s["qty"])["product"]
    dist = mr.post("life/samples/distribute", json={"checkin_id": chk["checkin_id"], "product": product, "qty": 1, "signature_png": _png()})
    assert dist.status_code in (200, 409), dist.text
    unsigned = mr.post("life/samples/distribute", json={"checkin_id": chk["checkin_id"], "product": product, "qty": 1, "signature_png": "data:image/png;base64,AAAA"})
    assert unsigned.status_code == 422
    mr.ok("POST", f"life/checkins/{chk['checkin_id']}/end")
    assert mr.get("clinical/patients").status_code == 403


def test_patient(as_user):
    pt = as_user("patient.lakshmi")
    me = pt.ok("GET", "clinical/me")
    assert me["username"] == "patient.lakshmi"
    explained = pt.ok("POST", "ai/translate", json={"patient_id": "P0001", "lang": "hi", "text": ""})
    assert explained["results"]
    assert isinstance(pt.ok("GET", "clinical/consents"), list)
    slots = pt.ok("GET", "clinical/availability")
    assert isinstance(slots, list)
    assert isinstance(pt.ok("GET", "clinical/locker"), dict)


def test_admin_audit(as_user):
    admin = as_user("admin.audit")
    rows = admin.ok("GET", "clinical/audit", params={"limit": 20})
    assert rows
    assert admin.ok("GET", "clinical/audit/verify")["valid"] is True
