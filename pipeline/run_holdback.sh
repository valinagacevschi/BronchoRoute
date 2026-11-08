#!/usr/bin/env bash
# Run the held-back AeroPath cases through the full pipeline.
#
# These five were extracted but never examined while the pipeline was written,
# so this is the first honest test of whether the logic generalises or was
# quietly fitted to case 1.
set -u
cd "$(dirname "$0")"

for n in 2 3 4 5 6; do
  echo "═══ case $n ═══"
  case_dir="data/extracted/AeroPath/$n"
  ct="$case_dir/${n}_CT_HR.nii.gz"

  if [ ! -f "ts_out/lobes_$n/lung_upper_lobe_left.nii.gz" ]; then
    uv run TotalSegmentator -i "$ct" -o "ts_out/lobes_$n" \
      --roi_subset lung_upper_lobe_left lung_lower_lobe_left \
                   lung_upper_lobe_right lung_middle_lobe_right \
                   lung_lower_lobe_right \
      --device mps --quiet 2>&1 | tail -2
  fi

  uv run python - "$n" <<'PY'
import sys, nibabel as nib, numpy as np
from pathlib import Path
n = sys.argv[1]
LOBES = {"lung_upper_lobe_left":1,"lung_lower_lobe_left":2,"lung_upper_lobe_right":3,
         "lung_middle_lobe_right":4,"lung_lower_lobe_right":5}
d = Path(f"ts_out/lobes_{n}"); ref=None; merged=None
for name,v in LOBES.items():
    img = nib.load(d/f"{name}.nii.gz"); m = np.asanyarray(img.dataobj)>0
    if merged is None: ref, merged = img, np.zeros(m.shape, np.uint8)
    merged[m]=v
nib.save(nib.Nifti1Image(merged, ref.affine, ref.header), f"ts_out/lobes_{n}.nii.gz")
PY

  uv run python build_graph.py "$case_dir" --lobes "ts_out/lobes_$n.nii.gz" -o out 2>&1 \
    | sed 's/^/  /'
done

echo
echo "═══ naming completeness across all cases ═══"
uv run python - <<'PY'
import json
from pathlib import Path

WANT = ["trachea", "right main bronchus", "left main bronchus",
        "right upper lobe bronchus", "right middle lobe bronchus",
        "right lower lobe bronchus", "left upper lobe bronchus",
        "left lower lobe bronchus"]

rows = []
for n in [1, 2, 3, 4, 5, 6]:
    p = Path(f"out/{n}_graph.json")
    if not p.exists():
        continue
    g = json.loads(p.read_text())
    e = g["edges"]
    names = {x["airway_name"] for x in e if x["airway_name"]}
    rows.append({
        "case": n,
        "branches": len(e),
        "gens": max(x["generation"] for x in e),
        "lobed": sum(1 for x in e if x["lobe"]),
        "root_r": max(x["radius_mm"] for x in e),
        "found": names,
    })

hdr = f"{'case':>5}{'branches':>10}{'gens':>6}{'lobed':>7}{'root r':>9}{'named':>7}"
print(hdr)
for r in rows:
    print(f"{r['case']:>5}{r['branches']:>10}{r['gens']:>6}{r['lobed']:>7}"
          f"{r['root_r']:>8.1f}{len(r['found'] & set(WANT)):>6}/8")

print("\nper-structure, . = found, MISSING = not named")
for w in WANT:
    marks = "".join(" ." if w in r["found"] else " X" for r in rows)
    flag = "" if all(w in r["found"] for r in rows) else "   <- MISSING somewhere"
    print(f"  {w:<28}{marks}{flag}")
PY
