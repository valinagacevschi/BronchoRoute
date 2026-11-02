# Changes: guarded synthetic segmentation for tests and the Electron smoke only.
"""Write tiny deterministic artifacts without loading model weights."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import nibabel as nib
import numpy as np

import repo_root
repo_root.ensure()
from app.caseref import CaseRef
import paths


def main():
    if os.environ.get("BRONCHOROUTE_FAKE_SEGMENT") != "1" or not (
        os.environ.get("BRONCHOROUTE_TESTING") == "1"
        or os.environ.get("BRONCHOROUTE_SMOKE") == "1"
    ):
        raise SystemExit("synthetic processing is restricted to tests")
    if paths.PIPELINE.resolve() == Path(__file__).resolve().parent:
        raise SystemExit("synthetic processing requires an isolated state root")
    ref = CaseRef.parse(sys.argv[1])
    for step in ("reading", "segmentation", "airway model", "graph"):
        print(f"step: {step}", flush=True)
        time.sleep(float(os.environ.get("BRONCHOROUTE_FAKE_STEP_SECONDS", "0.4")))
    if os.environ.get("BRONCHOROUTE_FAKE_FAIL") == "1":
        raise SystemExit(1)
    ct = nib.load(ref.ct)
    ref.ts_out.mkdir(parents=True, exist_ok=True)
    ref.trachea.parent.mkdir(parents=True, exist_ok=True)
    mask = np.zeros(ct.shape, dtype=np.uint8)
    mask[2:6, 2:6, 2:20] = 1
    for target in (ref.airways, ref.trachea, ref.lobes, ref.bridges):
        nib.save(nib.Nifti1Image(mask, ct.affine), target)
    (ref.ts_out / "airway_bridges.json").write_text(json.dumps({"bridges": []}))
    graph = {
        "case": ref.case_id, "root_node_id": 0, "coordinate_system": "RAS",
        "nodes": [
            {"id": 0, "position_mm": [-4, -4, 20], "radius_mm": 4},
            {"id": 1, "position_mm": [-4, -4, 2], "radius_mm": 2},
        ],
        "edges": [{
            "from": 0, "to": 1, "generation": 0, "radius_mm": 3,
            "length_mm": 18, "lobe": None, "airway_name": "trachea",
            "provenance": "ct", "centreline_mm": [[-4, -4, z] for z in range(20, 1, -2)],
        }], "review": [],
    }
    out = paths.OUT / ref.case_id
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{ref.case_id}_graph.json").write_text(json.dumps(graph))


if __name__ == "__main__":
    main()
