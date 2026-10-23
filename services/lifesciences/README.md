# lifesciences

Field operations for pharmaceutical medical representatives (MRs), written in Python with FastAPI. Compliance rules
are enforced here: an MR can only detail a doctor who has opted in, while physically at the clinic, and every
sample handed over is signed and capped.

Gateway prefix: `/api/life` · Port: `8083`

## Endpoints

| Method and path | Purpose |
|---|---|
| `GET /clinics` | Clinics with the doctor's detailing consent |
| `POST /checkins`, `POST /checkins/{id}/end`, `GET /checkins` | Geofenced check-in, valid for 45 minutes |
| `GET /assets`, `GET /assets/{id}`, `POST /engagement` | E-detailing content and engagement tracking (needs an active check-in) |
| `GET /samples/stock`, `POST /samples/distribute`, `GET /samples/ledger` | Sample stock and distribution with the doctor's signature |
| `GET /slots`, `POST /slots/{id}/request`, `POST /slots/{id}/decide` | Doctors publish visiting slots, MRs request, doctors decide |
| `POST /doctor/consent`, `GET /doctor/profile` | Doctor opt-in or opt-out for detailing |
| `GET /analytics` | Visit and engagement summary |

## Rules

- **Check-in:** the distance from the reported location to the clinic must be within the clinic's radius, allowing
  for the reported GPS accuracy, and the doctor must have consented. Rejected check-ins are audited with the distance
  and reason.
- **Samples:** each distribution requires a signature image and is capped at 10 units per doctor, per product, per
  month. The signature's SHA-256 is stored in the ledger and the audit chain.
- **No patient data:** MRs have no access to patient records (enforced by the clinical service).

## Develop

```bash
docker compose -f infra/docker-compose.yml up -d --build lifesciences
```

Geofence unit tests in `tests/test_geo.py` run during the image build.
