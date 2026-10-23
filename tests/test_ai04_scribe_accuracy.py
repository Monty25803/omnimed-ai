"""TC-AI-04: synthetic consultation scripts mapped to ICD-10 (assessment) and RxNorm (plan).

Micro-averaged precision and recall over all cases; both must reach the 95% target."""
from __future__ import annotations

import json
from pathlib import Path

TARGET = 0.95
CASES = json.loads((Path(__file__).parent / "data" / "scribe_cases.json").read_text())


def _score(expected: set, got: set) -> tuple[int, int, int]:
    return len(expected & got), len(got - expected), len(expected - got)


def test_scribe_coding_accuracy(as_user, record):
    doctor = as_user("dr.rao")
    totals = {"icd10": [0, 0, 0], "rxnorm": [0, 0, 0]}
    misses = []
    elapsed = []
    for case in CASES:
        out = doctor.ok("POST", "ai/scribe", json={"transcript": case["transcript"]})
        assert out["soap"]["grounding"]["accepted"], f"{case['id']}: rule-based note failed its own grounding"
        elapsed.append(out["elapsed_ms"])
        codes = out["soap"]["codes"]
        for system in ("icd10", "rxnorm"):
            tp, fp, fn = _score(set(case[system]), set(codes[system]))
            for i, v in enumerate((tp, fp, fn)):
                totals[system][i] += v
            if fp or fn:
                misses.append({"case": case["id"], "system": system, "expected": sorted(case[system]), "got": codes[system]})

    summary = {}
    for system, (tp, fp, fn) in totals.items():
        summary[system] = {"precision": round(tp / (tp + fp), 3) if tp + fp else 1.0, "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0,
                           "tp": tp, "fp": fp, "fn": fn}
    record("TC-AI-04", cases=len(CASES), target=TARGET, **summary, misses=misses, scribe_p50_ms=sorted(elapsed)[len(elapsed) // 2])
    for system, s in summary.items():
        assert s["precision"] >= TARGET and s["recall"] >= TARGET, f"{system} below target: {s}; misses: {json.dumps(misses, indent=1)}"
