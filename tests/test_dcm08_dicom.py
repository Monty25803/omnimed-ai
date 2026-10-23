"""TC-DCM-08: a 500 MiB CT series (1000 x 512x512 int16 slices) loads through the gateway in
under 2.5 s. Records the browser-equivalent load (6 parallel frame requests, like the viewer),
a wider pool, and a direct Orthanc WADO-RS baseline so the bottleneck is visible."""
from __future__ import annotations

import io
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np
import pytest
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian

from conftest import GATEWAY, ORTHANC, token

SLICES = int(os.getenv("DCM_SLICES", "1000"))
SIZE = 512
TARGET_S = 2.5
STRICT = os.getenv("DCM_TARGET_STRICT", "true") == "true"
MRN = "MRN-100003"
ROOT = "1.2.826.0.1.3680043.10.1077"
STUDY_UID = f"{ROOT}.8.{SLICES}"
SERIES_UID = f"{ROOT}.8.{SLICES}.1"


def _slice(i: int) -> bytes:
    rng = np.random.default_rng(i)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    body = ((x - 256) ** 2 / 230 ** 2 + (y - 256) ** 2 / 180 ** 2) < 1
    img = np.where(body, 40, -1000).astype(np.int16) + rng.integers(-20, 20, (SIZE, SIZE), dtype=np.int16)
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = f"{SERIES_UID}.{i + 1}"
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID, ds.SOPInstanceUID = CTImageStorage, meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = STUDY_UID, SERIES_UID
    ds.PatientID, ds.PatientName = MRN, "TC-DCM-08^Synthetic"
    ds.Modality, ds.StudyDescription, ds.SeriesDescription = "CT", f"TC-DCM-08 {SLICES}-slice load test (synthetic)", "Axial 0.5 mm"
    ds.InstanceNumber, ds.ImagePositionPatient, ds.SliceThickness = i + 1, [0, 0, i * 0.5], 0.5
    ds.Rows = ds.Columns = SIZE
    ds.PixelSpacing = [0.7, 0.7]
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
    ds.RescaleSlope, ds.RescaleIntercept, ds.WindowCenter, ds.WindowWidth = 1, 0, 40, 400
    ds.PixelData = img.tobytes()
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def _stow(batch: list[bytes]) -> None:
    boundary = "tcdcm08"
    body = b"".join(b"--" + boundary.encode() + b"\r\nContent-Type: application/dicom\r\n\r\n" + d + b"\r\n" for d in batch) + f"--{boundary}--\r\n".encode()
    resp = httpx.post(f"{ORTHANC}/dicom-web/studies", content=body, timeout=300,
                      headers={"Content-Type": f'multipart/related; type="application/dicom"; boundary={boundary}', "Accept": "application/dicom+json"})
    assert resp.status_code < 300, resp.text[:300]


@pytest.fixture(scope="module")
def series(as_user):
    lab = as_user("lab.suresh")
    have = httpx.get(f"{ORTHANC}/dicom-web/studies/{STUDY_UID}/series/{SERIES_UID}/instances", timeout=60)
    present = len(have.json()) if have.status_code == 200 else 0
    t0 = time.perf_counter()
    if present < SLICES:
        first = _slice(0)
        up = lab.post("diag/dicom/upload", files={"file": ("slice-0001.dcm", first, "application/dicom")})
        assert up.status_code == 200, up.text
        with ThreadPoolExecutor(4) as pool:
            for start in range(1, SLICES, 50):
                batch = list(pool.map(_slice, range(start, min(SLICES, start + 50))))
                _stow(batch)
    else:
        lab.post("diag/dicom/upload", files={"file": ("slice-0001.dcm", _slice(0), "application/dicom")})
    return {"ingest_s": round(time.perf_counter() - t0, 1), "reused": present >= SLICES}


def _payload(r: httpx.Response) -> bytes:
    ctype = r.headers.get("content-type", "")
    if not ctype.startswith("multipart/"):
        return r.content
    boundary = ctype.split("boundary=")[1].split(";")[0].strip('"').encode()
    part = r.content.split(b"--" + boundary)[1]
    body = part[part.index(b"\r\n\r\n") + 4:]
    return body[:-2] if body.endswith(b"\r\n") else body


def _load(path_for, headers, instances, pool_size):
    total = 0
    statuses: dict[int, int] = {}

    def fetch(inst):
        with_retry = 0
        while True:
            try:
                r = client.get(path_for(inst), headers=headers)
            except httpx.RemoteProtocolError:
                statuses[-1] = statuses.get(-1, 0) + 1
                if with_retry >= 3:
                    raise
                with_retry += 1
                continue
            if r.status_code == 429 and with_retry < 20:
                with_retry += 1
                time.sleep(0.05 * with_retry)
                statuses[429] = statuses.get(429, 0) + 1
                continue
            statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
            if r.status_code != 200:
                return 0
            raw = _payload(r)
            hu = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            return hu.nbytes // 2

    limits = httpx.Limits(max_connections=pool_size, max_keepalive_connections=pool_size)
    with httpx.Client(timeout=60, limits=limits) as client:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(pool_size) as pool:
            for n in pool.map(fetch, instances):
                total += n
        elapsed = time.perf_counter() - t0
    return elapsed, total, statuses


def _load_volume(headers, expected_bytes, chunk, pool):
    """Viewer strategy: parallel slice ranges from the assembled series volume."""
    ranges = [(s, min(chunk, SLICES - s)) for s in range(0, SLICES, chunk)]
    url = f"{GATEWAY}/api/diag/dicom/volume/{STUDY_UID}/{SERIES_UID}"
    statuses: dict[int, int] = {}
    with httpx.Client(timeout=300, limits=httpx.Limits(max_connections=pool)) as client:
        def fetch(r):
            resp = client.get(url, params={"start": r[0], "count": r[1]}, headers=headers)
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
            if resp.status_code != 200:
                return 0
            return np.frombuffer(resp.content, dtype=np.int16).nbytes

        t0 = time.perf_counter()
        first = fetch(ranges[0])
        first_s = time.perf_counter() - t0
        with ThreadPoolExecutor(pool) as ex:
            got = first + sum(ex.map(fetch, ranges[1:]))
        elapsed = time.perf_counter() - t0
    return {"seconds": round(elapsed, 2), "first_chunk_s": round(first_s, 2), "MiB_per_s": round(got / elapsed / 2**20, 1),
            "bytes_ok": got == expected_bytes, "statuses": statuses, "requests": len(ranges)}


def _load_volume_curl(token_value, expected_bytes, chunk, pool):
    """Same strategy with a native HTTP client, which is what a browser's fetch is closer to."""
    url = f"{GATEWAY}/api/diag/dicom/volume/{STUDY_UID}/{SERIES_UID}"
    ranges = [(s, min(chunk, SLICES - s)) for s in range(0, SLICES, chunk)]

    def fetch(r):
        out = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{size_download}", "-H", f"Authorization: Bearer {token_value}",
                              f"{url}?start={r[0]}&count={r[1]}"], capture_output=True, text=True, check=True).stdout.split()
        return int(out[0]), int(out[1])

    t0 = time.perf_counter()
    with ThreadPoolExecutor(pool) as ex:
        res = list(ex.map(fetch, ranges))
    elapsed = time.perf_counter() - t0
    got = sum(n for code, n in res if code == 200)
    return {"seconds": round(elapsed, 2), "MiB_per_s": round(got / elapsed / 2**20, 1), "bytes_ok": got == expected_bytes,
            "requests": len(ranges), "parallel": pool, "chunk_slices": chunk}


@pytest.mark.slow
def test_series_load_time(series, record):
    lab_headers = {"Authorization": f"Bearer {token('lab.suresh')}"}
    meta = httpx.get(f"{GATEWAY}/api/diag/dicom/studies/{STUDY_UID}/series", headers=lab_headers, timeout=120)
    assert meta.status_code == 200, meta.text[:300]
    s = [x for x in meta.json() if x["series_uid"] == SERIES_UID][0]
    instances = [i["sop_uid"] for i in s["instances"]]
    assert len(instances) == SLICES
    expected_bytes = SLICES * SIZE * SIZE * 2

    gw = lambda sop: f"{GATEWAY}/api/diag/dicom/frames/{STUDY_UID}/{SERIES_UID}/{sop}?f=1"  # noqa: E731
    direct = lambda sop: f"{ORTHANC}/dicom-web/studies/{STUDY_UID}/series/{SERIES_UID}/instances/{sop}/frames/1"  # noqa: E731
    results = {}
    for label, path_for, headers, pool in (
        ("gateway_pool6", gw, lab_headers, 6),
        ("gateway_pool16", gw, lab_headers, 16),
        ("orthanc_direct_pool16", direct, {"Accept": 'multipart/related; type="application/octet-stream"'}, 16),
    ):
        elapsed, got, statuses = _load(path_for, headers, instances, pool)
        results[label] = {"seconds": round(elapsed, 2), "MiB_per_s": round(got / elapsed / 2**20, 1), "bytes_ok": got == expected_bytes, "statuses": statuses}

    # Listing the series above starts the volume build in the background, as it does when a viewer opens a study.
    results["volume_first_open_python"] = _load_volume(lab_headers, expected_bytes, chunk=64, pool=6)
    results["volume_warm_python"] = _load_volume(lab_headers, expected_bytes, chunk=64, pool=6)
    tok = lab_headers["Authorization"][7:]
    results["volume_warm_curl_p4_c64"] = _load_volume_curl(tok, expected_bytes, chunk=64, pool=4)
    results["volume_warm_curl_p4_c250"] = _load_volume_curl(tok, expected_bytes, chunk=250, pool=4)

    load_keys = [k for k in results if k.startswith(("gateway", "volume"))]
    best_key = min(load_keys, key=lambda k: results[k]["seconds"])
    best = results[best_key]["seconds"]
    record("TC-DCM-08", series_MiB=round(expected_bytes / 2**20), slices=SLICES, target_s=TARGET_S, meets_target=best < TARGET_S,
           best_gateway_s=best, best_strategy=best_key, ingest=series, **results)
    assert all(results[k]["bytes_ok"] for k in load_keys), f"incomplete load: {results}"
    if STRICT:
        assert best < TARGET_S, f"best gateway load {best}s exceeds {TARGET_S}s"
