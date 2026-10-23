"""In-memory index over the local terminology subset (SNOMED-CT, ICD-10, RxNorm, LOINC)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_norm_re = re.compile(r"[^a-z0-9.+\-/ ]+")


def norm(s: str) -> str:
    return " ".join(_norm_re.sub(" ", s.lower()).split())


@dataclass(frozen=True)
class Concept:
    system: str
    code: str
    display: str
    kind: str
    attrs: tuple = ()

    def attr(self, key, default=None):
        return dict(self.attrs).get(key, default)


@dataclass
class Ontology:
    concepts: dict[tuple[str, str], Concept] = field(default_factory=dict)
    phrases: dict[str, list[Concept]] = field(default_factory=dict)
    short_forms: set[str] = field(default_factory=set)
    max_len: int = 1

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "Ontology":
        o = cls()
        for r in rows:
            attrs = tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in (r["attrs"] or {}).items()))
            c = Concept(r["system"], r["code"], r["display"], r["kind"], attrs)
            o.concepts[(c.system, c.code)] = c
            for s in {r["display"], *r["synonyms"]}:
                key = norm(s)
                if not key:
                    continue
                o.phrases.setdefault(key, [])
                if c not in o.phrases[key]:
                    o.phrases[key].append(c)
                if len(key) <= 3:
                    o.short_forms.add(key)
                o.max_len = max(o.max_len, len(key.split()))
        return o

    def get(self, system: str, code: str) -> Concept | None:
        return self.concepts.get((system, code))

    def lookup(self, phrase: str, kind: str | None = None, system: str | None = None) -> list[Concept]:
        out = self.phrases.get(norm(phrase), [])
        return [c for c in out if (kind is None or c.kind == kind) and (system is None or c.system == system)]

    def diagnosis(self, phrase: str) -> tuple[Concept, Concept] | None:
        """Returns (SNOMED, ICD-10) for a diagnosis phrase, or None if it cannot be grounded."""
        sn = self.lookup(phrase, kind="diagnosis", system="SNOMED-CT")
        if not sn:
            return None
        icd = self.get("ICD-10", sn[0].attr("icd10"))
        return (sn[0], icd) if icd else None

    def drug(self, phrase: str) -> Concept | None:
        c = self.lookup(phrase, kind="drug", system="RxNorm")
        return c[0] if c else None

    def lab(self, phrase: str) -> Concept | None:
        c = self.lookup(phrase, system="LOINC")
        return c[0] if c else None

    def any(self, phrase: str) -> bool:
        return bool(self.phrases.get(norm(phrase)))
