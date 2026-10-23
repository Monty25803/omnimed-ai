"""Dictionary-based clinical entity extraction with negation handling.

Longest-match lookup against the ontology, NegEx-style negation cues, and
regex extraction for vitals and medication sigs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ontology import Concept, Ontology, norm

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+\-/]*(?:\.[0-9]+)?")
NEG_CUES = {"no", "not", "denies", "denied", "without", "never", "negative", "nil", "absent", "free"}
NEG_BREAKS = {"but", "however", "although", "though", "except", "apart"}


@dataclass
class Token:
    text: str
    start: int
    end: int

    @property
    def low(self) -> str:
        return self.text.lower()


@dataclass
class Entity:
    text: str
    start: int
    end: int
    concepts: list[Concept]
    negated: bool = False
    token_index: int = 0
    token_len: int = 1

    @property
    def kinds(self) -> set[str]:
        return {c.kind for c in self.concepts}

    def first(self, system: str | None = None, kind: str | None = None) -> Concept | None:
        for c in self.concepts:
            if (system is None or c.system == system) and (kind is None or c.kind == kind):
                return c
        return None


def tokenize(text: str) -> list[Token]:
    return [Token(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def find_entities(text: str, onto: Ontology, kinds: set[str] | None = None) -> list[Entity]:
    toks = tokenize(text)
    out: list[Entity] = []
    i = 0
    while i < len(toks):
        match = None
        for n in range(min(onto.max_len, len(toks) - i), 0, -1):
            span = toks[i:i + n]
            phrase = norm(" ".join(t.text for t in span))
            concepts = onto.phrases.get(phrase)
            if not concepts:
                continue
            # Short forms (MI, AF, K, CAP) only count when written as acronyms.
            if phrase in onto.short_forms and not all(t.text.isupper() for t in span):
                continue
            if kinds:
                concepts = [c for c in concepts if c.kind in kinds]
                if not concepts:
                    continue
            match = Entity(text[span[0].start:span[-1].end], span[0].start, span[-1].end, concepts, token_index=i, token_len=n)
            break
        if match:
            match.negated = _negated(toks, i, text)
            out.append(match)
            i += match.token_len
        else:
            i += 1
    return out


def _negated(toks: list[Token], i: int, text: str) -> bool:
    for j in range(i - 1, max(-1, i - 6), -1):
        between = text[toks[j].end:toks[j + 1].start]
        if any(ch in between for ch in ".;?!"):
            return False
        w = toks[j].low
        if w in NEG_BREAKS:
            return False
        if w in NEG_CUES:
            return True
    return False


# ---------------------------------------------------------------- vitals

VITAL_PATTERNS = {
    "bp": re.compile(r"\b(?:bp|blood pressure)\D{0,15}?(\d{2,3})\s*(?:/|over)\s*(\d{2,3})", re.I),
    "hr": re.compile(r"\b(?:pulse|heart rate|hr)\D{0,12}?(\d{2,3})\b", re.I),
    "rr": re.compile(r"\b(?:respiratory rate|resp rate|rr|breathing rate)\D{0,12}?(\d{1,2})\b", re.I),
    "temp": re.compile(r"\b(?:temp|temperature)\D{0,12}?(\d{2,3}(?:\.\d)?)\s*(?:°|degrees?)?\s*([cf])?", re.I),
    "spo2": re.compile(r"\b(?:spo2|sp02|saturation|sats|oxygen saturation|o2 sat)\D{0,12}?(\d{2,3})\s*%?", re.I),
    "gcs": re.compile(r"\b(?:gcs|glasgow coma scale)\D{0,8}?(\d{1,2})\b", re.I),
}


def extract_vitals(text: str) -> dict:
    v: dict = {}
    if m := VITAL_PATTERNS["bp"].search(text):
        v["sbp"], v["dbp"] = int(m.group(1)), int(m.group(2))
    for k in ("hr", "rr", "spo2", "gcs"):
        if m := VITAL_PATTERNS[k].search(text):
            v[k] = int(m.group(1))
    if m := VITAL_PATTERNS["temp"].search(text):
        t = float(m.group(1))
        if (m.group(2) or "").lower() == "f" or t > 45:
            t = round((t - 32) * 5 / 9, 1)
        v["temp"] = t
    return v


# ---------------------------------------------------------------- medication sigs

STRENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|micrograms?|g|grams?|units?|iu)\b", re.I)
FREQ_PATTERNS = [
    ("QID", re.compile(r"\b(four times (?:a|per) day|four times daily|qid|q6h|1-1-1-1)\b", re.I)),
    ("TDS", re.compile(r"\b(three times (?:a|per) day|three times daily|thrice daily|tds|tid|t\.d\.s|q8h|1-1-1)\b", re.I)),
    ("BD", re.compile(r"\b(twice (?:a|per) day|twice daily|two times (?:a|per) day|bd|bid|b\.d|q12h|1-0-1)\b", re.I)),
    ("HS", re.compile(r"\b(at night|at bedtime|bedtime|hs|nightly|0-0-1)\b", re.I)),
    ("PRN", re.compile(r"\b(as needed|when needed|as required|when required|prn|sos)\b", re.I)),
    ("STAT", re.compile(r"\b(stat|immediately|right now)\b", re.I)),
    ("OD", re.compile(r"\b(once (?:a|per) day|once daily|daily|every day|every morning|od|q24h|1-0-0)\b", re.I)),
]
DURATION_RE = re.compile(r"\bfor\s+(\d+|one|two|three|four|five|six|seven|ten|fourteen)\s+(days?|weeks?|months?)\b", re.I)
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "fourteen": 14}
STOP_RE = re.compile(r"\b(stop|stopping|discontinue|hold|holding|cease)\b", re.I)
START_RE = re.compile(r"\b(start|starting|prescribe|prescribing|give|giving|begin|add|adding|put you on|take|continue|continuing|switch to)\b", re.I)


@dataclass
class Sig:
    strength: float | None = None
    unit: str | None = None
    frequency: str | None = None
    duration_days: int | None = None
    notes: list[str] = field(default_factory=list)


def parse_sig(window: str) -> Sig:
    s = Sig()
    if m := STRENGTH_RE.search(window):
        val, unit = float(m.group(1)), m.group(2).lower()
        if unit in ("g", "gram", "grams"):
            val, unit = val * 1000, "mg"
        elif unit.startswith("mc") or unit.startswith("micro"):
            val, unit = val / 1000, "mg"
        elif unit.startswith("unit") or unit == "iu":
            unit = "units"
        else:
            unit = "mg"
        s.strength, s.unit = val, unit
    for code, pat in FREQ_PATTERNS:
        if pat.search(window):
            s.frequency = code
            break
    if m := DURATION_RE.search(window):
        n = m.group(1).lower()
        n = WORD_NUM.get(n, None) or int(n)
        unit = m.group(2).lower()
        s.duration_days = n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
    return s


def sentence_of(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind("?", 0, start), text.rfind("!", 0, start))
    rights = [p for p in (text.find(".", end), text.find("\n", end), text.find("?", end)) if p != -1]
    # Decimal points inside numbers are not sentence boundaries.
    while left > 0 and left + 1 < len(text) and text[left - 1].isdigit() and text[left + 1].isdigit():
        left = max(text.rfind(".", 0, left), text.rfind("\n", 0, left))
    right = min(rights) if rights else len(text)
    while right < len(text) and right > 0 and text[right] == "." and right + 1 < len(text) and text[right - 1].isdigit() and text[right + 1].isdigit():
        nxt = [p for p in (text.find(".", right + 1), text.find("\n", right + 1)) if p != -1]
        right = min(nxt) if nxt else len(text)
    return text[left + 1:right].strip()
