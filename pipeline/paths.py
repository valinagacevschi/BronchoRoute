# Changes: bump PIPELINE_VERSION so Cases processed before estimated airway reprocess.
# Changes: PIPELINE_VERSION stamped on each Case when processing finishes.
# Changes: hide legacy review behind the default-off feature flag.
# Changes: sidecar temporary and rotating log directories.
# Changes: LUNGNAV_DATA_ROOT (alias of pipeline root), LUNGNAV_CACHE_DIR,
# LUNGNAV_CASES allowlist helpers for the lung-nav.appcenter.ro deploy.
"""Paths for data and outputs owned by the pipeline directory.

Phase 3 review: LUNGNAV_PIPELINE_ROOT redirects the whole tree (playwright tmp copy).
Deploy: LUNGNAV_DATA_ROOT is the same redirect (RO mount); LUNGNAV_CACHE_DIR holds
derived writes (crops, bridge labels) when the data root is read-only.
"""

from __future__ import annotations

import os
from pathlib import Path

_env_root = (
    os.environ.get("LUNGNAV_DATA_ROOT", "").strip()
    or os.environ.get("LUNGNAV_PIPELINE_ROOT", "").strip()
)
PIPELINE = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent
LOCAL_CASES = PIPELINE / "data/extracted/local"
AEROPATH = PIPELINE / "data/extracted/AeroPath"
TS_OUT = PIPELINE / "ts_out"
OUT = PIPELINE / "out"

_tmp = os.environ.get("LUNGNAV_TMP_DIR", "").strip()
TMP_DIR = Path(_tmp).resolve() if _tmp else PIPELINE / "tmp"
_log = os.environ.get("LUNGNAV_LOG_DIR", "").strip()
LOG_DIR = Path(_log).resolve() if _log else PIPELINE / "logs"

_cache = os.environ.get("LUNGNAV_CACHE_DIR", "").strip()
CACHE_DIR: Path | None = Path(_cache).resolve() if _cache else None


def allowed_case_ids() -> frozenset[str] | None:
    """Comma-list from LUNGNAV_CASES, or None when unrestricted."""
    raw = os.environ.get("LUNGNAV_CASES", "").strip()
    if not raw:
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def legacy_review() -> bool:
    return os.environ.get("BRONCHOROUTE_LEGACY_REVIEW", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# Bump when a processed Case should be offered for reprocess. Missing stamps count as outdated.
PIPELINE_VERSION = "2"


def viewer_only() -> bool:
    return os.environ.get("LUNGNAV_VIEWER_ONLY", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def cache_case_dir(case_id: str) -> Path:
    """Writable dir for derived artifacts; falls back to ts_out/<id>."""
    if CACHE_DIR is not None:
        return CACHE_DIR / case_id
    return TS_OUT / case_id
