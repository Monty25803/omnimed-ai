# tests

End-to-end tests for the test cases in the specification. They run in a container on the compose network and go
through the public gateway, as a real client would. The only exceptions are measurements that deliberately call a
backend component directly.

| File | Test case | What it checks |
|---|---|---|
| `test_syn01_panic.py` | TC-SYN-01 | 10 potassium panics; doctor, nurse and pharmacist each receive the SSE alert; drug held, ECG task raised; p95 under 500 ms |
| `test_ai04_scribe_accuracy.py` | TC-AI-04 | 20 scripted consultations in `data/scribe_cases.json`; ICD-10 and RxNorm precision and recall at least 0.95 |
| `test_grd02_grounding.py` | TC-GRD-02 | Five kinds of injected, unsupported terms are rejected and never saved; a forged approval is refused with 422 |
| `test_dcm08_dicom.py` | TC-DCM-08 | Generates a synthetic 1000-slice, 500 MiB CT series and times loading it several ways; best path under 2.5 s |
| `test_sec12_access.py` | TC-SEC-12 | Missing and forged tokens, cross-facility and cross-role access, internal endpoints, and that denials are audited |
| `test_smoke_personas.py` | Smoke | One main flow for each persona |

## Run

From the repository root, with the stack running:

```bash
# fast suite, about 15 seconds
docker compose -f infra/docker-compose.yml --profile test run --rm tests pytest -q -m "not slow"

# only the DICOM test; the first run uploads the series (about 2 minutes), later runs reuse it
docker compose -f infra/docker-compose.yml --profile test run --rm tests pytest -q -m slow

# everything
docker compose -f infra/docker-compose.yml --profile test run --rm tests
```

Metrics are written to `results/summary.json` (not committed).

To record the DICOM timings without failing on the 2.5 s target (for example on a slow machine), add
`-e DCM_TARGET_STRICT=false` after `run --rm`. `DCM_SLICES` changes the series size (default 1000).

## Notes

- The panic test acknowledges nothing, so its alerts escalate after `ESCALATION_TIMEOUT_SECONDS`. This is expected.
- The scribe cases and the vocabulary were written by the same team, so the accuracy score is optimistic.
