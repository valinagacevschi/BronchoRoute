"""Paths for data and outputs owned by the pipeline directory."""

from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
LOCAL_CASES = PIPELINE / "data/extracted/local"
AEROPATH = PIPELINE / "data/extracted/AeroPath"
TS_OUT = PIPELINE / "ts_out"
OUT = PIPELINE / "out"
