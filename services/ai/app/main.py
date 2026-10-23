from __future__ import annotations

import copy
import os
import time

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from omnicommon.runtime import create_app, log, rt

from . import llm
from .differential import rank
from .embed import embed, to_pgvector
from .extract import extract_vitals, find_entities
from .grounding import ground_proposal
from .lims_ranges import classify
from .ocr import ocr_lines, parse_prescription_lines, sample_image
from .ontology import Ontology
from .scribe import build_soap, collect_codes, render_narrative
from .translator import explain_results, translate_text

WHISPER_URL = os.getenv("WHISPER_URL", "").rstrip("/")
state: dict = {"onto": None}


async def on_start() -> None:
    async with rt.db.acquire() as c:
        rows = await c.fetch("SELECT system, code, display, synonyms, kind, attrs FROM ai.concepts")
        state["onto"] = Ontology.from_rows([dict(r) for r in rows])
        pending = await c.fetch("SELECT id, title, body FROM ai.guidelines WHERE embedding IS NULL")
        for g in pending:
            await c.execute("UPDATE ai.guidelines SET embedding=$2::vector WHERE id=$1", g["id"], to_pgvector(embed(g["title"] + " " + g["body"])))
    log.info("ontology loaded: %d concepts, %d phrases; embedded %d guidelines", len(state["onto"].concepts), len(state["onto"].phrases), len(pending))


app = create_app("ai", "OmniMed AI and Grounding Service", on_start)


def onto() -> Ontology:
    return state["onto"]


def _proposal(soap: dict) -> dict:
    return {"assessment": soap["assessment"], "medications": soap["plan"]["medications"], "tests": soap["plan"]["tests"], "narrative": soap["narrative"]}


def finalize_rule_based(soap: dict) -> tuple[dict, dict]:
    """Removes any item the grounding engine rejects and lists it for clinician completion."""
    rep = ground_proposal(_proposal(soap), onto())
    incomplete = []
    if not rep.accepted:
        bad = {r["text"].lower() for r in rep.rejected}
        keep_meds = []
        for m in soap["plan"]["medications"]:
            names = {m["drug"].lower(), f"{m['drug']} {m.get('strength_mg')} mg".lower(), f"{m['drug']} {m.get('strength_mg')} mg {m.get('frequency')}".lower()}
            if names & bad:
                reason = next(r["reason"] for r in rep.rejected if r["text"].lower() in names)
                incomplete.append({"item": m["drug"], "reason": reason})
            else:
                keep_meds.append(m)
        soap["plan"]["medications"] = keep_meds
        soap["assessment"] = [a for a in soap["assessment"] if a["display"].lower() not in bad]
        soap["plan"]["tests"] = [t for t in soap["plan"]["tests"] if t["display"].lower() not in bad]
        soap["narrative"] = render_narrative(soap)
        soap["codes"] = collect_codes(soap)
        rep = ground_proposal(_proposal(soap), onto())
    soap["incomplete"] = incomplete
    return soap, rep.as_dict()


def soap_from_draft(base: dict, draft: dict) -> dict:
    """Binds a model draft to concept ids; only called after the draft passed grounding."""
    o = onto()
    soap = copy.deepcopy(base)
    soap["assessment"] = []
    for d in draft.get("assessment", []):
        sn, icd = o.diagnosis(d["display"])
        soap["assessment"].append({"display": sn.display, "snomed": sn.code, "icd10": icd.code, "certainty": d.get("certainty", "provisional")})
    soap["plan"]["medications"] = []
    for m in draft.get("medications", []):
        c = o.drug(m["drug"])
        soap["plan"]["medications"].append({"drug": c.display, "rxcui": c.code, "strength_mg": m.get("strength_mg"), "unit": "mg", "frequency": m.get("frequency"),
                                            "duration_days": m.get("duration_days"), "action": m.get("action", "start"), "dose_units": m.get("dose_units", 1)})
    soap["plan"]["tests"] = [{"display": o.lab(t["display"]).display, "loinc": o.lab(t["display"]).code} for t in draft.get("tests", [])]
    soap["narrative"] = {k: draft.get("narrative", {}).get(k, "") for k in ("S", "O", "A", "P")}
    soap["codes"] = collect_codes(soap)
    return soap


async def _log(source: str, accepted: bool, rejected: list, generator: str) -> None:
    async with rt.db.acquire() as c:
        await c.execute("INSERT INTO ai.grounding_log(source, accepted, rejected, generator) VALUES ($1,$2,$3,$4)", source, accepted, rejected, generator)


class ScribeBody(BaseModel):
    transcript: str
    patient_id: str | None = None
    draft_override: dict | None = None


async def _scribe(transcript: str, p, draft_override: dict | None) -> dict:
    t0 = time.perf_counter()
    base = build_soap(transcript, onto())
    draft, generator, rejected_draft = None, "rule-based", None
    if draft_override is not None:
        draft, source = draft_override, "external-draft"
    elif llm.enabled():
        try:
            draft, source = await llm.draft(llm.SCRIBE_PROMPT, transcript), "llm"
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM draft failed, using rule-based scribe: %s", exc)
    if draft is not None:
        rep = ground_proposal(draft, onto())
        await _log("scribe", rep.accepted, rep.rejected, source)
        if rep.accepted:
            soap, grounding, generator = soap_from_draft(base, draft), rep.as_dict(), source
            soap["incomplete"] = []
        else:
            rejected_draft = {"source": source, "rejected": rep.rejected}
            await rt.audit(p, "ai.grounding.reject", "ClinicalNote", "", source=source, rejected=[r["text"] for r in rep.rejected])
            soap, grounding = finalize_rule_based(base)
            generator = "rule-based-fallback"
    else:
        soap, grounding = finalize_rule_based(base)
        await _log("scribe", grounding["accepted"], grounding["rejected"], generator)
    soap["grounding"] = grounding
    return {"soap": soap, "generator": generator, "rejected_draft": rejected_draft, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.post("/scribe")
async def scribe(body: ScribeBody, request: Request):
    p = await rt.authorize(request, body.patient_id, roles=["doctor"])
    if len(body.transcript) > 60_000:
        raise HTTPException(413, "transcript too long")
    out = await _scribe(body.transcript, p, body.draft_override)
    await rt.audit(p, "ai.scribe", "ClinicalNote", body.patient_id or "", generator=out["generator"])
    return out


@app.post("/scribe/audio")
async def scribe_audio(request: Request, file: UploadFile, patient_id: str | None = None):
    p = await rt.authorize(request, patient_id, roles=["doctor"])
    if not WHISPER_URL:
        raise HTTPException(503, "No speech-to-text engine configured. Set WHISPER_URL to an OpenAI-compatible Whisper server (for example faster-whisper-server) or paste the transcript.")
    audio = await file.read()
    resp = await rt.http.post(f"{WHISPER_URL}/v1/audio/transcriptions", files={"file": (file.filename or "audio.wav", audio, file.content_type or "audio/wav")},
                              data={"model": os.getenv("WHISPER_MODEL", "Systran/faster-whisper-small"), "response_format": "json"}, timeout=300)
    if resp.status_code >= 300:
        raise HTTPException(502, f"speech-to-text failed: {resp.text[:200]}")
    transcript = resp.json().get("text", "")
    out = await _scribe(transcript, p, None)
    out["transcript"] = transcript
    return out


class TextBody(BaseModel):
    text: str


@app.post("/codify")
async def codify(body: TextBody, request: Request):
    await rt.authorize(request, roles=["doctor", "pharmacist", "lab", "nurse"])
    ents = find_entities(body.text, onto())
    out = []
    for e in ents:
        codes = [{"system": c.system, "code": c.code, "display": c.display, "kind": c.kind} for c in e.concepts]
        for c in e.concepts:
            if c.system == "SNOMED-CT" and c.attr("icd10"):
                icd = onto().get("ICD-10", c.attr("icd10"))
                if icd and all(x["code"] != icd.code for x in codes):
                    codes.append({"system": "ICD-10", "code": icd.code, "display": icd.display, "kind": icd.kind})
        out.append({"text": e.text, "start": e.start, "end": e.end, "negated": e.negated, "codes": codes})
    return {"entities": out, "vitals": extract_vitals(body.text)}


class GuardBody(BaseModel):
    proposal: dict


@app.post("/guardrail/check")
async def guardrail(body: GuardBody, request: Request):
    p = await rt.authorize(request, roles=["doctor", "pharmacist", "lab", "nurse", "admin"])
    rep = ground_proposal(body.proposal, onto())
    await _log("guardrail", rep.accepted, rep.rejected, "external")
    if not rep.accepted:
        await rt.audit(p, "ai.grounding.reject", "Proposal", "", rejected=[r["text"] for r in rep.rejected])
    return rep.as_dict()


class DxBody(BaseModel):
    patient_id: str
    symptoms_text: str = ""


@app.post("/differential")
async def differential(body: DxBody, request: Request):
    p = await rt.authorize(request, body.patient_id, roles=["doctor"])
    ctx = await rt.clinical_get(p, f"/patients/{body.patient_id}")
    findings, negated = set(), set()
    for e in find_entities(body.symptoms_text, onto(), kinds={"finding"}):
        c = e.first(kind="finding")
        (negated if e.negated else findings).add(c.code)
    lv = ctx.get("latest_vitals") or {}
    vitals = {k: lv.get(k) for k in ("hr", "sbp", "rr", "temp", "spo2", "gcs")}
    vitals.update({k: v for k, v in extract_vitals(body.symptoms_text).items() if v is not None})
    labs = {code: o["value"] for code, o in (ctx.get("latest_labs") or {}).items() if o.get("value") is not None}
    history = {c["icd10"] for c in ctx.get("conditions", []) if c.get("icd10")}
    ranked = rank(findings, negated, vitals, labs, history)
    async with rt.db.acquire() as c:
        for d in ranked:
            rows = await c.fetch("""SELECT title, source, url, 1 - (embedding <=> $2::vector) AS similarity FROM ai.guidelines
                WHERE condition_icd10=$1 ORDER BY embedding <=> $2::vector LIMIT 2""", d["icd10"], to_pgvector(embed(d["display"] + " " + " ".join(d["evidence"]))))
            d["guidelines"] = [dict(r) for r in rows]
            sn = onto().diagnosis(d["display"])
            d["snomed"] = sn[0].code if sn else None
        related = await c.fetch("SELECT title, source, url, 1 - (embedding <=> $1::vector) AS similarity FROM ai.guidelines ORDER BY embedding <=> $1::vector LIMIT 3",
                                to_pgvector(embed(body.symptoms_text or "clinical assessment")))
    rep = ground_proposal({"assessment": [{"display": d["display"], "icd10": d["icd10"]} for d in ranked]}, onto())
    await rt.audit(p, "ai.differential", "Patient", body.patient_id, top=[d["icd10"] for d in ranked])
    return {"engine": "weighted-evidence v1", "differential": ranked, "related_guidelines": [dict(r) for r in related],
            "inputs": {"findings": sorted(findings), "negated": sorted(negated), "vitals": vitals, "labs": labs, "history": sorted(history)},
            "grounding": rep.as_dict(),
            "disclaimer": "Decision support only. Scores are heuristic evidence weights, not calibrated probabilities."}


class TranslateBody(BaseModel):
    text: str = ""
    lang: str = "en"
    patient_id: str | None = None


@app.post("/translate")
async def translate(body: TranslateBody, request: Request):
    p = await rt.authorize(request, body.patient_id)
    try:
        out: dict = {"lang": body.lang}
        if body.text:
            out["text"] = translate_text(body.text, body.lang)
        if body.patient_id:
            ctx = await rt.clinical_get(p, f"/patients/{body.patient_id}")
            results = []
            for code, obs in (ctx.get("latest_labs") or {}).items():
                c = onto().get("LOINC", code)
                if not c or obs.get("value") is None or obs.get("source") != "lab":
                    continue
                attrs = dict(c.attrs)
                results.append({"analyte": c.display, "value": obs["value"], "unit": obs.get("unit") or attrs.get("unit"),
                                "ref_low": attrs.get("low"), "ref_high": attrs.get("high"), "flag": classify(obs["value"], attrs), "at": obs["effective_at"]})
            out["results"] = explain_results(results, body.lang)
            out["conditions"] = [translate_text(c["display"], body.lang) for c in ctx.get("conditions", [])]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return out


@app.get("/ocr/sample")
async def ocr_sample():
    return Response(sample_image(), media_type="image/png")


@app.post("/ocr")
async def ocr(request: Request, file: UploadFile):
    await rt.authorize(request, roles=["pharmacist", "doctor"])
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "image too large")
    t0 = time.perf_counter()
    lines = ocr_lines(data)
    items = parse_prescription_lines(lines, onto())
    return {"engine": "tesseract-5 + fuzzy RxNorm match", "lines": lines, "items": items, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


@app.post("/ocr/text")
async def ocr_text(body: TextBody, request: Request):
    await rt.authorize(request, roles=["pharmacist", "doctor"])
    lines = [{"text": ln, "ocr_confidence": 1.0} for ln in body.text.splitlines() if ln.strip()]
    return {"engine": "typed text + fuzzy RxNorm match", "items": parse_prescription_lines(lines, onto())}


@app.get("/ontology/search")
async def ontology_search(q: str, request: Request, system: str | None = None, limit: int = 20):
    await rt.authorize(request)
    ql = q.lower().strip()
    out = []
    for c in onto().concepts.values():
        if system and c.system != system:
            continue
        if ql in c.display.lower() or ql == c.code.lower():
            out.append({"system": c.system, "code": c.code, "display": c.display, "kind": c.kind})
        if len(out) >= limit:
            break
    return out
