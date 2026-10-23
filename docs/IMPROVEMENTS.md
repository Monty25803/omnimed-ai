# OmniMed AI: test results, gaps and recommended next work

Written from the integration run of 23 Oct 2026 on a Windows 11 laptop (12 vCPU / 8 GB given to Docker Desktop),
the whole stack running under `infra/docker-compose.yml`. Raw numbers are in `tests/results/summary.json` after a run.

## 1. Results against the specification test cases

| Case | Spec target | Measured | Result |
|---|---|---|---|
| TC-SYN-01 potassium panic fan-out | < 500 ms to doctor, nurse, pharmacist | p50 57 ms, p95 78 ms, max 81 ms over 10 rounds (SSE, client clock); synapse processing p50 42 ms | Pass |
| TC-AI-04 scribe coding accuracy | > 95% RxNorm and ICD-10 | 20 scripts: ICD-10 P/R 1.00/1.00, RxNorm P/R 1.00/1.00 (first run: ICD-10 recall 0.95, see 2.3) | Pass |
| TC-GRD-02 injected terms | 0% ungrounded terms saved | 5 injection types rejected, 0% survival; forged `grounding.accepted` now refused with 422 (was saved, see 2.1) | Pass after fix |
| TC-DCM-08 500 MB series | < 2.5 s on a LAN | 1000 x 512x512 CT, 500 MiB: warm series volume via gateway 1.04 to 1.97 s best case across runs; per-frame path 8.4 to 11.7 s | Pass on the gateway path; browser path see 2.2 |
| TC-SEC-12 access control | 403 for unauthenticated and cross-facility | 403 for: no token (10 endpoints), 5 forged-token types, 4 cross-boundary personas x 7 PHI endpoints, role boundaries, internal endpoint via gateway; denials present in the audit ledger; chain of 1,414 entries verifies, WORM sample matches | Pass |
| Persona smoke flows | one happy path per persona | doctor, nurse, pharmacist, lab (sign plus public verify plus tamper check), MR (geofence, consent, signed sample), patient, admin | Pass |

Also verified in a real browser: the lab portal renders the 1000-slice series through the new volume path, and the
doctor portal receives the live panic alert 292 ms after the lab message was posted, with spironolactone shown on hold.

Suite totals: 39 integration tests, 18 AI unit tests, 3 lifesciences and 6 diagnostics unit tests (run during image builds),
`tsc` clean for web and mobile, `kubectl kustomize` plus kubeconform clean for base/dev/prod, `terraform validate` clean.

## 2. Defects found by the tests and fixed in this pass

### 2.1 Encounter notes trusted a client-side grounding flag (safety, high)
`POST /patients/{id}/encounters` saved any note whose JSON contained `grounding.accepted: true`. A client could skip
the AI service and store invented drugs and diagnoses. The clinical service now re-runs the guardrail server-side
(`services/clinical/grounding.go`) over the assessment, plan, tests, narrative, history and reported medications, and
fails closed (503) if the AI service is unreachable. Rejections are audited as `blocked`. A NetworkPolicy
`clinical-to-ai` allows this call in Kubernetes.

Same handler: conditions were never added to the problem list because it read a `diagnoses` key the scribe never
produces. It now reads `assessment` and only promotes diagnoses marked `confirmed`.

### 2.2 DICOM loading was bound by per-slice requests
The viewer issued one WADO-RS request per slice. Orthanc's per-frame endpoint tops out near 85 MiB/s even when called
directly, so a 500 MiB series could not load in under about 6 s however the gateway was tuned.

Fix: `GET /dicom/volume/{study}/{series}?start=&count=` assembles a series once into a contiguous 16-bit pixel volume
in object storage and serves slice ranges. Opening the series list starts the build in the background. The viewer
loads four ranges in parallel and falls back to per-frame requests for compressed or multi-frame series. It now keeps
16-bit pixels in memory and converts only the visible slice to HU, halving browser memory (500 MB instead of 1 GB).

Remaining gap, measured: in the browser the same 500 MB takes about 8.6 s. The Next.js `/api/proxy` hop moves
about 96 MB/s versus about 290 MB/s at the gateway, and Docker Desktop's Windows port forwarding costs more on this
machine. Recommendation: route `/api/diag/dicom/volume/*` from the Ingress straight to the gateway with a cookie-to-bearer
step (GET only, SameSite=strict cookie), or serve short-lived signed object-storage URLs to the viewer, so bulk pixels
never pass through Node.

Also fixed on this path: Orthanc drops idle keep-alive sockets, which surfaced as intermittent 500s; frame fetches now
retry. Per-request `httpx` INFO logging (which also wrote study and instance UIDs to logs) is now off.

### 2.3 Scribe missed diagnoses stated as investigation findings
"The ECG shows atrial fibrillation" produced no assessment because "shows" was not a diagnostic cue. Added
shows/showed/confirms/reveals cues plus a unit test. Caveat: the 20 scripts and the vocabulary were written by the same
team, so 100% is optimistic; an independent, clinician-written script set should replace it before any accuracy claim.

### 2.4 Escalation text compounded at every tier
Each escalation prefixed the previous message, producing "ESCALATED ... ESCALATED ... PANIC". Escalations now wrap the
original message once and state the tier.

### 2.5 Production object-storage names were hard-coded
Services used fixed bucket names (`reports`, `pki`, `audit-worm`) that do not match the account-scoped buckets Terraform
creates. Bucket names are now environment variables, and Terraform writes them into the Secrets Manager entry that
External Secrets syncs.

## 3. Known gaps, by priority

### Patient safety
1. **Narrative scan misses unknown drug names without a dose or suffix.** "Start Zorvex BD" is caught only when a
   strength follows or the word has a drug-like suffix. Add a full RxNorm/brand dictionary (licensed) and treat any
   unknown capitalised token next to a frequency or route as a rejection.
2. **OCR on distorted handwriting is unreliable.** Tesseract with upscaling reads the printed sample, but lines with a
   missing frequency are only flagged, not resolved. Keep the mandatory pharmacist confirmation; evaluate a handwriting
   model (for example TrOCR fine-tuned on Indian prescriptions) with a measured error rate.
3. **Rule-based scribe vocabulary is small** (25 ICD-10 diagnoses, 36 RxNorm drugs, 32 LOINC tests). Load licensed SNOMED CT (India
   edition), RxNorm and LOINC releases into `ai.concepts` and re-run TC-AI-04 on a larger independent corpus.
4. **Lesion assist is a threshold-based demonstrator**, not a validated model. It must stay labelled as such until a
   trained, regulator-cleared model replaces it.
5. **Alert escalation has no acknowledgement-by-proxy.** If the nurse completes the ECG task, the doctor's alert stays
   open. Link task completion to alert closure where clinically appropriate.

### Security and compliance
1. **Vault runs in dev mode** (in-memory, root token). Production needs Vault in HA with the KMS auto-unseal key
   Terraform creates, Kubernetes auth for each service account, and per-service transit policies.
2. **App-to-S3 credentials are static IAM user keys** so the same code runs on MinIO. Move to IRSA (web identity)
   in production.
3. **Production Keycloak hostname and database are placeholders** (`auth.omnimed.example.org`, `keycloak` database on
   RDS that must be created). Add MFA for clinical roles and short access-token lifetimes for the mobile client.
4. **Rate limiting is per client IP.** Every browser request reaches the gateway from the web container, so all users
   share one bucket. Key the limiter on the bearer token or a forwarded client IP set by the Ingress.
5. **Audit WORM mirror** writes each entry with Object Lock in COMPLIANCE mode (MinIO locally, S3 in production), but
   anyone with database and bucket-admin rights in the same account could still rebuild both. Add a periodic external
   anchor of the chain head (for example to a separate AWS account) for tamper evidence.
6. **The derived pixel volume** is stored unencrypted in local MinIO. In production it lands in the KMS-encrypted DICOM
   bucket; add a lifecycle rule and evict volumes when studies are deleted or amended.

### Integrations still simulated
- ABDM: ABHA verification, HIP linking and consent artefacts are local simulations, not the NHA sandbox.
- SMS/WhatsApp reminders go to an outbox table; no provider is connected.
- Speech-to-text needs `WHISPER_URL`; without it the scribe accepts typed transcripts only.
- The LLM path is optional (`LLM_API_KEY`) and always behind the grounding guardrail; it was not exercised in this run.

### Operations
- No metrics or tracing yet. Add OpenTelemetry to all services; alert on synapse latency, guardrail rejection rate,
  escalation counts and audit mirror lag.
- Diagnostics runs a single uvicorn worker; volume builds and DICOM proxying compete with report signing. Split imaging
  into its own deployment or add workers.
- Test runs leave open alerts that escalate after 120 s. The suite should acknowledge its own alerts.

## 4. Suggested next features
1. Medication reconciliation at admission and discharge, using the grounded RxNorm list and pharmacy dispensing history.
2. Sepsis bundle tracker driven by the existing qSOFA/MEWS scores, with time-zero and bundle-element timers.
3. Progressive imaging: show the first slice range immediately (0.2 to 0.5 s measured) and stream the rest, plus prior-study comparison.
4. Patient-side FHIR export and ABDM push of discharge summaries once the NHA sandbox is integrated.
5. Antimicrobial stewardship view: culture results against active antibiotics, using the same synapse rules engine.
6. Offline-first mobile eMAR with queued, signed administrations that sync when the ward Wi-Fi returns.
7. MR compliance reporting: monthly sample caps and consent status exported for the regulatory audit.
