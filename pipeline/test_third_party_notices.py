# Changes: generated notices are stable and omit the dev group.
"""The notices file is a build artifact: regenerating it must be a no-op."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "third_party_notices.py"


def _module():
    spec = importlib.util.spec_from_file_location("third_party_notices", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_notices_stable_and_match_committed_file():
    module = _module()
    lock = REPO / "pipeline" / "uv.lock"
    first = module.generate(lock)
    second = module.generate(lock)
    assert first == second
    committed = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert committed == first
    assert "\n- playwright " not in first
    assert "\n- greenlet " not in first
    assert "\n- kimimaro " in first
    assert "\n- dijkstra3d " in first
    assert "GPL-3.0-or-later" in first
    assert "doi.org/10.5281/zenodo.23190857" in first
    assert "lung_vessels" in first
    assert "OFL-1.1" in first
    assert "## Needs human review" in first
