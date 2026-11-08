"""Browser-facing local app server for Lung Navigator."""

from pathlib import Path
import sys

_PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
_pipeline_str = str(_PIPELINE)
if _pipeline_str not in sys.path:
    sys.path.insert(0, _pipeline_str)
