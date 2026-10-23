"""TC-SEC-12: unauthenticated, forged and cross-facility calls are refused with 403 and audited."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest

from conftest import GATEWAY, token

PHI_ENDPOINTS = [
    ("GET", "clinical/patients/P0001", None),
    ("GET", "clinical/patients/P0001/timeline", None),
    ("GET", "clinical/patients/P0001/fhir", None),
    ("GET", "diag/results?patient_id=P0001", None),
    ("GET", "diag/dicom/studies?patient_id=P0001", None),
    ("POST", "ai/differential", {"patient_id": "P0001", "symptoms_text": "fever"}),
    ("POST", "ai/translate", {"patient_id": "P0001", "text": "", "lang": "hi"}),
]
ANY_ENDPOINTS = PHI_ENDPOINTS + [("GET", "life/clinics", None), ("POST", "ai/codify", {"text": "fever"}), ("GET", "clinical/alerts", None)]


def _b64(obj) -> str:
    raw = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _forgeries() -> dict[str, str]:
    real = token("dr.rao")
    header, payload, sig = real.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    escalated = {**claims, "preferred_username": "admin.audit", "realm_access": {"roles": ["admin"]}}
    expired = {**claims, "exp": int(time.time()) - 3600}
    hs_header = {"alg": "HS256", "typ": "JWT", "kid": json.loads(base64.urlsafe_b64decode(header + "=="))["kid"]}
    signing_input = f"{_b64(hs_header)}.{_b64(claims)}".encode()
    hs_sig = _b64(hmac.new(b"omnimed", signing_input, hashlib.sha256).digest())
    return {
        "tampered-claims": f"{header}.{_b64(escalated)}.{sig}",
        "expired": f"{header}.{_b64(expired)}.{sig}",
        "alg-none": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}.",
        "hs256-confusion": f"{signing_input.decode()}.{hs_sig}",
        "garbage": "not-a-jwt",
    }


def _call(method, path, body, headers):
    return httpx.request(method, f"{GATEWAY}/api/{path}", json=body, headers=headers, timeout=30)


@pytest.mark.parametrize("method,path,body", ANY_ENDPOINTS)
def test_unauthenticated_is_403(method, path, body):
    resp = _call(method, path, body, {})
    assert resp.status_code == 403, f"{method} {path} without a token -> {resp.status_code}"


@pytest.mark.parametrize("kind", ["tampered-claims", "expired", "alg-none", "hs256-confusion", "garbage"])
def test_forged_tokens_are_403(kind):
    forged = _forgeries()[kind]
    for method, path, body in PHI_ENDPOINTS[:4] + [("GET", "life/clinics", None)]:
        resp = _call(method, path, body, {"Authorization": f"Bearer {forged}"})
        assert resp.status_code == 403, f"{kind}: {method} {path} -> {resp.status_code}"


CROSS = [
    ("dr.mehta", "P0001", "other-facility doctor reads F1 patient"),
    ("dr.rao", "P0101", "F1 doctor reads F2 patient"),
    ("mr.vikram", "P0001", "medical representative reads PHI"),
    ("patient.lakshmi", "P0002", "patient reads another patient"),
]


@pytest.mark.parametrize("user,patient,why", CROSS, ids=[c[2] for c in CROSS])
def test_cross_boundary_reads_are_403(as_user, user, patient, why):
    api = as_user(user)
    for method, path, body in PHI_ENDPOINTS:
        path = path.replace("P0001", patient)
        if body:
            body = {**body, "patient_id": patient}
        resp = api.request(method, path, json=body)
        assert resp.status_code == 403, f"{why}: {method} {path} -> {resp.status_code} {resp.text[:200]}"


def test_role_boundaries(as_user):
    nurse, mr, patient, lab = as_user("nurse.anita"), as_user("mr.vikram"), as_user("patient.lakshmi"), as_user("lab.suresh")
    rx = {"patient_id": "P0001", "rxcui": "17767", "strength_mg": 5, "frequency": "OD", "duration_days": 7}
    assert nurse.post("clinical/prescriptions", json=rx).status_code == 403
    assert patient.post("clinical/prescriptions", json=rx).status_code == 403
    assert mr.post("ai/scribe", json={"transcript": "Doctor: start amlodipine 5 mg"}).status_code == 403
    assert lab.post("life/checkins", json={"clinic_id": "C1", "lat": 12.9716, "lon": 77.5946}).status_code == 403
    assert nurse.get("clinical/audit").status_code == 403


def test_internal_endpoint_not_reachable_through_gateway(as_user):
    resp = as_user("dr.rao").post("clinical/internal/authz", json={"patient_id": "P0101", "action": "read"})
    assert resp.status_code == 403


def test_denials_are_audited_and_chain_valid(as_user, record):
    as_user("dr.mehta").get("clinical/patients/P0001")
    admin = as_user("admin.audit")
    denied = admin.ok("GET", "clinical/audit", params={"outcome": "denied", "limit": 50})
    rows = denied if isinstance(denied, list) else denied.get("entries", denied.get("items", []))
    assert any(r.get("actor") == "dr.mehta" for r in rows), "cross-facility denial not in the audit ledger"
    verify = admin.ok("GET", "clinical/audit/verify")
    record("TC-SEC-12", audit_verify=verify)
    assert verify.get("valid") is True
