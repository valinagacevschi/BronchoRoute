"""Canonical RAS int16 thorax crop for the browser CT panes.

Wire format: little-endian int16, shape (x, y, z), memory_order "x fastest"
(Fortran / column-major: voxel (i, j, k) at byte offset 2 * (i + j*nx + k*nx*ny)).

Phase 3 review: public assert_affine_match; shared cache_pair; corrupt meta → rebuild.
"""

from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np
from atomic_json import json_file_lock
from termcolor import cprint

from app.cache_pair import publish_binary_pair, read_cached_binary_pair, read_meta_json
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


def assert_affine_match(ct_affine: np.ndarray, other: np.ndarray, label: str) -> None:
    """Public affine equality check (atol=AFFINE_ATOL); used by masks too."""
    if not np.allclose(ct_affine, other, atol=AFFINE_ATOL, rtol=0):
        raise ValueError(f"{label} affine does not match CT affine (atol={AFFINE_ATOL})")


# Back-compat alias for internal callers.
_assert_affine_match = assert_affine_match


def _inputs_fingerprint(ref: CaseRef) -> dict[str, dict]:
    return {
        name: _fingerprint(getattr(ref, name))
        for name in ("ct", "lobes", "trachea", "airways")
    }


def _cache_hit(bin_path: Path, meta_path: Path, inputs: dict) -> dict | None:
    meta = read_meta_json(meta_path)
    if meta is None or not bin_path.is_file():
        return None
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

        cprint(f"building CT crop for {case_id}", "cyan")
        ct_img = _load_canonical(ref.ct)
        lobes_img = _load_canonical(ref.lobes)
        trachea_img = _load_canonical(ref.trachea)
        airways_img = _load_canonical(ref.airways)
        assert_affine_match(ct_img.affine, lobes_img.affine, "lobes")
        assert_affine_match(ct_img.affine, trachea_img.affine, "trachea")
        assert_affine_match(ct_img.affine, airways_img.affine, "airways")

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
        return publish_binary_pair(
            bin_path,
            meta_path,
            raw,
            meta,
            bin_prefix=".ct_crop.bin.",
            meta_prefix=".ct_crop.json.",
        )


def read_ct_bytes(case_id: str) -> tuple[bytes, dict]:
    ref = CaseRef.parse(case_id)
    bin_path, meta_path = crop_paths(ref)

    def _nbytes(meta: dict) -> int:
        return int(np.prod(meta["shape"])) * 2

    return read_cached_binary_pair(
        case_id,
        bin_path,
        meta_path,
        ensure_meta=build_ct_crop,
        force_rebuild=lambda cid: build_ct_crop(cid, force=True),
        expected_nbytes=_nbytes,
    )


def voxel_byte_offset(i: int, j: int, k: int, shape: tuple[int, int, int]) -> int:
    """Byte offset for voxel (i,j,k) under memory_order 'x fastest'."""
    nx, ny, _nz = shape
    return 2 * (i + j * nx + k * nx * ny)
