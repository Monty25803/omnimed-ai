# web

Next.js 14 (App Router) portals for every persona, plus the public report verification page.

## Pages

| Route | Persona | Main features |
|---|---|---|
| `/` | everyone | Persona picker and sign-in |
| `/doctor` | Doctor | Patient timeline, ambient scribe (typed or audio) with grounding results, prescribing with safety alerts and stock, differential, imaging, appointments, MR slot decisions |
| `/nurse` | Nurse | Ward grid with early-warning scores, vitals entry, barcode eMAR, tasks |
| `/pharmacy` | Pharmacist | Prescription queue and dispensing, substitutes, inventory sync, in-radius stock, refill risk and reminders, prescription OCR |
| `/pathology` | Lab | Analyzer message ingest, results, report drafting and signing with PDF download, DICOM upload and viewer |
| `/mr` | Medical rep | Clinic check-in, e-detailing, sample hand-over with the doctor's signature, slot requests |
| `/patient` | Patient | Results explained in English or Hindi, consents, health locker, appointments |
| `/audit` | Auditor | Audit ledger and chain verification |
| `/verify` | public | Checks a signed report's hash and signature from its QR code |

For staff personas, the shared header (`components/Shell.tsx`) shows live critical alerts over server-sent events,
with acknowledgement.

## How requests flow

The browser never holds a token. `app/api/login` exchanges credentials with Keycloak and stores the access and
refresh tokens in `httpOnly`, `SameSite=strict` cookies. Every API call goes to `app/api/proxy/[...path]`, which
refreshes the token when needed and forwards the request to the gateway with a bearer header.

Bulk imaging also uses this proxy, which makes it about 3× slower than calling the gateway directly. See
[docs/IMPROVEMENTS.md](../../docs/IMPROVEMENTS.md) for the recommended fix.

## DICOM viewer

`components/DicomViewer.tsx` loads a series through the volume endpoint in four parallel ranges, keeps the raw
16-bit pixels, and converts only the visible slice to Hounsfield units. It supports window presets, a HU probe and
lesion-assist overlays, and falls back to per-slice loading for compressed series.

## Develop

```bash
npm install
npm run dev        # http://localhost:3000, expects the compose stack for the gateway and Keycloak
npm run typecheck
```

| Variable | Default |
|---|---|
| `GATEWAY_URL` | `http://localhost:8000` |
| `KEYCLOAK_URL` | `http://localhost:8080` |
| `COOKIE_SECURE` | `false`; set `true` behind HTTPS |
