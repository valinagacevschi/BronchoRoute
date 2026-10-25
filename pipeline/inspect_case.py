"""Report what an AeroPath case actually contains.

The dataset documents an airway mask and a "lungs" mask but never says whether
the latter is binary, left/right, or per-lobe. Lobar branch labelling needs five
lobes, so that distinction decides whether TotalSegmentator is required.
"""

import sys
from pathlib import Path

import nibabel as nib
import numpy as np


def report(path: Path) -> None:
    img = nib.load(path)
    data = np.asanyarray(img.dataobj)
    values, counts = np.unique(data, return_counts=True)

    print(f"\n{path.name}")
    print(f"  shape      {data.shape}")
    print(f"  dtype      {data.dtype}")
    print(f"  spacing    {tuple(round(float(z), 3) for z in img.header.get_zooms())} mm")
    print(f"  orientation {''.join(nib.aff2axcodes(img.affine))}")

    if len(values) <= 12:
        print("  labels:")
        for v, c in zip(values, counts):
            print(f"    {int(v):>4}  {c:>12,} voxels")
    else:
        print(f"  continuous, range [{data.min()}, {data.max()}] "
              f"({len(values)} distinct values)")


def main() -> None:
    case_dir = Path(sys.argv[1])
    for path in sorted(case_dir.glob("*.nii.gz")):
        report(path)


if __name__ == "__main__":
    main()
