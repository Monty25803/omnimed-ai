from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from minio import Minio
from pydantic import BaseModel

from omnicommon.runtime import create_app, log, rt, settings, utcnow

from . import lims
from .segment import PLUGINS
from .synth import chest_ct_series

ORTHANC = os.getenv("ORTHANC_URL", "http://localhost:8042")
PUBLIC_WEB = os.getenv("PUBLIC_WEB_URL", "http://localhost:3000")

state: dict = {"by_astm": {}, "by_loinc": {}, "key": None, "cert_pem": b"", "minio": None}
_authz_cache: dict[tuple[str, str], float] = {}


async def on_start() -> None:
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT code, display, attrs FROM ai.concepts WHERE system='LOINC' AND kind='lab'")
    for r in rows:
        entry = {"loinc": r["code"], "display": r["display"], **r["attrs"]}
        state["by_loinc"][r["code"]] = entry
        if r["attrs"].get("astm"):
            state["by_astm"][r["attrs"]["astm"]] = entry
    mc = Minio(settings.minio_endpoint, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=settings.minio_secure)
    for b in (settings.reports_bucket, settings.pki_bucket, settings.volume_bucket):
        if not mc.bucket_exists(b):
            mc.make_bucket(b)
    state["minio"] = mc
    _load_signing_key(mc)
    asyncio.create_task(_seed_imaging())


app = create_app("diagnostics", "OmniMed Diagnostics Service", on_start)


# ---------------------------------------------------------------- PKI

def _load_signing_key(mc: Minio) -> None:
    try:
        key_pem = mc.get_object(settings.pki_bucket, "lab-signing-key.pem").read()
        cert_pem = mc.get_object(settings.pki_bucket, "lab-signing-cert.pem").read()
        key = serialization.load_pem_private_key(key_pem, password=None)
    except Exception:  # noqa: BLE001 - first start
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OmniMed Diagnostic Report Signing"),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "OmniMed City Hospital Laboratory")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=False,
                                             key_agreement=False, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        mc.put_object(settings.pki_bucket, "lab-signing-key.pem", io.BytesIO(key_pem), len(key_pem))
        mc.put_object(settings.pki_bucket, "lab-signing-cert.pem", io.BytesIO(cert_pem), len(cert_pem))
        log.info("generated new report signing key")
    state["key"] = key
    state["cert_pem"] = cert_pem


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- LIMS ingest

class IngestBody(BaseModel):
    message: str


async def _patient_for_mrn(mrn: str) -> str:
    async with rt.db.acquire() as c:
        pid = await c.fetchval("SELECT id FROM clinical.patients WHERE mrn=$1", mrn)
    if not pid:
        raise HTTPException(404, f"no patient with identifier {mrn}")
    return pid


async def _ingest(request: Request, parsed: lims.ParsedMessage) -> dict:
    received = utcnow()
    if parsed.errors and not parsed.results:
        raise HTTPException(422, {"errors": parsed.errors})
    pid = await _patient_for_mrn(parsed.mrn)
    p = await rt.authorize(request, pid, action="result", roles=["lab"])
    stored, panics, unknown = [], [], []
    async with rt.db.acquire() as c:
        for r in parsed.results:
            ref = state["by_loinc"].get(r.code) if r.code_system == "loinc" else state["by_astm"].get(r.code)
            if not ref:
                unknown.append(r.code)
                continue
            flag = lims.classify(r.value, ref)
            unit = ref.get("unit") or r.unit
            rid = await c.fetchval(
                """INSERT INTO diag.lab_results(patient_id, loinc, analyte, value, unit, ref_low, ref_high, flag, instrument, message_id, protocol, received_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id""",
                pid, ref["loinc"], ref["display"], r.value, unit, ref.get("low"), ref.get("high"), flag, parsed.instrument, parsed.message_id, parsed.protocol, received)
            item = {"result_id": rid, "patient_id": pid, "loinc": ref["loinc"], "analyte": ref["display"], "value": r.value, "unit": unit,
                    "flag": flag, "ref_low": ref.get("low"), "ref_high": ref.get("high"), "received_at": received, "message_id": parsed.message_id,
                    "entered_by": p.username}
            stored.append(item)
            if flag in ("LL", "HH"):
                panics.append(item)
    # Panic events go first: they gate the < 500 ms synapse target.
    for item in panics:
        await rt.publish("lab.panic", item)
    for item in stored:
        await rt.publish("lab.result", item)
    await rt.audit(p, "lab.ingest", "Observation", parsed.message_id, patient=pid, protocol=parsed.protocol, results=len(stored), panics=len(panics))
    return {"patient_id": pid, "protocol": parsed.protocol, "instrument": parsed.instrument, "message_id": parsed.message_id,
            "results": stored, "panic_count": len(panics), "unknown_codes": unknown, "warnings": parsed.errors}


@app.post("/lims/astm")
async def ingest_astm(body: IngestBody, request: Request):
    return await _ingest(request, lims.parse_astm(body.message))


@app.post("/lims/hl7")
async def ingest_hl7(body: IngestBody, request: Request):
    return await _ingest(request, lims.parse_hl7(body.message))


@app.get("/lims/catalog")
async def catalog(request: Request):
    await rt.authorize(request)
    return sorted(state["by_loinc"].values(), key=lambda x: x["display"])


@app.get("/results")
async def results(request: Request, patient_id: str):
    await rt.authorize(request, patient_id)
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT * FROM diag.lab_results WHERE patient_id=$1 ORDER BY received_at DESC LIMIT 200", patient_id)
    return [dict(r) for r in rows]


@app.get("/results/recent")
async def recent_results(request: Request):
    p = await rt.authorize(request, roles=["lab", "doctor"])
    async with rt.db.acquire() as c:
        rows = await c.fetch("""SELECT r.* FROM diag.lab_results r JOIN clinical.patients pt ON pt.id=r.patient_id
            WHERE pt.facility_id=$1 ORDER BY r.received_at DESC LIMIT 100""", p.facility_id)
    await rt.audit(p, "phi.list", "Observation", "", count=len(rows))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- reports

class ReportBody(BaseModel):
    patient_id: str
    result_ids: list[int]
    title: str = "Laboratory Report"


def _canonical(report: dict, results: list[dict]) -> bytes:
    content = {
        "report_id": report["id"], "patient_id": report["patient_id"], "title": report["title"],
        "signed_by": report["signed_by"], "signed_at": report["signed_at"].astimezone(timezone.utc).isoformat(),
        "results": [{"loinc": r["loinc"], "analyte": r["analyte"], "value": r["value"], "unit": r["unit"], "flag": r["flag"],
                     "ref_low": r["ref_low"], "ref_high": r["ref_high"]} for r in sorted(results, key=lambda x: x["id"])],
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":")).encode()


@app.post("/reports")
async def create_report(body: ReportBody, request: Request):
    p = await rt.authorize(request, body.patient_id, action="result", roles=["lab"])
    async with rt.db.acquire() as c:
        n = await c.fetchval("SELECT count(*) FROM diag.lab_results WHERE patient_id=$1 AND id = ANY($2)", body.patient_id, body.result_ids)
        if n != len(body.result_ids) or n == 0:
            raise HTTPException(422, "results must exist and belong to the patient")
        rid = await c.fetchval("INSERT INTO diag.reports(patient_id, title, result_ids) VALUES ($1,$2,$3) RETURNING id", body.patient_id, body.title, body.result_ids)
    await rt.audit(p, "report.create", "DiagnosticReport", rid, patient=body.patient_id)
    return {"id": rid, "status": "draft"}


@app.get("/reports")
async def list_reports(request: Request, patient_id: str | None = None):
    if patient_id:
        await rt.authorize(request, patient_id)
        q, args = "SELECT * FROM diag.reports WHERE patient_id=$1 ORDER BY created_at DESC", [patient_id]
    else:
        p = await rt.authorize(request, roles=["lab"])
        q, args = ("SELECT r.* FROM diag.reports r JOIN clinical.patients pt ON pt.id=r.patient_id WHERE pt.facility_id=$1 ORDER BY r.created_at DESC LIMIT 100", [p.facility_id])
    async with rt.db.acquire() as c:
        rows = await c.fetch(q, *args)
    return [{k: v for k, v in dict(r).items() if k != "signature"} for r in rows]


def _render_pdf(report: dict, results: list[dict], patient: dict, qr_url: str, digest: str) -> bytes:
    import qrcode
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    cv = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    cv.setFillColor(colors.HexColor("#0f4c5c"))
    cv.rect(0, h - 28 * mm, w, 28 * mm, fill=1, stroke=0)
    cv.setFillColor(colors.white)
    cv.setFont("Helvetica-Bold", 16)
    cv.drawString(15 * mm, h - 14 * mm, "OmniMed City Hospital Laboratory")
    cv.setFont("Helvetica", 10)
    cv.drawString(15 * mm, h - 21 * mm, f"{report['title']}  |  Report #{report['id']}")
    cv.setFillColor(colors.black)
    y = h - 40 * mm
    pt = patient.get("patient", {})
    cv.setFont("Helvetica", 10)
    for line in (f"Patient: {pt.get('name', '')}   MRN: {pt.get('mrn', '')}   Age/Sex: {pt.get('age', '')}/{pt.get('gender', '')}",
                 f"Signed by: {report['signed_by']}   at {report['signed_at'].astimezone(timezone.utc):%Y-%m-%d %H:%M UTC}"):
        cv.drawString(15 * mm, y, line)
        y -= 6 * mm
    y -= 4 * mm
    cv.setFont("Helvetica-Bold", 10)
    for x, t in ((15, "Test (LOINC)"), (95, "Result"), (125, "Unit"), (150, "Reference"), (180, "Flag")):
        cv.drawString(x * mm, y, t)
    y -= 2 * mm
    cv.line(15 * mm, y, w - 15 * mm, y)
    y -= 6 * mm
    for r in results:
        abnormal = r["flag"] != "N"
        cv.setFont("Helvetica-Bold" if abnormal else "Helvetica", 10)
        cv.setFillColor(colors.HexColor("#b00020") if r["flag"] in ("HH", "LL") else colors.black)
        ref = f"{r['ref_low'] if r['ref_low'] is not None else ''} - {r['ref_high'] if r['ref_high'] is not None else ''}"
        for x, t in ((15, f"{r['analyte']} ({r['loinc']})"), (95, f"{r['value']:g}"), (125, r["unit"] or ""), (150, ref), (180, r["flag"])):
            cv.drawString(x * mm, y, str(t))
        y -= 6 * mm
    cv.setFillColor(colors.black)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=4, border=2)
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    ib = io.BytesIO()
    img.save(ib, format="PNG")
    ib.seek(0)
    cv.drawImage(ImageReader(ib), 15 * mm, 18 * mm, 42 * mm, 42 * mm)
    cv.setFont("Helvetica", 8)
    cv.drawString(62 * mm, 50 * mm, "Scan to verify authenticity. The QR carries an ECDSA P-256 signature")
    cv.drawString(62 * mm, 45 * mm, "over the canonical report content.")
    cv.drawString(62 * mm, 38 * mm, f"SHA-256: {digest[:32]}")
    cv.drawString(62 * mm, 34 * mm, f"         {digest[32:]}")
    cv.drawString(62 * mm, 26 * mm, "Synthetic demonstration data. Not for clinical use.")
    cv.showPage()
    cv.save()
    return buf.getvalue()


@app.post("/reports/{report_id}/sign")
async def sign_report(report_id: int, request: Request):
    async with rt.db.acquire() as c:
        report = await c.fetchrow("SELECT * FROM diag.reports WHERE id=$1", report_id)
    if not report:
        raise HTTPException(404, "report not found")
    p = await rt.authorize(request, report["patient_id"], action="result", roles=["lab"])
    if report["status"] == "signed":
        raise HTTPException(409, "report already signed")
    signed_at = utcnow()
    async with rt.db.acquire() as c:
        results = [dict(r) for r in await c.fetch("SELECT * FROM diag.lab_results WHERE id = ANY($1)", report["result_ids"])]
    rep = {**dict(report), "signed_by": p.display_name, "signed_at": signed_at}
    canon = _canonical(rep, results)
    digest = hashlib.sha256(canon).hexdigest()
    sig = state["key"].sign(canon, ec.ECDSA(hashes.SHA256()))
    qr_url = f"{PUBLIC_WEB}/verify?r={report_id}&h={digest}&s={_b64u(sig)}"
    patient = await rt.clinical_get(p, f"/patients/{report['patient_id']}")
    pdf = _render_pdf(rep, results, patient, qr_url, digest)
    key = f"{report['patient_id']}/report-{report_id}.pdf"
    state["minio"].put_object(settings.reports_bucket, key, io.BytesIO(pdf), len(pdf), content_type="application/pdf")
    async with rt.db.acquire() as c:
        await c.execute("UPDATE diag.reports SET status='signed', signed_by=$2, signed_at=$3, sha256=$4, signature=$5, pdf_key=$6 WHERE id=$1",
                        report_id, p.display_name, signed_at, digest, _b64u(sig), key)
    await rt.audit(p, "report.sign", "DiagnosticReport", report_id, sha256=digest)
    return {"id": report_id, "status": "signed", "sha256": digest, "verify_url": qr_url}


@app.get("/reports/{report_id}/pdf")
async def report_pdf(report_id: int, request: Request):
    async with rt.db.acquire() as c:
        report = await c.fetchrow("SELECT * FROM diag.reports WHERE id=$1", report_id)
    if not report or not report["pdf_key"]:
        raise HTTPException(404, "signed report not found")
    p = await rt.authorize(request, report["patient_id"])
    data = state["minio"].get_object(settings.reports_bucket, report["pdf_key"]).read()
    await rt.audit(p, "report.download", "DiagnosticReport", report_id)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="report-{report_id}.pdf"'})


@app.get("/verify")
async def verify(r: int, h: str, s: str):
    """Public endpoint behind the QR code; returns no PHI beyond the report title and results summary."""
    async with rt.db.acquire() as c:
        report = await c.fetchrow("SELECT * FROM diag.reports WHERE id=$1", r)
        results = [dict(x) for x in await c.fetch("SELECT * FROM diag.lab_results WHERE id = ANY($1)", report["result_ids"])] if report else []
    if not report or report["status"] != "signed":
        return {"valid": False, "reason": "no signed report with this identifier"}
    canon = _canonical(dict(report), results)
    digest = hashlib.sha256(canon).hexdigest()
    checks = {"hash_matches_qr": digest == h, "hash_matches_record": digest == report["sha256"]}
    try:
        state["key"].public_key().verify(_unb64u(s), canon, ec.ECDSA(hashes.SHA256()))
        checks["signature_valid"] = True
    except (InvalidSignature, ValueError):
        checks["signature_valid"] = False
    valid = all(checks.values())
    await rt.audit(None, "report.verify", "DiagnosticReport", r, outcome="allowed" if valid else "denied", checks=checks)
    return {"valid": valid, "checks": checks, "report_id": r, "title": report["title"], "signed_by": report["signed_by"],
            "signed_at": report["signed_at"], "result_count": len(results), "signer_certificate": state["cert_pem"].decode()}


# ---------------------------------------------------------------- DICOM

async def _stow(instances: list[bytes]) -> None:
    boundary = "omnimedboundary"
    body = b"".join(b"--" + boundary.encode() + b"\r\nContent-Type: application/dicom\r\n\r\n" + i + b"\r\n" for i in instances) + b"--" + boundary.encode() + b"--\r\n"
    resp = await rt.http.post(f"{ORTHANC}/dicom-web/studies", content=body, timeout=300,
                              headers={"Content-Type": f'multipart/related; type="application/dicom"; boundary={boundary}', "Accept": "application/dicom+json"})
    if resp.status_code >= 300:
        raise RuntimeError(f"STOW-RS failed: {resp.status_code} {resp.text[:200]}")


async def _seed_imaging() -> None:
    for attempt in range(60):
        try:
            await rt.http.get(f"{ORTHANC}/system")
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(2)
    async with rt.db.acquire() as c:
        existing = await c.fetchval("SELECT count(*) FROM diag.study_patients")
    if existing:
        return
    for pid, mrn, nodule, desc in (("P0001", "MRN-100001", True, "CT CHEST WITHOUT CONTRAST (synthetic)"),
                                   ("P0002", "MRN-100002", False, "CT CHEST WITHOUT CONTRAST (synthetic)")):
        study, _series, instances = await asyncio.to_thread(chest_ct_series, mrn, 48, 512, 0.7, 2.5, nodule, desc)
        await _stow(instances)
        async with rt.db.acquire() as c:
            await c.execute("INSERT INTO diag.study_patients(study_uid, patient_id, description) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", study, pid, desc)
        log.info("seeded synthetic CT study %s for %s", study, pid)


async def _study_patient(study_uid: str) -> str:
    async with rt.db.acquire() as c:
        pid = await c.fetchval("SELECT patient_id FROM diag.study_patients WHERE study_uid=$1", study_uid)
    if not pid:
        raise HTTPException(404, "study not registered to a patient")
    return pid


async def _authorize_study(request: Request, study_uid: str) -> None:
    """Frame fetches are frequent; cache a positive decision for 60 s per token and study."""
    pid = await _study_patient(study_uid)
    token = request.headers.get("authorization", "")
    key = (hashlib.sha256(token.encode()).hexdigest(), pid)
    if _authz_cache.get(key, 0) > time.time():
        return
    await rt.authorize(request, pid)
    _authz_cache[key] = time.time() + 60


def _tag(meta: dict, tag: str, default=None):
    v = meta.get(tag, {}).get("Value")
    return v[0] if v else default


@app.get("/dicom/studies")
async def studies(request: Request, patient_id: str):
    await rt.authorize(request, patient_id)
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT * FROM diag.study_patients WHERE patient_id=$1 ORDER BY created_at DESC", patient_id)
    out = []
    for r in rows:
        resp = await rt.http.get(f"{ORTHANC}/dicom-web/studies", params={"StudyInstanceUID": r["study_uid"], "includefield": "all"})
        meta = resp.json()[0] if resp.status_code == 200 and resp.json() else {}
        out.append({"study_uid": r["study_uid"], "patient_id": r["patient_id"], "description": r["description"],
                    "study_date": _tag(meta, "00080020"), "modalities": meta.get("00080061", {}).get("Value", []),
                    "instances": _tag(meta, "00201208"), "series": _tag(meta, "00201206")})
    return out


@app.get("/dicom/studies/{study_uid}/series")
async def series(study_uid: str, request: Request):
    await _authorize_study(request, study_uid)
    resp = await rt.http.get(f"{ORTHANC}/dicom-web/studies/{study_uid}/series")
    out = []
    for s in resp.json():
        se = _tag(s, "0020000E")
        inst = (await rt.http.get(f"{ORTHANC}/dicom-web/studies/{study_uid}/series/{se}/instances", params={"includefield": "00200013,00280008"})).json()
        instances = sorted(({"sop_uid": _tag(i, "00080018"), "number": int(_tag(i, "00200013", 0) or 0), "frames": int(_tag(i, "00280008", 1) or 1)} for i in inst), key=lambda x: x["number"])
        first = instances[0]["sop_uid"]
        meta = (await rt.http.get(f"{ORTHANC}/dicom-web/studies/{study_uid}/series/{se}/instances/{first}/metadata")).json()[0]
        spacing = meta.get("00280030", {}).get("Value", [1, 1])
        out.append({"series_uid": se, "description": _tag(s, "0008103E", ""), "modality": _tag(s, "00080060", ""),
                    "rows": _tag(meta, "00280010"), "columns": _tag(meta, "00280011"), "pixel_spacing": spacing,
                    "bits_allocated": _tag(meta, "00280100"), "pixel_representation": _tag(meta, "00280103", 0),
                    "rescale_slope": float(_tag(meta, "00281053", 1)), "rescale_intercept": float(_tag(meta, "00281052", 0)),
                    "window_center": float(_tag(meta, "00281050", 40)), "window_width": float(_tag(meta, "00281051", 400)),
                    "instances": instances})
        _background.add(task := asyncio.create_task(_prewarm(study_uid, se)))
        task.add_done_callback(_background.discard)
    return out


def _strip_multipart(content_type: str, body: bytes) -> list[bytes]:
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[9:].strip('"')
    if not boundary:
        return [body]
    delim = b"--" + boundary.encode()
    parts = []
    for chunk in body.split(delim)[1:]:
        if chunk.startswith(b"--"):
            break
        header_end = chunk.find(b"\r\n\r\n")
        payload = chunk[header_end + 4:]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        parts.append(payload)
    return parts


async def _orthanc_frames(study_uid: str, series_uid: str, sop_uid: str, numbers: list[int]) -> list[bytes]:
    url = f"{ORTHANC}/dicom-web/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}/frames/{','.join(map(str, numbers))}"
    for attempt in range(3):
        try:
            resp = await rt.http.get(url, headers={"Accept": 'multipart/related; type="application/octet-stream"'}, timeout=60)
            break
        except httpx.TransportError:
            # Orthanc closes idle keep-alive sockets; a GET for frames is safe to retry.
            if attempt == 2:
                raise
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "frame retrieval failed")
    return _strip_multipart(resp.headers.get("content-type", ""), resp.content)


@app.get("/dicom/frames/{study_uid}/{series_uid}/{sop_uid}")
async def frames(study_uid: str, series_uid: str, sop_uid: str, request: Request, f: str = Query("1", description="Comma separated 1-based frame numbers")):
    """WADO-RS frame retrieval; returns the requested frames' raw pixel bytes concatenated in order."""
    await _authorize_study(request, study_uid)
    numbers = [int(x) for x in f.split(",") if x.strip()]
    if not numbers or len(numbers) > 256:
        raise HTTPException(422, "request between 1 and 256 frames")
    parts = await _orthanc_frames(study_uid, series_uid, sop_uid, numbers)
    return Response(b"".join(parts), media_type="application/octet-stream", headers={"X-Frame-Count": str(len(parts))})


_layouts: dict[tuple[str, str], tuple[float, dict]] = {}
_volume_locks: dict[str, asyncio.Lock] = {}
_background: set[asyncio.Task] = set()


async def _layout(study_uid: str, series_uid: str) -> dict:
    """Instance order and frame geometry for a series, cached briefly so chunked volume reads stay cheap."""
    hit = _layouts.get((study_uid, series_uid))
    if hit and hit[0] > time.time():
        return hit[1]
    inst = (await rt.http.get(f"{ORTHANC}/dicom-web/studies/{study_uid}/series/{series_uid}/instances",
                              params={"includefield": "00200013,00280008"}, timeout=60)).json()
    if not inst:
        raise HTTPException(404, "series not found")
    ordered = sorted(inst, key=lambda i: int(_tag(i, "00200013", 0) or 0))
    if any(int(_tag(i, "00280008", 1) or 1) != 1 for i in ordered):
        raise HTTPException(422, "volume endpoint supports single-frame instances only; use /dicom/frames")
    sops = [_tag(i, "00080018") for i in ordered]
    meta = (await rt.http.get(f"{ORTHANC}/dicom-web/studies/{study_uid}/series/{series_uid}/instances/{sops[0]}/metadata")).json()[0]
    rows, cols, bits = _tag(meta, "00280010"), _tag(meta, "00280011"), _tag(meta, "00280100")
    if bits != 16 or meta.get("00020010", {}).get("Value", ["1.2.840.10008.1.2.1"])[0] not in ("1.2.840.10008.1.2.1", "1.2.840.10008.1.2"):
        raise HTTPException(422, "volume endpoint supports uncompressed 16-bit pixel data only; use /dicom/frames")
    digest = hashlib.sha256("|".join(sops).encode()).hexdigest()[:16]
    layout = {"sops": sops, "frame_bytes": rows * cols * 2, "rows": rows, "columns": cols, "key": f"{study_uid}/{series_uid}/{digest}.raw"}
    _layouts[(study_uid, series_uid)] = (time.time() + 300, layout)
    return layout


def _build_volume_sync(study_uid: str, series_uid: str, layout: dict) -> None:
    """Assembles every slice into one contiguous object so viewers read large ranges instead of one request per slice.

    Runs on worker threads with a blocking client: httpx's async client reads large bodies several times slower."""
    t0 = time.perf_counter()
    fb = layout["frame_bytes"]
    base = f"{ORTHANC}/dicom-web/studies/{study_uid}/series/{series_uid}/instances"
    accept = {"Accept": 'multipart/related; type="application/octet-stream"'}
    fd, path = tempfile.mkstemp(dir="/tmp", suffix=".raw")
    try:
        with httpx.Client(timeout=60, limits=httpx.Limits(max_connections=16)) as client:
            def one(item: tuple[int, str]) -> None:
                i, sop = item
                for attempt in range(3):
                    try:
                        resp = client.get(f"{base}/{sop}/frames/1", headers=accept)
                        break
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                if resp.status_code != 200:
                    raise RuntimeError(f"frame retrieval for {sop} failed with {resp.status_code}")
                raw = _strip_multipart(resp.headers.get("content-type", ""), resp.content)[0]
                if len(raw) != fb:
                    raise RuntimeError(f"instance {sop} pixel data is {len(raw)} bytes, expected {fb}")
                os.pwrite(fd, raw, i * fb)

            with ThreadPoolExecutor(16) as pool:
                list(pool.map(one, enumerate(layout["sops"])))
        state["minio"].fput_object(settings.volume_bucket, layout["key"], path, content_type="application/octet-stream")
    finally:
        os.close(fd)
        os.unlink(path)
    log.info("built volume %s (%d slices) in %.1fs", layout["key"], len(layout["sops"]), time.perf_counter() - t0)


async def _ensure_volume(study_uid: str, series_uid: str, layout: dict) -> None:
    lock = _volume_locks.setdefault(layout["key"], asyncio.Lock())
    async with lock:
        if not await asyncio.to_thread(_volume_exists, layout["key"]):
            await asyncio.to_thread(_build_volume_sync, study_uid, series_uid, layout)


async def _prewarm(study_uid: str, series_uid: str) -> None:
    try:
        await _ensure_volume(study_uid, series_uid, await _layout(study_uid, series_uid))
    except Exception as exc:  # noqa: BLE001 - viewers fall back to per-frame loading
        log.info("volume prewarm skipped for %s: %s", series_uid, getattr(exc, "detail", exc))


def _volume_exists(key: str) -> bool:
    try:
        state["minio"].stat_object(settings.volume_bucket, key)
        return True
    except Exception:  # noqa: BLE001
        return False


@app.get("/dicom/volume/{study_uid}/{series_uid}")
async def volume(study_uid: str, series_uid: str, request: Request, start: int = Query(0, ge=0), count: int = Query(0, ge=0)):
    """Raw stored pixel values for slices [start, start+count) in instance order, concatenated."""
    await _authorize_study(request, study_uid)
    layout = await _layout(study_uid, series_uid)
    n, fb = len(layout["sops"]), layout["frame_bytes"]
    count = count or n - start
    if start >= n or start + count > n:
        raise HTTPException(416, f"series has {n} slices")
    try:
        await _ensure_volume(study_uid, series_uid, layout)
    except RuntimeError as exc:
        raise HTTPException(502, f"could not assemble series volume: {exc}") from exc
    obj = await asyncio.to_thread(state["minio"].get_object, settings.volume_bucket, layout["key"], start * fb, count * fb)

    def stream():
        try:
            yield from obj.stream(1 << 20)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(stream(), media_type="application/octet-stream",
                             headers={"Content-Length": str(count * fb), "X-Frame-Count": str(count), "X-Frame-Bytes": str(fb), "X-Total-Frames": str(n)})


class SegmentBody(BaseModel):
    study_uid: str
    series_uid: str
    sop_uid: str
    frame: int = 1
    plugin: str = "enclosed-lung-lesion"


@app.post("/dicom/segment")
async def segment(body: SegmentBody, request: Request):
    pid = await _study_patient(body.study_uid)
    p = await rt.authorize(request, pid, roles=["lab", "doctor"])
    plugin = PLUGINS.get(body.plugin)
    if not plugin:
        raise HTTPException(422, f"unknown plugin; available: {list(PLUGINS)}")
    meta = (await rt.http.get(f"{ORTHANC}/dicom-web/studies/{body.study_uid}/series/{body.series_uid}/instances/{body.sop_uid}/metadata")).json()[0]
    rows, cols = _tag(meta, "00280010"), _tag(meta, "00280011")
    slope, intercept = float(_tag(meta, "00281053", 1)), float(_tag(meta, "00281052", 0))
    spacing = [float(x) for x in meta.get("00280030", {}).get("Value", [1, 1])]
    resp = await rt.http.get(f"{ORTHANC}/dicom-web/studies/{body.study_uid}/series/{body.series_uid}/instances/{body.sop_uid}/frames/{body.frame}",
                             headers={"Accept": 'multipart/related; type="application/octet-stream"'})
    raw = _strip_multipart(resp.headers.get("content-type", ""), resp.content)[0]
    dtype = np.int16 if _tag(meta, "00280103", 0) == 1 else np.uint16
    hu = np.frombuffer(raw, dtype=dtype).reshape(rows, cols).astype(np.float32) * slope + intercept
    t0 = time.perf_counter()
    result = await asyncio.to_thread(plugin.run, hu, (spacing[0], spacing[1]))
    result["inference_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    async with rt.db.acquire() as c:
        aid = await c.fetchval("INSERT INTO diag.annotations(instance_id, frame, plugin, result, created_by) VALUES ($1,$2,$3,$4,$5) RETURNING id",
                               body.sop_uid, body.frame, plugin.name, result, p.username)
    await rt.audit(p, "dicom.segment", "ImagingStudy", body.study_uid, findings=len(result["findings"]))
    return {"annotation_id": aid, **result}


@app.get("/dicom/annotations")
async def annotations(request: Request, study_uid: str, sop_uid: str):
    await _authorize_study(request, study_uid)
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT * FROM diag.annotations WHERE instance_id=$1 ORDER BY created_at DESC", sop_uid)
    return [dict(r) for r in rows]


@app.post("/dicom/upload")
async def upload(request: Request, file: UploadFile):
    import pydicom
    data = await file.read()
    try:
        ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"not a DICOM file: {exc}") from exc
    pid = await _patient_for_mrn(str(ds.PatientID))
    p = await rt.authorize(request, pid, action="result", roles=["lab"])
    await _stow([data])
    async with rt.db.acquire() as c:
        await c.execute("INSERT INTO diag.study_patients(study_uid, patient_id, description) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                        str(ds.StudyInstanceUID), pid, str(getattr(ds, "StudyDescription", "Uploaded study")))
    await rt.audit(p, "dicom.upload", "ImagingStudy", str(ds.StudyInstanceUID), patient=pid)
    return {"study_uid": str(ds.StudyInstanceUID), "patient_id": pid}
