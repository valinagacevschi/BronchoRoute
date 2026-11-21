# Changes: pack deterministic TotalSegmentator weight zips and print upload commands.
"""Pack the TotalSegmentator datasets BronchoRoute runs, without uploading them.

--pack --dry-run lists those datasets and the bytes that would be zipped.
--pack writes stored zips (sorted names, fixed timestamps, no Apple extras).
--upload prints gh release commands and does not run them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import zipfile
from pathlib import Path

REPO = "valinagacevschi/BronchoRoute"
TAG = "weights-v1"
VERSION = "2.18.0"

# TotalSegmentator 3.0.1 runs the lobe step as total_v2, which loads the same
# v2 weights: lung_vessels is task 117 and, with robust_crop, the 3 mm model
# 297; total_v2 --roi_subset runs organs part 291 and the 6 mm crop model 298.
# The current ROI set also asks for heart, aorta and pulmonary_vein, which
# live in the cardiac part (Dataset293). This tag does not mirror 293 yet, so
# a packaged app that requests those ROIs falls back to a runtime download.
# Add Dataset293_TotalSegmentator_part3_cardiac_1559subj here and to the
# weights-v1 release before shipping those layers offline.
DATASETS = (
    "Dataset117_lung_airways_arteries_veins_282subj",
    "Dataset291_TotalSegmentator_part1_organs_1559subj",
    "Dataset297_TotalSegmentator_total_3mm_1559subj",
    "Dataset298_TotalSegmentator_total_6mm_1559subj",
)

SKIP_NAMES = {"__MACOSX", ".DS_Store", ".verified"}


def skipped(name: str) -> bool:
    return name in SKIP_NAMES or name.startswith("._")


ZIP_TIME = (1980, 1, 1, 0, 0, 0)
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MANIFEST = SCRIPT_DIR.parent / "weights-manifest.json"
DEFAULT_ROOT = Path.home() / ".totalsegmentator" / "nnunet" / "results"


def iter_files(root: Path) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept = []
        for name in sorted(dirnames):
            if skipped(name):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if skipped(name):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                continue
            rel = full.relative_to(root).as_posix()
            if any(skipped(part) for part in rel.split("/")):
                continue
            paths.append((rel, full))
    paths.sort(key=lambda item: item[0])
    return paths


def dataset_bytes(root: Path) -> int:
    return sum(full.stat().st_size for _, full in iter_files(root))


def require_dataset(root: Path, name: str) -> Path:
    path = root / name
    if not path.is_dir():
        sys.exit(f"missing dataset: {path}")
    return path


def print_dry_run(root: Path) -> None:
    print(f"root {root}")
    for name in DATASETS:
        path = require_dataset(root, name)
        print(f"{name} {dataset_bytes(path)}")


def pack_dataset(source: Path, dest: Path) -> tuple[int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    if tmp.exists():
        tmp.unlink()
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for rel, full in iter_files(source):
            info = zipfile.ZipInfo(rel, ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644) << 16
            info.flag_bits = 0
            archive.writestr(info, full.read_bytes())
    os.replace(tmp, dest)
    digest = hashlib.sha256()
    with dest.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return dest.stat().st_size, digest.hexdigest()


def write_manifest(path: Path, files: list[dict]) -> None:
    document = {
        "repo": REPO,
        "tag": TAG,
        "totalsegmentator_version": VERSION,
        "files": [
            {
                "dataset": item["dataset"],
                "asset": item["asset"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            }
            for item in files
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def pack_all(root: Path, out_dir: Path, manifest_path: Path) -> list[dict]:
    files = []
    for name in DATASETS:
        source = require_dataset(root, name)
        dest = out_dir / f"{name}.zip"
        size, digest = pack_dataset(source, dest)
        item = {
            "dataset": name,
            "asset": dest.name,
            "bytes": size,
            "sha256": digest,
            "path": dest,
        }
        files.append(item)
        print(f"wrote {dest} {size} {digest}")
    write_manifest(manifest_path, files)
    print(f"manifest {manifest_path}")
    return files


def release_identity(manifest_path: Path) -> tuple[str, str, str]:
    if manifest_path.is_file():
        data = json.loads(manifest_path.read_text())
        return data["repo"], data["tag"], data["totalsegmentator_version"]
    return REPO, TAG, VERSION


def print_upload(manifest_path: Path, out_dir: Path) -> None:
    repo, tag, version = release_identity(manifest_path)
    notes = f"TotalSegmentator {version} weights"
    create = " ".join([
        "gh", "release", "create", shlex.quote(tag),
        "--repo", shlex.quote(repo),
        "--title", shlex.quote(tag),
        "--notes", shlex.quote(notes),
    ])
    print(create)
    if manifest_path.is_file():
        assets = [item["asset"] for item in json.loads(manifest_path.read_text())["files"]]
    else:
        assets = [f"{name}.zip" for name in DATASETS]
    for asset in assets:
        upload = " ".join([
            "gh", "release", "upload", shlex.quote(tag),
            shlex.quote(str(out_dir / asset)),
            "--repo", shlex.quote(repo),
            "--clobber",
        ])
        print(upload)


def default_out() -> Path:
    return Path(os.environ.get("TMPDIR", "/tmp")) / "bronchoroute-weights-v1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", action="store_true", help="list or write weight zips")
    parser.add_argument("--upload", action="store_true", help="print gh release commands; do not run them")
    parser.add_argument("--dry-run", action="store_true", help="with --pack, list datasets and sizes only")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    if not args.pack and not args.upload:
        parser.error("pass --pack, --upload, or both")
    out_dir = args.out or default_out()
    if args.pack:
        if args.dry_run:
            print_dry_run(args.root)
        else:
            pack_all(args.root, out_dir, args.manifest)
    if args.upload:
        print_upload(args.manifest, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
