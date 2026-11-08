# Changes: port BREA continuous geometry encoder; bundle profile next to this module (#56).
"""Continuous CT-to-BREA appearance adapter, independent of model formatting.

A training-only reference quantile curve supplies the disparity distribution.
No noise, dither, rank bins, RGB targets, or CT portal labels enter this transform.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage

PROTOCOL = 'brea_geometry_continuous_v2'
DEFAULT_PROFILE = Path(__file__).resolve().parent / 'brea-depth-profile.json'


@lru_cache(maxsize=8)
def _read_profile(path: str, digest: str) -> dict:
    profile = json.loads(Path(path).read_text())
    q = np.asarray(profile['quantiles'], dtype=np.float64)
    if profile.get('protocol') != PROTOCOL or q.ndim != 1 or len(q) < 2:
        raise ValueError('Invalid BREA depth profile')
    if not np.isfinite(q).all() or np.any(np.diff(q) < 0) or q.min() < 0 or q.max() > 1:
        raise ValueError('BREA quantiles must be finite, monotone and in [0,1]')
    if profile.get('reference_split') != 'train':
        raise ValueError('BREA profile must use training references only')
    return profile


def load_profile(path: str | Path = DEFAULT_PROFILE) -> dict:
    path = Path(path).resolve()
    return _read_profile(str(path), hashlib.sha256(path.read_bytes()).hexdigest())


def continuous_brea_depth(depth: np.ndarray, valid: np.ndarray | None = None,
                          profile: dict | None = None) -> np.ndarray:
    """Return float32 near-bright disparity at BREA's native 128px resolution.

    Extend masked pixels before resampling, using only valid intersections for the extension.
    Rank statistics use the complete filled square, matching cropped training inputs. Exact equal distances remain equal. Interpolated midranks
    avoid the staircase of a quantized empirical CDF. The averaged training
    quantile curve is frozen; per-view ranks discard unknown estimator scale.
    """
    profile = load_profile() if profile is None else profile
    value = np.asarray(depth, dtype=np.float32)
    if value.ndim != 2:
        raise ValueError('CT depth must be a 2D array')
    mask = np.isfinite(value) & (value > 0)
    if valid is not None:
        if np.shape(valid) != value.shape:
            raise ValueError('valid mask must match CT depth')
        mask &= np.asarray(valid, dtype=bool)
    if not mask.any():
        raise ValueError('CT depth has no valid positive wall intersections')
    indices = ndimage.distance_transform_edt(~mask, return_distances=False, return_indices=True)
    filled = value[tuple(indices)]
    size = int(profile.get('native_size', 128))
    resized = cv2.resize(filled, (size, size), interpolation=cv2.INTER_AREA)
    support = cv2.resize(mask.astype(np.uint8), (size, size), interpolation=cv2.INTER_NEAREST).astype(bool)
    if not support.any():
        raise ValueError('CT depth has no valid support at BREA resolution')
    sigma = float(profile.get('smoothing_sigma', 0.75))
    if sigma > 0:
        resized = cv2.GaussianBlur(resized, (0, 0), sigma)
    disparity = -resized
    values, counts = np.unique(disparity, return_counts=True)
    target = np.asarray(profile['quantiles'])
    if len(values) == 1:
        return np.full((size, size), np.interp(0.5, np.linspace(0, 1, len(target)), target), np.float32)
    midranks = (np.cumsum(counts) - counts / 2) / counts.sum()
    ranks = np.interp(disparity, values, midranks)
    result = np.interp(ranks, np.linspace(0, 1, len(target)), target)
    post_sigma = float(profile.get("output_smoothing_sigma", 0.75))
    if post_sigma > 0:
        result = cv2.GaussianBlur(result.astype(np.float32), (0, 0), post_sigma)
    return result.astype(np.float32)


def brea_geometry_image(depth: np.ndarray, valid: np.ndarray | None = None,
                        size: int = 256, profile: dict | None = None) -> np.ndarray:
    """Match BREA's encode-at-128px then bicubic-resize image convention."""
    unit = continuous_brea_depth(depth, valid, profile)
    image = Image.fromarray(np.rint(unit * 255).astype(np.uint8))
    return np.asarray(image.resize((size, size), Image.Resampling.BICUBIC)).copy()
