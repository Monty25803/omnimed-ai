from __future__ import annotations

import base64
import hashlib

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from omnicommon.runtime import create_app, rt, utcnow

from .geo import geofence_decision, haversine_m, relevance

SESSION_MINUTES = 45
MONTHLY_SAMPLE_CAP = 10  # per doctor per product, a conservative stand-in for regulatory sample limits

app = create_app("lifesciences", "OmniMed Life Sciences Service")


@app.get("/clinics")
async def clinics(request: Request):
    await rt.authorize(request, roles=["mr", "doctor"])
    async with rt.db.acquire() as c:
        rows = await c.fetch("""SELECT c.*, s.display_name AS doctor_name, coalesce(dp.detailing_consent,false) AS detailing_consent
            FROM life.clinics c JOIN clinical.staff s ON s.username=c.doctor LEFT JOIN life.doctor_profiles dp ON dp.doctor=c.doctor ORDER BY c.id""")
    return [dict(r) for r in rows]


class CheckinBody(BaseModel):
    clinic_id: str
    lat: float
    lon: float
    accuracy_m: float | None = None


@app.post("/checkins")
async def checkin(body: CheckinBody, request: Request):
    p = await rt.authorize(request, roles=["mr"])
    async with rt.db.acquire() as c:
        clinic = await c.fetchrow("SELECT * FROM life.clinics WHERE id=$1", body.clinic_id)
        if not clinic:
            raise HTTPException(404, "clinic not found")
        consent = await c.fetchval("SELECT detailing_consent FROM life.doctor_profiles WHERE doctor=$1", clinic["doctor"])
        dist = haversine_m(body.lat, body.lon, clinic["lat"], clinic["lon"])
        ok, reason = geofence_decision(dist, clinic["radius_m"], body.accuracy_m)
        if ok and not consent:
            ok, reason = False, "doctor has not opted in to detailing visits"
        await c.execute("UPDATE life.checkins SET ended_at=now() WHERE rep=$1 AND ended_at IS NULL", p.username)
        cid = await c.fetchval("""INSERT INTO life.checkins(rep, clinic_id, lat, lon, accuracy_m, distance_m, accepted, ended_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7, CASE WHEN $7 THEN NULL ELSE now() END) RETURNING id""",
                               p.username, body.clinic_id, body.lat, body.lon, body.accuracy_m, dist, ok)
    await rt.audit(p, "mr.checkin", "Clinic", body.clinic_id, outcome="allowed" if ok else "denied", distance_m=round(dist, 1), reason=reason)
    if not ok:
        raise HTTPException(403, {"checkin_id": cid, "accepted": False, "distance_m": round(dist, 1), "reason": reason})
    return {"checkin_id": cid, "accepted": True, "distance_m": round(dist, 1), "reason": reason, "valid_minutes": SESSION_MINUTES}


async def _active_checkin(c, rep: str, checkin_id: int):
    row = await c.fetchrow("""SELECT * FROM life.checkins WHERE id=$1 AND rep=$2 AND accepted AND ended_at IS NULL
        AND started_at > now() - make_interval(mins => $3)""", checkin_id, rep, SESSION_MINUTES)
    if not row:
        raise HTTPException(403, "no active geo-verified check-in; check in at the clinic first")
    return row


@app.post("/checkins/{checkin_id}/end")
async def end_checkin(checkin_id: int, request: Request):
    p = await rt.authorize(request, roles=["mr"])
    async with rt.db.acquire() as c:
        await c.execute("UPDATE life.checkins SET ended_at=now() WHERE id=$1 AND rep=$2 AND ended_at IS NULL", checkin_id, p.username)
    return {"checkin_id": checkin_id, "ended": True}


@app.get("/checkins")
async def my_checkins(request: Request):
    p = await rt.authorize(request, roles=["mr"])
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT * FROM life.checkins WHERE rep=$1 ORDER BY started_at DESC LIMIT 50", p.username)
    return [dict(r) for r in rows]


@app.get("/samples/stock")
async def sample_stock(request: Request):
    p = await rt.authorize(request, roles=["mr"])
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT * FROM life.sample_stock WHERE rep=$1 ORDER BY product", p.username)
    return [dict(r) for r in rows]


class SampleBody(BaseModel):
    checkin_id: int
    product: str
    qty: int = Field(gt=0, le=20)
    signature_png: str  # data URL captured on-device


@app.post("/samples/distribute")
async def distribute(body: SampleBody, request: Request):
    p = await rt.authorize(request, roles=["mr"])
    if not body.signature_png.startswith("data:image/png;base64,") or len(body.signature_png) < 200:
        raise HTTPException(422, "a captured doctor signature (PNG data URL) is required")
    try:
        sig_bytes = base64.b64decode(body.signature_png.split(",", 1)[1], validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "signature is not valid base64 PNG") from exc
    if not sig_bytes.startswith(b"\x89PNG"):
        raise HTTPException(422, "signature is not a PNG image")
    async with rt.db.acquire() as c, c.transaction():
        chk = await _active_checkin(c, p.username, body.checkin_id)
        clinic = await c.fetchrow("SELECT * FROM life.clinics WHERE id=$1", chk["clinic_id"])
        stock = await c.fetchrow("SELECT * FROM life.sample_stock WHERE rep=$1 AND product=$2 FOR UPDATE", p.username, body.product)
        if not stock or stock["qty"] < body.qty:
            raise HTTPException(409, "insufficient sample stock")
        month_total = await c.fetchval("""SELECT coalesce(sum(qty),0) FROM life.sample_ledger WHERE doctor=$1 AND product=$2
            AND at > date_trunc('month', now())""", clinic["doctor"], body.product)
        if month_total + body.qty > MONTHLY_SAMPLE_CAP:
            raise HTTPException(409, f"monthly sample cap reached for this doctor ({month_total}/{MONTHLY_SAMPLE_CAP})")
        await c.execute("UPDATE life.sample_stock SET qty=qty-$3 WHERE rep=$1 AND product=$2", p.username, body.product, body.qty)
        digest = hashlib.sha256(sig_bytes).hexdigest()
        lid = await c.fetchval("""INSERT INTO life.sample_ledger(rep, clinic_id, checkin_id, doctor, product, lot, qty, signature_png, signature_sha256)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
                               p.username, clinic["id"], body.checkin_id, clinic["doctor"], body.product, stock["lot"], body.qty, body.signature_png, digest)
    await rt.audit(p, "mr.sample", "SampleLedger", lid, doctor=clinic["doctor"], product=body.product, qty=body.qty, signature_sha256=digest)
    return {"ledger_id": lid, "lot": stock["lot"], "remaining": stock["qty"] - body.qty, "signature_sha256": digest}


@app.get("/samples/ledger")
async def ledger(request: Request):
    p = await rt.authorize(request, roles=["mr", "doctor"])
    col = "rep" if p.role == "mr" else "doctor"
    async with rt.db.acquire() as c:
        rows = await c.fetch(f"SELECT id, rep, clinic_id, checkin_id, doctor, product, lot, qty, signature_sha256, at FROM life.sample_ledger WHERE {col}=$1 ORDER BY at DESC", p.username)
    return [dict(r) for r in rows]


@app.get("/assets")
async def assets(request: Request, doctor: str | None = None):
    """Only medical-legal approved assets are ever served, ranked by non-PHI relevance when a doctor is given."""
    p = await rt.authorize(request, roles=["mr", "doctor"])
    target = p.username if p.role == "doctor" else doctor
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT id, title, kind, product, tags, mlr_ref, source, jsonb_array_length(slides) AS slide_count FROM life.assets WHERE status='approved'")
        prof = await c.fetchrow("SELECT * FROM life.doctor_profiles WHERE doctor=$1", target) if target else None
    out = []
    for r in rows:
        d = dict(r)
        d["relevance"] = relevance(d["tags"], prof["interests"], prof["publication_topics"], prof["specialty"], prof["practice_mix"]) if prof else None
        out.append(d)
    out.sort(key=lambda x: -(x["relevance"] or 0))
    return out


@app.get("/assets/{asset_id}")
async def asset(asset_id: str, request: Request):
    await rt.authorize(request, roles=["mr", "doctor"])
    async with rt.db.acquire() as c:
        row = await c.fetchrow("SELECT * FROM life.assets WHERE id=$1 AND status='approved'", asset_id)
    if not row:
        raise HTTPException(404, "asset not found or not approved for use")
    return dict(row)


class EngagementBody(BaseModel):
    checkin_id: int
    asset_id: str
    slide: int = Field(ge=0)
    dwell_ms: int = Field(ge=0, le=3_600_000)


@app.post("/engagement")
async def engagement(body: EngagementBody, request: Request):
    p = await rt.authorize(request, roles=["mr"])
    async with rt.db.acquire() as c:
        await _active_checkin(c, p.username, body.checkin_id)
        ok = await c.fetchval("SELECT EXISTS(SELECT 1 FROM life.assets WHERE id=$1 AND status='approved')", body.asset_id)
        if not ok:
            raise HTTPException(404, "asset not approved")
        eid = await c.fetchval("INSERT INTO life.engagement(rep, checkin_id, asset_id, slide, dwell_ms) VALUES ($1,$2,$3,$4,$5) RETURNING id",
                               p.username, body.checkin_id, body.asset_id, body.slide, body.dwell_ms)
    return {"id": eid}


@app.get("/analytics")
async def analytics(request: Request):
    p = await rt.authorize(request, roles=["mr", "doctor"])
    col = "e.rep" if p.role == "mr" else "cl.doctor"
    async with rt.db.acquire() as c:
        per_slide = await c.fetch(f"""SELECT e.asset_id, a.title, e.slide, count(*) AS views, round(avg(e.dwell_ms)) AS avg_dwell_ms
            FROM life.engagement e JOIN life.assets a ON a.id=e.asset_id JOIN life.checkins ck ON ck.id=e.checkin_id JOIN life.clinics cl ON cl.id=ck.clinic_id
            WHERE {col}=$1 GROUP BY e.asset_id, a.title, e.slide ORDER BY e.asset_id, e.slide""", p.username)
        visits = await c.fetchval(f"""SELECT count(*) FROM life.checkins ck JOIN life.clinics cl ON cl.id=ck.clinic_id
            WHERE {'ck.rep' if p.role == 'mr' else 'cl.doctor'}=$1 AND ck.accepted""", p.username)
        rejected = await c.fetchval(f"""SELECT count(*) FROM life.checkins ck JOIN life.clinics cl ON cl.id=ck.clinic_id
            WHERE {'ck.rep' if p.role == 'mr' else 'cl.doctor'}=$1 AND NOT ck.accepted""", p.username)
    return {"verified_visits": visits, "rejected_checkins": rejected, "slides": [dict(r) for r in per_slide]}


@app.get("/slots")
async def slots(request: Request, doctor: str | None = None):
    p = await rt.authorize(request, roles=["mr", "doctor"])
    target = p.username if p.role == "doctor" else doctor
    async with rt.db.acquire() as c:
        rows = await c.fetch("""SELECT * FROM life.rep_slots WHERE starts_at > now() AND ($1::text IS NULL OR doctor=$1)
            AND ($2 = 'doctor' OR status='open' OR rep=$3) ORDER BY starts_at LIMIT 100""", target, p.role, p.username)
    return [dict(r) for r in rows]


class SlotRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=200)


@app.post("/slots/{slot_id}/request")
async def request_slot(slot_id: int, body: SlotRequest, request: Request):
    """Reps can only request slots the doctor published for detailing; patient schedules are never touched."""
    p = await rt.authorize(request, roles=["mr"])
    async with rt.db.acquire() as c, c.transaction():
        slot = await c.fetchrow("SELECT * FROM life.rep_slots WHERE id=$1 FOR UPDATE", slot_id)
        if not slot or slot["status"] != "open":
            raise HTTPException(409, "slot not open")
        consent = await c.fetchval("SELECT detailing_consent FROM life.doctor_profiles WHERE doctor=$1", slot["doctor"])
        if not consent:
            raise HTTPException(403, "doctor has not opted in to detailing visits")
        pending = await c.fetchval("SELECT count(*) FROM life.rep_slots WHERE doctor=$1 AND rep=$2 AND status IN ('requested','approved') AND starts_at > now()", slot["doctor"], p.username)
        if pending >= 1:
            raise HTTPException(409, "one pending or approved visit per doctor at a time")
        await c.execute("UPDATE life.rep_slots SET status='requested', rep=$2, topic=$3 WHERE id=$1", slot_id, p.username, body.topic)
    await rt.audit(p, "mr.slot.request", "RepSlot", slot_id, doctor=slot["doctor"])
    return {"id": slot_id, "status": "requested"}


class SlotDecision(BaseModel):
    approve: bool


@app.post("/slots/{slot_id}/decide")
async def decide_slot(slot_id: int, body: SlotDecision, request: Request):
    p = await rt.authorize(request, roles=["doctor"])
    status = "approved" if body.approve else "open"
    async with rt.db.acquire() as c:
        row = await c.fetchrow("""UPDATE life.rep_slots SET status=$3, rep=CASE WHEN $3='open' THEN NULL ELSE rep END,
            topic=CASE WHEN $3='open' THEN NULL ELSE topic END WHERE id=$1 AND doctor=$2 AND status='requested' RETURNING *""", slot_id, p.username, status)
    if not row:
        raise HTTPException(404, "no pending request on this slot")
    await rt.audit(p, "mr.slot.decide", "RepSlot", slot_id, approved=body.approve)
    return dict(row)


class ConsentBody(BaseModel):
    consent: bool


@app.post("/doctor/consent")
async def doctor_consent(body: ConsentBody, request: Request):
    p = await rt.authorize(request, roles=["doctor"])
    async with rt.db.acquire() as c:
        await c.execute("""INSERT INTO life.doctor_profiles(doctor, specialty, detailing_consent, consent_at) VALUES ($1, coalesce($3,'General'), $2, now())
            ON CONFLICT (doctor) DO UPDATE SET detailing_consent=$2, consent_at=now()""", p.username, body.consent, p.staff.get("specialty"))
    await rt.audit(p, "mr.consent", "DoctorProfile", p.username, consent=body.consent)
    return {"doctor": p.username, "detailing_consent": body.consent, "at": utcnow()}


@app.get("/doctor/profile")
async def doctor_profile(request: Request):
    p = await rt.authorize(request, roles=["doctor"])
    async with rt.db.acquire() as c:
        row = await c.fetchrow("SELECT * FROM life.doctor_profiles WHERE doctor=$1", p.username)
    return dict(row) if row else {"doctor": p.username, "detailing_consent": False}
