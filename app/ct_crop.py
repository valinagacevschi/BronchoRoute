"""Canonical RAS int16 thorax crop for the browser CT panes.

Wire format: little-endian int16, shape (x, y, z), memory_order "x fastest"
(Fortran / column-major: voxel (i, j, k) at byte offset 2 * (i + j*nx + k*nx*ny)).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np
from atomic_json import json_file_lock

import paths
from app.caseref import CaseRef

MARGIN_MM = 10.0
MEMORY_ORDER = "x fastest"
WINDOW_PRESETS = {
    "lung": {"W": 1500, "L": -600},
    "mediastinal": {"W": 400, "L": 40},
}
AFFINE_ATOL = 1e-3


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def crop_paths(ref: CaseRef) -> tuple[Path, Path]:
    cache_dir = ref.crop_cache_dir
    return cache_dir / "ct_crop.bin", cache_dir / "ct_crop.json"


def _load_canonical(path: Path) -> nib.Nifti1Image:
    return nib.as_closest_canonical(nib.load(path))


def _bbox(mask: np.ndarray, spacing: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    coords = np.argwhere(mask)
    if coords.size == 0:
        raise ValueError("thorax mask is empty")
    lo = coords.min(axis=0)
    hi = coords.max(axis=0) + 1
    margin = np.ceil(MARGIN_MM / spacing).astype(int)
    shape = np.asarray(mask.shape)
    lo = np.maximum(lo - margin, 0)
    hi = np.minimum(hi + margin, shape)
    return lo, hi


def _hu_to_int16(crop: np.ndarray) -> np.ndarray:
    info = np.iinfo(np.int16)
    rounded = np.rint(np.asarray(crop, dtype=np.float64))
    return np.clip(rounded, info.min, info.max).astype(np.int16)


def _assert_affine_match(ct_affine: np.ndarray, other: np.ndarray, label: str) -> None:
    if not np.allclose(ct_affine, other, atol=AFFINE_ATOL, rtol=0):
        raise ValueError(f"{label} affine does not match CT affine (atol={AFFINE_ATOL})")


def _inputs_fingerprint(ref: CaseRef) -> dict[str, dict]:
    return {
        name: _fingerprint(getattr(ref, name))
        for name in ("ct", "lobes", "trachea", "airways")
    }


def _cache_hit(bin_path: Path, meta_path: Path, inputs: dict) -> dict | None:
    if not bin_path.is_file() or not meta_path.is_file():
        return None
    meta = json.loads(meta_path.read_text())
    if meta.get("inputs") != inputs or not meta.get("sha256"):
        return None
    if meta.get("memory_order") != MEMORY_ORDER:
        return None
    stat = bin_path.stat()
    expected_size = meta.get("bin_size")
    if expected_size is None:
        nx, ny, nz = meta.get("shape", [0, 0, 0])
        expected_size = int(nx) * int(ny) * int(nz) * 2
    if stat.st_size != expected_size:
        return None
    cached_mtime = meta.get("bin_mtime_ns")
    if cached_mtime is not None and stat.st_mtime_ns != cached_mtime:
        return None
    return meta


def _publish_crop_pair(bin_path: Path, meta_path: Path, raw: bytes, meta: dict) -> dict:
    """Write bin then meta (meta carries sha256); publish each via replace."""
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    tmp_bin_name: str | None = None
    tmp_meta_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=bin_path.parent,
            prefix=".ct_crop.bin.",
            suffix=".tmp",
            delete=False,
        ) as tmp_bin:
            tmp_bin.write(raw)
            tmp_bin.flush()
            os.fsync(tmp_bin.fileno())
            tmp_bin_name = tmp_bin.name
        os.replace(tmp_bin_name, bin_path)
        tmp_bin_name = None
        published = dict(meta)
        published["sha256"] = digest
        published["bin_size"] = len(raw)
        published["bin_mtime_ns"] = bin_path.stat().st_mtime_ns
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=meta_path.parent,
            prefix=".ct_crop.json.",
            suffix=".tmp",
            delete=False,
        ) as tmp_meta:
            json.dump(published, tmp_meta, indent=2)
            tmp_meta.write("\n")
            tmp_meta.flush()
            os.fsync(tmp_meta.fileno())
            tmp_meta_name = tmp_meta.name
        os.replace(tmp_meta_name, meta_path)
        tmp_meta_name = None
        return published
    finally:
        for name in (tmp_bin_name, tmp_meta_name):
            if name is not None:
                try:
                    os.unlink(name)
                except OSError:
                    pass


def build_ct_crop(case_id: str, *, force: bool = False) -> dict:
    """Build or reuse the thorax crop; returns meta dict (also written to disk)."""
    ref = CaseRef.parse(case_id)
    for key in ("ct", "lobes", "trachea", "airways"):
        path = getattr(ref, key)
        if not path.is_file():
            raise FileNotFoundError(f"missing {key} for case {case_id}")
    bin_path, meta_path = crop_paths(ref)
    inputs = _inputs_fingerprint(ref)

    with json_file_lock(bin_path):
        if not force:
            hit = _cache_hit(bin_path, meta_path, inputs)
            if hit is not None:
                return hit

        ct_img = _load_canonical(ref.ct)
        lobes_img = _load_canonical(ref.lobes)
        trachea_img = _load_canonical(ref.trachea)
        airways_img = _load_canonical(ref.airways)
        _assert_affine_match(ct_img.affine, lobes_img.affine, "lobes")
        _assert_affine_match(ct_img.affine, trachea_img.affine, "trachea")
        _assert_affine_match(ct_img.affine, airways_img.affine, "airways")

        ct = np.asanyarray(ct_img.dataobj)
        lobes = np.asanyarray(lobes_img.dataobj) > 0
        trachea = np.asanyarray(trachea_img.dataobj) > 0
        airways = np.asanyarray(airways_img.dataobj) > 0
        if ct.shape != lobes.shape or ct.shape != trachea.shape or ct.shape != airways.shape:
            raise ValueError(
                f"case {case_id}: CT/mask shape mismatch "
                f"{ct.shape} vs lobes {lobes.shape} trachea {trachea.shape} "
                f"airways {airways.shape}"
            )
        spacing = np.asarray(ct_img.header.get_zooms()[:3], dtype=float)
        mask = lobes | trachea | airways
        lo, hi = _bbox(mask, spacing)
        crop = _hu_to_int16(ct[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]])
        # Fortran / x-fastest layout to match memory_order.
        wire = np.asarray(np.asfortranarray(crop), dtype="<i2", order="F")
        raw = wire.tobytes(order="F")
        origin = (ct_img.affine @ np.array([lo[0], lo[1], lo[2], 1.0], dtype=float))[:3]
        affine = ct_img.affine.copy()
        affine[:3, 3] = origin
        meta = {
            "case": case_id,
            "shape": [int(crop.shape[0]), int(crop.shape[1]), int(crop.shape[2])],
            "shape_axis_order": "x,y,z (RAS voxel axes after as_closest_canonical)",
            "memory_order": MEMORY_ORDER,
            "spacing_mm": [float(spacing[0]), float(spacing[1]), float(spacing[2])],
            "affine": affine.tolist(),
            "crop_offset_voxels": [int(lo[0]), int(lo[1]), int(lo[2])],
            "dtype": "int16",
            "endian": "little",
            "window_presets": WINDOW_PRESETS,
            "inputs": inputs,
        }
        return _publish_crop_pair(bin_path, meta_path, raw, meta)


def read_ct_bytes(case_id: str) -> tuple[bytes, dict]:
    meta = build_ct_crop(case_id)
    ref = CaseRef.parse(case_id)
    bin_path, _meta_path = crop_paths(ref)
    raw = bin_path.read_bytes()
    # Cheap integrity: size must match; sha comes from meta (no full re-hash on hit).
    expected = meta.get("bin_size")
    if expected is not None and len(raw) != expected:
        meta = build_ct_crop(case_id, force=True)
        raw = bin_path.read_bytes()
    elif len(raw) != int(np.prod(meta["shape"])) * 2:
        meta = build_ct_crop(case_id, force=True)
        raw = bin_path.read_bytes()
    return raw, meta


def voxel_byte_offset(i: int, j: int, k: int, shape: tuple[int, int, int]) -> int:
    """Byte offset for voxel (i,j,k) under memory_order 'x fastest'."""
    nx, ny, _nz = shape
    return 2 * (i + j * nx + k * nx * ny)
