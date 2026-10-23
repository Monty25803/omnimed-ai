"""Synthetic DICOM generation (chest CT phantom and large multi-frame series).

No real imaging data ships with OmniMed; these phantoms exercise the full
Orthanc -> DICOMweb -> WebGL viewer -> segmentation path.
"""
from __future__ import annotations

import io
from datetime import datetime

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

CT_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.2"
MF_GRAYSCALE_WORD_SC = "1.2.840.10008.5.1.4.1.1.7.3"


def _base(mrn: str, study_uid: str, series_uid: str, sop_class: str, description: str) -> Dataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    sop_uid = generate_uid()
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sop_uid
    ds.PatientID = mrn
    ds.PatientName = "SYNTHETIC^PHANTOM"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.StudyDescription = description
    ds.SeriesDescription = description
    now = datetime.now()
    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.AccessionNumber = study_uid[-8:]
    ds.StudyID = "1"
    ds.SeriesNumber = 1
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.RescaleIntercept = -1024
    ds.RescaleSlope = 1
    ds.WindowCenter = -600
    ds.WindowWidth = 1500
    return ds


def _to_bytes(ds: Dataset) -> bytes:
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


def chest_phantom_slice(size: int, spacing: float, z_mm: float, nodule: tuple[float, float, float, float] | None, rng: np.random.Generator) -> np.ndarray:
    """Returns one axial slice in HU."""
    c = (np.arange(size) - size / 2 + 0.5) * spacing
    x, y = np.meshgrid(c, c)
    img = np.full((size, size), -1000.0)
    body = (x / 165) ** 2 + (y / 115) ** 2 <= 1
    img[body] = -90  # subcutaneous fat
    img[(x / 155) ** 2 + (y / 105) ** 2 <= 1] = 40  # muscle / soft tissue
    taper = max(0.0, 1 - (abs(z_mm) / 140) ** 2)
    for cx in (-68, 68):
        lung = ((x - cx) / (52 * (0.7 + 0.3 * taper))) ** 2 + ((y + 5) / (82 * (0.7 + 0.3 * taper))) ** 2 <= 1
        img[lung] = -850
    heart = ((x - 18) / 45) ** 2 + ((y + 15) / 38) ** 2 <= 1
    img[heart & (z_mm < 40)] = 45
    img[(x / 14) ** 2 + ((y - 75) / 14) ** 2 <= 1] = 700  # vertebral body
    img[(x / 10) ** 2 + ((y + 40) / 10) ** 2 <= 1] = 60  # aorta
    if nodule:
        nx, ny, nz, r = nodule
        dz = z_mm - nz
        if abs(dz) < r:
            rr = np.sqrt(r * r - dz * dz)
            img[(x - nx) ** 2 + (y - ny) ** 2 <= rr * rr] = 25
    img += rng.normal(0, 12, img.shape)
    return img


def chest_ct_series(mrn: str, slices: int = 48, size: int = 512, spacing: float = 0.7, thickness: float = 2.5,
                    nodule: bool = True, description: str = "CT CHEST WITHOUT CONTRAST (synthetic)") -> tuple[str, str, list[bytes]]:
    rng = np.random.default_rng(abs(hash(mrn)) % (2**32))
    study_uid, series_uid = generate_uid(), generate_uid()
    nod = (-72.0, -20.0, 0.0, 7.5) if nodule else None
    out = []
    for i in range(slices):
        z = (i - slices / 2) * thickness
        hu = chest_phantom_slice(size, spacing, z, nod, rng)
        stored = np.clip(hu + 1024, 0, 65535).astype(np.uint16)
        ds = _base(mrn, study_uid, series_uid, CT_IMAGE_STORAGE, description)
        ds.Modality = "CT"
        ds.InstanceNumber = i + 1
        ds.Rows = ds.Columns = size
        ds.PixelSpacing = [spacing, spacing]
        ds.SliceThickness = thickness
        ds.ImagePositionPatient = [-size * spacing / 2, -size * spacing / 2, z]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SliceLocation = z
        ds.PixelData = stored.tobytes()
        out.append(_to_bytes(ds))
    return study_uid, series_uid, out


def multiframe_series(mrn: str, frames: int, size: int = 512) -> tuple[str, str, bytes]:
    """One multi-frame instance; 512x512x2 bytes = 0.5 MiB per frame."""
    rng = np.random.default_rng(7)
    study_uid, series_uid = generate_uid(), generate_uid()
    ds = _base(mrn, study_uid, series_uid, MF_GRAYSCALE_WORD_SC, "MULTIFRAME PERFORMANCE SERIES (synthetic)")
    ds.Modality = "OT"
    ds.InstanceNumber = 1
    ds.Rows = ds.Columns = size
    ds.NumberOfFrames = frames
    ds.PixelSpacing = [0.7, 0.7]
    base = np.clip(chest_phantom_slice(size, 0.7, 0.0, (-72.0, -20.0, 0.0, 7.5), rng) + 1024, 0, 65535).astype(np.uint16)
    buf = bytearray()
    for f in range(frames):
        buf += np.roll(base, f % 32, axis=1).tobytes()
    ds.PixelData = bytes(buf)
    return study_uid, series_uid, _to_bytes(ds)
