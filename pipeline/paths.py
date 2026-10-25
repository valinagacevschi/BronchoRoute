"""Paths for data and outputs owned by the pipeline directory.

Phase 3 review: LUNGNAV_PIPELINE_ROOT redirects the whole tree (playwright tmp copy).
"""

from __future__ import annotations

import os
from pathlib import Path

_env_root = os.environ.get("LUNGNAV_PIPELINE_ROOT", "").strip()
PIPELINE = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent
LOCAL_CASES = PIPELINE / "data/extracted/local"
AEROPATH = PIPELINE / "data/extracted/AeroPath"
TS_OUT = PIPELINE / "ts_out"
OUT = PIPELINE / "out"
