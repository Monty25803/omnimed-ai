# clinical

The clinical core, written in Go (chi, pgx, NATS, MinIO, go-redis). It holds the patient record and enforces access
control; the other services ask it for authorization decisions and send it their audit events.

Gateway prefix: `/api/clinical` · Port: `8081`

## Responsibilities

| Area | What it does | Source |
|---|---|---|
| Patients and FHIR | Patient list, timeline, FHIR R4 `Bundle` export, capability statement | `patients.go`, `fhir.go` |
| Encounters | Saves scribe notes after re-grounding them through the AI service; fails closed | `patients.go`, `grounding.go` |
| Prescriptions | Allergies and cross-reactivity, drug interactions (hard or soft), renal and potassium limits from the latest labs, maximum daily dose, duplicate orders; hold, release, dispense | `rx.go` |
| Pharmacy | Inventory, generic substitutes, in-radius stock across pharmacies, refill risk, reminder outbox | `pharmacy.go` |
| Ward and eMAR | Ward grid, vitals with MEWS and qSOFA, barcode administration with wrong-patient blocking | `ward.go`, `emar.go` |
| Alerts | SSE stream, acknowledgement, tiered escalation when nobody responds | `alerts.go` |
| Synapse | Consumes lab panics and red alerts, holds risky drugs, raises STAT tasks | `synapse.go` |
| Access control | JWT verification via JWKS, ABAC decisions, `/internal/authz` for other services | `auth.go`, `abac.go` |
| PHI encryption | Envelope encryption with Vault transit data keys | `kms.go` |
| Audit | Hash-chained ledger in Postgres, mirrored to an Object Lock bucket, verification endpoint | `audit.go` |
| Patient services | ABHA verification and registries (simulated ABDM), health locker, consents, appointments | `locker.go`, `scheduling.go` |

## Access rules

Decisions are made per patient and per action (`read`, `write`, `dispense`, `administer`, `result`):

- **Medical representatives and administrators** never see patient data.
- **Patients** see only their own record.
- **Clinical writes** require an active shift.
- **Same-facility staff** can read. To write, lab and pharmacy staff may act on any patient in their facility; everyone
  else must be on the patient's care team.
- **Other facilities** can read only while the patient's consent for that facility is active.

Every decision, allowed or denied, is written to the audit ledger.

## Synapse rules

The service subscribes to the JetStream stream `SYNAPSE` (`lab.panic`, `lab.result`, `ward.redalert`, `rx.created`,
`audit.record`) and answers `pharmacy.stock.query` requests.

| Panic (LOINC and flag) | Drug classes held | Task raised |
|---|---|---|
| Potassium high (`2823-3` HH) | Potassium-sparing diuretics, potassium supplements; ACE inhibitors and ARBs flagged for review | STAT ECG |
| Potassium low (`2823-3` LL) | none | STAT ECG |
| Glucose low (`2345-7` LL) | Insulin, sulfonylureas | Bedside glucose |
| INR high (`6301-6` HH) | Anticoagulants | Bleeding assessment |
| Platelets low (`777-3` LL) | Anticoagulants, antiplatelets | Bleeding assessment |
| Troponin high (`10839-9` HH) | none | STAT ECG |

Alerts go to every care-team member and escalate to the next tier after `ESCALATION_TIMEOUT_SECONDS`.

## Configuration

| Variable | Default |
|---|---|
| `PORT` | `8081` |
| `DATABASE_URL` | `postgres://omnimed:omnimed@localhost:5432/omnimed` |
| `REDIS_URL`, `NATS_URL` | local defaults |
| `JWKS_URL`, `ISSUER_SUFFIX` | Keycloak realm `omnimed` |
| `INTERNAL_TOKEN` | `dev-internal-token` (shared with the Python services) |
| `VAULT_ADDR`, `VAULT_TOKEN` | `http://localhost:8200`, `root` |
| `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_SECURE` | local MinIO |
| `AUDIT_BUCKET` | `audit-worm` |
| `AI_URL` | `http://localhost:8084` |
| `ESCALATION_TIMEOUT_SECONDS` | `120` |

## Develop

```bash
# rebuild and restart only this service (unit tests run during the image build)
docker compose -f infra/docker-compose.yml up -d --build clinical

# or run the tests directly
cd services/clinical && go test ./...
```
