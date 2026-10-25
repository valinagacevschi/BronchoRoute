"""Airway provenance label volume on the CT crop grid (Phase 3).

Labels (uint8, x-fastest / Fortran order, same shape+affine as ct_crop):
  1 = CT airway (airways.nii.gz minus bridge voxels)
  2 = interpolated (airway_bridges.nii.gz; wins over 1 where both present)
  3 = manual (reserved; none yet)

Phase 3 review: assert_affine_match via public helper; shared cache_pair.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from atomic_json import json_file_lock
from termcolor import cprint

from app.cache_pair import publish_binary_pair, read_cached_binary_pair, read_meta_json
from app.caseref import CaseRef
from app.ct_crop import (
    AFFINE_ATOL,
    MEMORY_ORDER,
    _fingerprint,
    _load_canonical,
    assert_affine_match,
    build_ct_crop,
)

LABEL_CT = 1
LABEL_INTERPOLATED = 2
LABEL_MANUAL = 3  # reserved


class MasksUnavailable(FileNotFoundError):
    """Raised when required airway masks are missing (maps to HTTP 404)."""


def mask_paths(ref: CaseRef) -> tuple[Path, Path]:
    cache_dir = ref.crop_cache_dir
    return cache_dir / "masks_crop.bin", cache_dir / "masks_crop.json"


def _inputs_fingerprint(ref: CaseRef, ct_meta: dict) -> dict:
    inputs: dict = {
        "airways": _fingerprint(ref.airways),
        "ct_crop_sha256": ct_meta.get("sha256"),
        "crop_offset_voxels": list(ct_meta.get("crop_offset_voxels") or []),
        "shape": list(ct_meta.get("shape") or []),
    }
    if ref.bridges.is_file():
        inputs["bridges"] = _fingerprint(ref.bridges)
    else:
        inputs["bridges"] = None
    return inputs


def _cache_hit(bin_path: Path, meta_path: Path, inputs: dict) -> dict | None:
    meta = read_meta_json(meta_path)
    if meta is None or not bin_path.is_file():
        return None
    if meta.get("inputs") != inputs or not meta.get("sha256"):
        return None
    if meta.get("memory_order") != MEMORY_ORDER:
        return None
    if meta.get("dtype") != "uint8":
        return None
    expected_size = meta.get("bin_size")
    if expected_size is None:
        nx, ny, nz = meta.get("shape", [0, 0, 0])
        expected_size = int(nx) * int(ny) * int(nz)
    if bin_path.stat().st_size != expected_size:
        return None
    cached_mtime = meta.get("bin_mtime_ns")
    if cached_mtime is not None and bin_path.stat().st_mtime_ns != cached_mtime:
        return None
    return meta


def _compose_labels(
    airways: np.ndarray, bridges: np.ndarray | None
) -> np.ndarray:
    labels = np.zeros(airways.shape, dtype=np.uint8)
    labels[airways] = LABEL_CT
    if bridges is not None:
        labels[bridges] = LABEL_INTERPOLATED
    return labels


def build_masks_crop(case_id: str, *, force: bool = False) -> dict:
    """Build or reuse the uint8 label crop on the CT crop grid."""
    ref = CaseRef.parse(case_id)
    if not ref.airways.is_file():
        raise MasksUnavailable(f"airways mask missing for case {case_id}")

    ct_meta = build_ct_crop(case_id)
    bin_path, meta_path = mask_paths(ref)
    inputs = _inputs_fingerprint(ref, ct_meta)

    with json_file_lock(bin_path):
        if not force:
            hit = _cache_hit(bin_path, meta_path, inputs)
            if hit is not None:
                return hit

        cprint(f"building masks crop for {case_id}", "cyan")
        airways_img = _load_canonical(ref.airways)
        ct_affine = np.asarray(ct_meta["affine"], dtype=float)
        source_affine = airways_img.affine
        # Crop affine shares the CT linear part; origin is rewritten for the crop.
        if not np.allclose(
            source_affine[:3, :3], ct_affine[:3, :3], atol=AFFINE_ATOL, rtol=0
        ):
            raise ValueError(
                f"airways linear affine does not match CT crop (atol={AFFINE_ATOL})"
            )

        lo = np.asarray(ct_meta["crop_offset_voxels"], dtype=int)
        shape = np.asarray(ct_meta["shape"], dtype=int)
        hi = lo + shape

        airways_full = np.asanyarray(airways_img.dataobj) > 0
        if any(hi[i] > airways_full.shape[i] for i in range(3)):
            raise ValueError(
                f"case {case_id}: crop extent {hi.tolist()} exceeds airways "
                f"shape {list(airways_full.shape)}"
            )
        airways_crop = airways_full[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]

        bridges_crop = None
        if ref.bridges.is_file():
            bridges_img = _load_canonical(ref.bridges)
            if bridges_img.shape != airways_img.shape:
                raise ValueError(
                    f"case {case_id}: bridges shape {bridges_img.shape} != "
                    f"airways {airways_img.shape}"
                )
            assert_affine_match(source_affine, bridges_img.affine, "bridges")
            bridges_full = np.asanyarray(bridges_img.dataobj) > 0
            bridges_crop = bridges_full[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]

        labels = _compose_labels(airways_crop, bridges_crop)
        wire = np.asarray(np.asfortranarray(labels), dtype=np.uint8, order="F")
        raw = wire.tobytes(order="F")
        meta = {
            "case": case_id,
            "shape": [int(shape[0]), int(shape[1]), int(shape[2])],
            "shape_axis_order": "x,y,z (RAS voxel axes after as_closest_canonical)",
            "memory_order": MEMORY_ORDER,
            "spacing_mm": list(ct_meta["spacing_mm"]),
            "affine": ct_meta["affine"],
            "crop_offset_voxels": list(ct_meta["crop_offset_voxels"]),
            "dtype": "uint8",
            "labels": {
                "1": "ct",
                "2": "interpolated",
                "3": "manual",
            },
            "inputs": inputs,
        }
        return publish_binary_pair(
            bin_path,
            meta_path,
            raw,
            meta,
            bin_prefix=".masks_crop.bin.",
            meta_prefix=".masks_crop.json.",
        )


def read_masks_bytes(case_id: str) -> tuple[bytes, dict]:
    ref = CaseRef.parse(case_id)
    bin_path, meta_path = mask_paths(ref)

    def _nbytes(meta: dict) -> int:
        return int(np.prod(meta["shape"]))

    return read_cached_binary_pair(
        case_id,
        bin_path,
        meta_path,
        ensure_meta=build_masks_crop,
        force_rebuild=lambda cid: build_masks_crop(cid, force=True),
        expected_nbytes=_nbytes,
    )
