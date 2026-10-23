"""Lesion contouring plugins.

The default plugin is classical image processing (lung field extraction and
detection of soft-tissue structures fully enclosed by aerated lung). It is a
stand-in for a trained segmentation network behind the same interface.
"""
from __future__ import annotations

import math
from typing import Protocol

import numpy as np
from scipy import ndimage as ndi
from skimage import measure


class SegmentationPlugin(Protocol):
    name: str
    version: str

    def run(self, hu: np.ndarray, spacing: tuple[float, float]) -> dict: ...


class EnclosedLungLesionPlugin:
    name = "enclosed-lung-lesion"
    version = "1.0.0"

    def run(self, hu: np.ndarray, spacing: tuple[float, float]) -> dict:
        sy, sx = spacing
        body = ndi.binary_fill_holes(hu > -500)
        lung = (hu < -400) & body
        lung = ndi.binary_opening(lung, iterations=2)
        lung_filled = ndi.binary_fill_holes(lung)
        enclosed = lung_filled & ~lung
        labels, n = ndi.label(enclosed)
        findings = []
        for i in range(1, n + 1):
            mask = labels == i
            area_mm2 = float(mask.sum()) * sx * sy
            if area_mm2 < 12 or area_mm2 > 3000:
                continue
            contours = measure.find_contours(mask.astype(float), 0.5)
            if not contours:
                continue
            c = max(contours, key=len)
            perim = float(np.sum(np.hypot(np.diff(c[:, 0]) * sy, np.diff(c[:, 1]) * sx)))
            circularity = 4 * math.pi * area_mm2 / (perim * perim) if perim else 0.0
            step = max(1, len(c) // 64)
            rows, cols = np.nonzero(mask)
            findings.append({
                "label": "pulmonary nodule candidate" if circularity > 0.6 else "enclosed lung opacity",
                "area_mm2": round(area_mm2, 1),
                "equivalent_diameter_mm": round(2 * math.sqrt(area_mm2 / math.pi), 1),
                "mean_hu": round(float(hu[mask].mean()), 1),
                "circularity": round(circularity, 2),
                "centroid": [round(float(cols.mean()), 1), round(float(rows.mean()), 1)],
                "contour": [[round(float(p[1]), 1), round(float(p[0]), 1)] for p in c[::step]],
            })
        findings.sort(key=lambda f: -f["area_mm2"])
        lung_fraction = float(lung.sum()) / float(body.sum() or 1)
        return {"plugin": self.name, "version": self.version, "findings": findings,
                "lung_fraction": round(lung_fraction, 3),
                "disclaimer": "Classical image-processing assist, not a trained diagnostic model. Radiologist review required."}


PLUGINS: dict[str, SegmentationPlugin] = {EnclosedLungLesionPlugin.name: EnclosedLungLesionPlugin()}
