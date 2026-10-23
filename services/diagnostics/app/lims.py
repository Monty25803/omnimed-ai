"""Analyzer message parsers: ASTM E1394 and HL7 v2 ORU^R01.

Both parsers emit the same neutral shape so the ingest pipeline does not care
which protocol an analyzer speaks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ParsedResult:
    code: str  # ASTM local test code or LOINC
    code_system: str  # "astm" | "loinc"
    value: float
    unit: str
    analyzer_flag: str = ""
    raw_ref: str = ""


@dataclass
class ParsedMessage:
    protocol: str
    message_id: str
    instrument: str
    mrn: str
    results: list[ParsedResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _records(raw: str) -> list[str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Strip ASTM framing (STX, frame number, ETX/ETB + checksum) when present.
    raw = re.sub(r"\x02\d?", "", raw)
    raw = re.sub(r"[\x03\x17][0-9A-Fa-f]{2}", "", raw)
    return [line.strip() for line in raw.split("\n") if line.strip()]


def _num(s: str) -> float | None:
    s = s.strip().lstrip("<>=")
    try:
        return float(s)
    except ValueError:
        return None


def parse_astm(raw: str) -> ParsedMessage:
    msg = ParsedMessage(protocol="ASTM", message_id="", instrument="", mrn="")
    for rec in _records(raw):
        fields = rec.split("|")
        rtype = fields[0][-1:] if fields[0] else ""
        if rtype == "H":
            # H|\^&|||Instrument^Version|...|||||||P|1|timestamp
            msg.instrument = (fields[4].split("^")[0] if len(fields) > 4 else "") or "ASTM analyzer"
            control_id = fields[2] if len(fields) > 2 else ""
            msg.message_id = control_id or (fields[-1] if len(fields) > 12 else datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"))
        elif rtype == "P":
            # Practice-assigned ID (P-3) is the MRN; fall back to lab-assigned ID (P-4).
            pid = fields[3] if len(fields) > 3 else ""
            if not pid and len(fields) > 4:
                pid = fields[4]
            msg.mrn = pid.split("^")[0].strip()
        elif rtype == "R":
            if len(fields) < 4:
                msg.errors.append(f"short R record: {rec}")
                continue
            test = fields[2].split("^")
            code = next((c for c in reversed(test) if c), "")
            value = _num(fields[3])
            if not code or value is None:
                msg.errors.append(f"unparseable result: {rec}")
                continue
            msg.results.append(ParsedResult(
                code=code.upper(), code_system="astm", value=value,
                unit=fields[4] if len(fields) > 4 else "",
                raw_ref=fields[5] if len(fields) > 5 else "",
                analyzer_flag=fields[6] if len(fields) > 6 else "",
            ))
    if not msg.mrn:
        msg.errors.append("no patient identifier in P record")
    return msg


def parse_hl7(raw: str) -> ParsedMessage:
    msg = ParsedMessage(protocol="HL7v2", message_id="", instrument="", mrn="")
    for seg in _records(raw):
        f = seg.split("|")
        name = f[0]
        if name == "MSH":
            # MSH-1 is the field separator itself, so MSH-n sits at index n-1.
            msg.instrument = f[2].split("^")[0] if len(f) > 2 else "HL7 analyzer"
            msg.message_id = f[9] if len(f) > 9 else ""
            if len(f) > 8 and not f[8].startswith("ORU"):
                msg.errors.append(f"unsupported message type {f[8]}")
        elif name == "PID":
            ids = f[3].split("~") if len(f) > 3 else []
            msg.mrn = ids[0].split("^")[0].strip() if ids else ""
        elif name == "OBX":
            if len(f) < 6:
                msg.errors.append(f"short OBX: {seg}")
                continue
            if f[2] not in ("NM", "SN"):
                continue
            ident = f[3].split("^")
            code = ident[0]
            system = "loinc" if len(ident) > 2 and ident[2].upper() in ("LN", "LOINC") else "astm"
            value = _num(f[5].replace("^", ""))
            if value is None:
                msg.errors.append(f"non-numeric OBX value: {seg}")
                continue
            msg.results.append(ParsedResult(
                code=code if system == "loinc" else code.upper(), code_system=system, value=value,
                unit=f[6].split("^")[0] if len(f) > 6 else "",
                raw_ref=f[7] if len(f) > 7 else "",
                analyzer_flag=f[8] if len(f) > 8 else "",
            ))
    if not msg.mrn:
        msg.errors.append("no PID-3 patient identifier")
    return msg


def classify(value: float, ref: dict) -> str:
    """Flag against reference and panic ranges; the analyzer's own flag is never trusted."""
    pl, ph = ref.get("panic_low"), ref.get("panic_high")
    lo, hi = ref.get("low"), ref.get("high")
    if pl is not None and value <= pl:
        return "LL"
    if ph is not None and value >= ph:
        return "HH"
    if lo is not None and value < lo:
        return "L"
    if hi is not None and value > hi:
        return "H"
    return "N"


def build_hl7_oru(mrn: str, results: list[tuple[str, str, float, str]], message_id: str) -> str:
    """Helper used by tests and the portal's sample generator."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    segs = [
        f"MSH|^~\\&|OMNI-ANALYZER|OMNILAB|OMNIMED|F1|{ts}||ORU^R01|{message_id}|P|2.5.1",
        f"PID|1||{mrn}^^^OMNIMED^MR",
        f"OBR|1|||CHEM^Chemistry panel|||{ts}",
    ]
    for i, (loinc, name, value, unit) in enumerate(results, start=1):
        segs.append(f"OBX|{i}|NM|{loinc}^{name}^LN||{value}|{unit}|||||F|||{ts}")
    return "\r".join(segs)
