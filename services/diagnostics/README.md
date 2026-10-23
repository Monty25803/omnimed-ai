# diagnostics

Laboratory and imaging service, written in Python with FastAPI. It turns analyzer messages into coded results,
detects critical values and publishes them to the clinical synapse. It also signs reports and serves DICOM images
from Orthanc.

Gateway prefix: `/api/diag` · Port: `8082`

## Endpoints

| Method and path | Purpose |
|---|---|
| `POST /lims/hl7`, `POST /lims/astm` | Ingest an HL7 v2 ORU or ASTM E1394 message; critical values publish `lab.panic` |
| `GET /lims/catalog` | Test catalogue with LOINC codes and reference ranges |
| `GET /results`, `GET /results/recent` | Results for a patient, or recent results for the lab |
| `POST /reports`, `GET /reports` | Draft and list reports |
| `POST /reports/{id}/sign` | Sign with the lab's ECDSA P-256 key and render a PDF whose QR code carries the signature |
| `GET /reports/{id}/pdf` | Download the signed PDF |
| `GET /verify` | Public check that a report's hash and signature are intact (no login) |
| `GET /dicom/studies`, `GET /dicom/studies/{study}/series` | Browse studies; listing series starts building the volume cache |
| `GET /dicom/frames/{study}/{series}/{sop}` | One slice as raw pixels (fallback path) |
| `GET /dicom/volume/{study}/{series}?start=&count=` | A range of slices from the cached series volume (fast path) |
| `POST /dicom/upload` | Upload DICOM files to Orthanc |
| `POST /dicom/segment`, `GET /dicom/annotations` | Lesion assist (threshold-based demonstrator) and saved annotations |

## How the DICOM volume path works

Per-slice WADO-RS requests are limited by Orthanc to about 85 MiB/s. Instead, the first time a series is opened, the
service fetches every slice once with 16 threads, writes a contiguous 16-bit volume to the `VOLUME_BUCKET`, and from
then on serves byte ranges of it. Volumes are keyed by a hash of the series' instance list, so a changed series gets
a new volume. Only uncompressed, single-frame, 16-bit series use this path; others return 422 and the viewer falls
back to per-slice loading.

## Configuration

| Variable | Default |
|---|---|
| `ORTHANC_URL` | `http://localhost:8042` |
| `PUBLIC_WEB_URL` | `http://localhost:3000` (used in report QR codes) |
| `REPORTS_BUCKET`, `PKI_BUCKET`, `VOLUME_BUCKET` | `reports`, `pki`, `volumes` |
| Shared settings | `DATABASE_URL`, `NATS_URL`, `CLINICAL_URL`, `INTERNAL_TOKEN`, `MINIO_*` (see `services/common`) |

## Develop

```bash
docker compose -f infra/docker-compose.yml up -d --build diagnostics
```

Unit tests in `tests/` (HL7 and ASTM parsing, critical-value classification, segmentation) run during the image build.
