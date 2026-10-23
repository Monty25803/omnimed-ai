"""Deterministic grounding engine (zero-hallucination guardrail).

Every clinical entity in a generated proposal must map to a concept id in the
local SNOMED-CT / ICD-10 / RxNorm / LOINC store and respect hard safety
boundaries. Narrative text is additionally scanned for drug-like and
disease-like terms. Any failure rejects the whole proposal.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .extract import STRENGTH_RE, tokenize
from .ontology import Ontology, norm

DRUG_SUFFIXES = ("pril", "sartan", "olol", "dipine", "statin", "cillin", "mycin", "micin", "floxacin", "cycline", "azole", "prazole",
                 "tidine", "xaban", "gatran", "gliptin", "gliflozin", "glutide", "parin", "vir", "mab", "nib", "zumab", "semide",
                 "thiazide", "triptan", "setron", "profen", "fenac", "oxacin", "cef", "penem", "sone", "olone", "formin", "zepam", "zolam")
DISEASE_SUFFIXES = ("itis", "osis", "emia", "aemia", "oma", "pathy", "plasia", "trophy", "algia", "uria", "penia", "cytosis")
DISEASE_WORDS = {"syndrome", "disease", "disorder", "infection", "deficiency", "failure", "insufficiency", "carcinoma", "tumour", "tumor"}
ENGLISH_WHITELIST = {"diagnosis", "prognosis", "differential", "aroma", "diploma", "academia", "stigma", "empathy", "sympathy", "apathy",
                     "basis", "analysis", "hypothesis", "emphasis", "thesis", "dose", "close", "purpose", "response", "reason", "person",
                     "season", "lesson", "sone", "coma", "ultrasound", "worsening", "known", "none", "phone", "alone", "someone", "tone",
                     "stone", "bone", "zone", "gone", "done", "throne", "ozone", "anyone", "everyone", "clone", "cone", "hone"}
FREQUENCIES = {"OD", "BD", "TDS", "QID", "HS", "PRN", "STAT"}
PER_DAY = {"OD": 1, "BD": 2, "TDS": 3, "QID": 4, "HS": 1, "PRN": 1, "STAT": 1}


@dataclass
class Check:
    text: str
    kind: str
    status: str  # grounded | rejected
    system: str = ""
    code: str = ""
    display: str = ""
    reason: str = ""


@dataclass
class GroundingReport:
    accepted: bool = True
    checks: list[Check] = field(default_factory=list)

    def ok(self, **kw) -> None:
        self.checks.append(Check(status="grounded", **kw))

    def reject(self, **kw) -> None:
        self.accepted = False
        self.checks.append(Check(status="rejected", **kw))

    @property
    def rejected(self) -> list[dict]:
        return [asdict(c) for c in self.checks if c.status == "rejected"]

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "checked": len(self.checks), "rejected": self.rejected,
                "grounded": [asdict(c) for c in self.checks if c.status == "grounded"]}


def ground_proposal(proposal: dict, onto: Ontology) -> GroundingReport:
    rep = GroundingReport()
    for dx in proposal.get("assessment", []):
        text = dx.get("display", "")
        pair = onto.diagnosis(text)
        if not pair:
            rep.reject(text=text, kind="diagnosis", reason="no SNOMED-CT concept for this diagnosis")
            continue
        sn, icd = pair
        if dx.get("snomed") and dx["snomed"] != sn.code:
            rep.reject(text=text, kind="diagnosis", reason=f"SNOMED code {dx['snomed']} does not match concept {sn.code}")
            continue
        if dx.get("icd10") and dx["icd10"] != icd.code:
            rep.reject(text=text, kind="diagnosis", reason=f"ICD-10 code {dx['icd10']} does not match concept {icd.code}")
            continue
        rep.ok(text=text, kind="diagnosis", system="SNOMED-CT", code=sn.code, display=sn.display)

    for med in proposal.get("medications", []):
        text = med.get("drug", "")
        c = onto.drug(text)
        if not c:
            rep.reject(text=text, kind="medication", reason="no RxNorm ingredient concept")
            continue
        if med.get("rxcui") and med["rxcui"] != c.code:
            rep.reject(text=text, kind="medication", reason=f"RxCUI {med['rxcui']} does not match {c.code}")
            continue
        strengths = [float(s) for s in (c.attr("strengths_mg") or [])]
        st = med.get("strength_mg")
        if st is not None and strengths and float(st) not in strengths:
            rep.reject(text=f"{text} {st} mg", kind="medication", reason=f"strength not available; valid strengths {strengths}")
            continue
        freq = med.get("frequency")
        if freq and freq not in FREQUENCIES:
            rep.reject(text=f"{text} {freq}", kind="medication", reason="unrecognised frequency")
            continue
        mx = c.attr("max_daily_mg")
        if st is not None and mx is not None and float(st) * PER_DAY.get(freq or "OD", 1) * int(med.get("dose_units", 1)) > float(mx):
            rep.reject(text=f"{text} {st} mg {freq}", kind="medication", reason=f"exceeds maximum daily dose {mx} mg")
            continue
        if med.get("action") not in ("stop", "continue") and not st:
            rep.reject(text=text, kind="medication", reason="new medication without a strength")
            continue
        rep.ok(text=text, kind="medication", system="RxNorm", code=c.code, display=c.display)

    for t in proposal.get("tests", []):
        text = t.get("display", "")
        c = onto.lab(text)
        if not c:
            rep.reject(text=text, kind="test", reason="no LOINC concept for this test")
            continue
        if t.get("loinc") and t["loinc"] != c.code:
            rep.reject(text=text, kind="test", reason=f"LOINC {t['loinc']} does not match {c.code}")
            continue
        rep.ok(text=text, kind="test", system="LOINC", code=c.code, display=c.display)

    for section, text in (proposal.get("narrative") or {}).items():
        scan_narrative(text or "", onto, rep, section)
    return rep


def _looks_like_drug(w: str) -> bool:
    return len(w) > 4 and w not in ENGLISH_WHITELIST and w.endswith(DRUG_SUFFIXES)


def _looks_like_disease(w: str) -> bool:
    return len(w) > 4 and w not in ENGLISH_WHITELIST and w.endswith(DISEASE_SUFFIXES)


def scan_narrative(text: str, onto: Ontology, rep: GroundingReport, section: str = "") -> None:
    """Flags any drug-like or disease-like term in free text that the ontology cannot ground."""
    toks = tokenize(text)
    words = [t.text.lower() for t in toks]
    covered: set[int] = set()
    # Mark every token covered by a known multi-word concept first.
    i = 0
    while i < len(words):
        for n in range(min(onto.max_len, len(words) - i), 0, -1):
            if onto.any(" ".join(words[i:i + n])):
                covered.update(range(i, i + n))
                i += n - 1
                break
        i += 1

    for m in STRENGTH_RE.finditer(text):
        before = [t for t in toks if t.end <= m.start()]
        if not before:
            continue
        # The drug name is the nearest preceding alphabetic token(s).
        j = len(before) - 1
        while j >= 0 and not before[j].text.isalpha():
            j -= 1
        if j < 0:
            continue
        idx = toks.index(before[j])
        name = words[idx]
        two = " ".join(words[max(0, idx - 1):idx + 1])
        if onto.drug(name) or onto.drug(two):
            continue
        if name in {"of", "to", "by", "and", "at", "with", "dose", "take", "daily"}:
            continue
        rep.reject(text=f"{toks[idx].text} {m.group(0)}", kind=f"narrative:{section}", reason="dosed term is not a known RxNorm medication")
        covered.add(idx)

    for idx, w in enumerate(words):
        if idx in covered or not w.isalpha():
            continue
        if _looks_like_drug(w) and not onto.drug(w):
            rep.reject(text=toks[idx].text, kind=f"narrative:{section}", reason="drug-like term not in RxNorm")
        elif _looks_like_disease(w):
            rep.reject(text=toks[idx].text, kind=f"narrative:{section}", reason="disease-like term not in SNOMED-CT")
        elif w in DISEASE_WORDS:
            phrase_ok = any(onto.any(" ".join(words[k:idx + 1])) for k in range(max(0, idx - 3), idx))
            if not phrase_ok and idx - 1 >= 0 and words[idx - 1].isalpha() and words[idx - 1] not in {"the", "a", "of", "no", "any", "this", "your", "known"}:
                rep.reject(text=" ".join(t.text for t in toks[max(0, idx - 1):idx + 1]), kind=f"narrative:{section}", reason="named condition not in SNOMED-CT")


def contains_term(text: str, term: str) -> bool:
    return re.search(r"\b" + re.escape(term.lower()) + r"\b", text.lower()) is not None


__all__ = ["ground_proposal", "scan_narrative", "GroundingReport", "contains_term", "norm"]
