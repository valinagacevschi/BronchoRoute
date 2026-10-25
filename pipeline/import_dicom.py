"""Select and import an axial CT series without exporting DICOM identifiers."""

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import nibabel as nib
import numpy as np
import pydicom
import SimpleITK as sitk
from paths import LOCAL_CASES


VERSION = "1.0"
PREFERRED_KERNEL = re.compile(r"STANDARD|SOFT|B30", re.IGNORECASE)
CONTRAST_WORDS = re.compile(r"ARTERIAL|VENOUS|VENOS|TARDIV|ANGIO", re.IGNORECASE)
SCOUT_WORDS = re.compile(r"SCOUT|LOCALIZER|TOPOGRAM", re.IGNORECASE)


def read_series(src_dir: Path) -> tuple[dict[str, dict], int]:
    src_dir = src_dir.resolve()
    series: dict[str, dict] = {}
    skipped = 0
    for path in src_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, force=False)
            uid = str(ds.SeriesInstanceUID)
            sop_class = str(ds.SOPClassUID.name)
            row = series.get(uid)
            if row is None:
                row = {
                    "series_uid_sha256": series_uid_hash(uid),
                    "number": int(ds.SeriesNumber),
                    "folder": (path.relative_to(src_dir).parts[0]
                               if len(path.relative_to(src_dir).parts) > 1 else "."),
                    "description": str(getattr(ds, "SeriesDescription", "")),
                    "modality": str(getattr(ds, "Modality", "")),
                    "sop_class": sop_class,
                    "kernel": str(getattr(ds, "ConvolutionKernel", "")),
                    "slice_thickness": float(getattr(ds, "SliceThickness", 0) or 0),
                    "orientation": np.zeros(6),
                    "positions": [],
                    "spacing_values": [],
                    "in_plane_size": (0, 0),
                    "files": [],
                }
                series[uid] = row
            if sop_class != "CT Image Storage":
                row["files"].append(str(path))
                continue
            orientation = np.asarray(ds.ImageOrientationPatient, dtype=float)
            position = np.asarray(ds.ImagePositionPatient, dtype=float)
            spacing = float(ds.SliceThickness)
            if not row["positions"]:
                row["slice_thickness"] = spacing
                row["orientation"] = orientation
                row["in_plane_size"] = (int(ds.Columns), int(ds.Rows))
            row["positions"].append(float(position @ np.cross(orientation[:3], orientation[3:])))
            row["spacing_values"].append(spacing)
            row["files"].append(str(path))
        except Exception:
            skipped += 1
    for row in series.values():
        if row["sop_class"] != "CT Image Storage":
            row["count"] = len(row["files"])
            row["spacing"] = row["slice_thickness"]
            row["uniform"] = False
            row["axial"] = False
            row["size"] = (*row["in_plane_size"], row["count"])
            continue
        coords = np.sort(np.asarray(row["positions"], dtype=float))
        gaps = np.diff(coords)
        row["count"] = len(row["files"])
        row["spacing"] = float(np.median(gaps)) if len(gaps) else float(row["slice_thickness"])
        row["uniform"] = bool(len(gaps) and np.max(np.abs(gaps - row["spacing"])) <= 0.05)
        row["axial"] = abs(float(np.cross(row["orientation"][:3], row["orientation"][3:])[2])) >= 0.99
        ordered = sorted(zip(row["positions"], row["files"]))
        row["files"] = [name for _, name in ordered]
        row["size"] = (*row["in_plane_size"], row["count"])
    return series, skipped


def skip_reason(row: dict) -> str | None:
    if row["sop_class"] != "CT Image Storage":
        return f"not a CT image ({row['sop_class']})"
    if row["modality"] != "CT":
        return "not CT modality"
    if not row["axial"]:
        return "not axial"
    if row["count"] < 100:
        return "fewer than 100 slices"
    if not row["uniform"]:
        return "non-uniform slice spacing"
    if SCOUT_WORDS.search(row["description"]):
        return "scout/localizer"
    return None


def pick_series(series: dict[str, dict], override: str | None) -> tuple[str, dict, dict[str, str]]:
    reasons = {uid: reason for uid, row in series.items()
               if (reason := skip_reason(row)) is not None}
    candidates = [(uid, row) for uid, row in series.items() if uid not in reasons]
    if override is not None:
        selected = [(uid, row) for uid, row in candidates
                    if str(row["number"]) == override or row["folder"] == override]
        if not selected:
            raise SystemExit(f"no eligible series numbered or in folder {override}")
        chosen_uid, chosen = selected[0]
    else:
        if not candidates:
            raise SystemExit("no eligible CT series found")
        chosen_uid, chosen = min(candidates, key=lambda item: (
            item[1]["spacing"],
            not bool(PREFERRED_KERNEL.search(item[1]["kernel"])),
            bool(CONTRAST_WORDS.search(item[1]["description"])),
            -item[1]["count"],
        ))
    for uid, row in candidates:
        if uid != chosen_uid:
            reasons[uid] = selection_reason(row, chosen)
    return chosen_uid, chosen, reasons


def selection_reason(row: dict, chosen: dict) -> str:
    if row["spacing"] > chosen["spacing"] + 0.05:
        return f"thicker slices ({row['spacing']:.2f} mm)"
    if row["spacing"] < chosen["spacing"] - 0.05:
        return f"thinner slices ({row['spacing']:.2f} mm)"
    row_preferred = bool(PREFERRED_KERNEL.search(row["kernel"]))
    chosen_preferred = bool(PREFERRED_KERNEL.search(chosen["kernel"]))
    if row_preferred != chosen_preferred:
        return f"{row['kernel']} kernel"
    phase = re.search(r"ARTERIAL|VENOUS|VENOS|TARDIV|ANGIO", row["description"], re.IGNORECASE)
    chosen_phase = re.search(r"ARTERIAL|VENOUS|VENOS|TARDIV|ANGIO", chosen["description"], re.IGNORECASE)
    if phase and not chosen_phase:
        return f"contrast phase ({phase.group(0).title()})"
    if row["count"] < chosen["count"]:
        return "fewer slices"
    if row["count"] > chosen["count"]:
        return f"more slices ({row['count']})"
    return "duplicate series at the same selection rank"


def next_case_id(root: Path) -> str:
    used = {int(m.group(1)) for p in root.glob("local-*")
            if (m := re.fullmatch(r"local-(\d{3})", p.name))}
    number = 1
    while number in used:
        number += 1
    return f"local-{number:03d}"


def series_uid_hash(uid: str) -> str:
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("src_dir", type=Path, nargs="?")
    parser.add_argument("--id")
    parser.add_argument("--series")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--backfill-hash", nargs=3, metavar=("CASE_ID", "SRC_DIR", "SERIES"))
    args = parser.parse_args()
    if args.backfill_hash:
        case_id, src_dir, series_name = args.backfill_hash
        series_dir = Path(src_dir) / series_name
        headers = sorted(path for path in series_dir.rglob("*") if path.is_file())
        if not headers:
            raise SystemExit(f"no DICOM header found in {series_name}")
        ds = pydicom.dcmread(headers[0], stop_before_pixels=True, force=False)
        series_hash = series_uid_hash(str(ds.SeriesInstanceUID))
        case_path = LOCAL_CASES / case_id / "case.json"
        document = json.loads(case_path.read_text())
        document.setdefault("source", {})["series_uid_sha256"] = series_hash
        case_path.write_text(json.dumps(document, indent=2) + "\n")
        print(f"Backfilled series hash for {case_id}")
        return
    if args.src_dir is None:
        parser.error("src_dir is required unless --backfill-hash is used")
    series, unreadable = read_series(args.src_dir)
    chosen_uid, chosen, reasons = pick_series(series, args.series)

    if not args.force:
        local_root = LOCAL_CASES
        for case_path in sorted(local_root.glob("*/case.json")):
            try:
                existing = json.loads(case_path.read_text())
                existing_hash = existing.get("source", {}).get("series_uid_sha256")
            except (OSError, json.JSONDecodeError):
                continue
            if existing_hash == chosen["series_uid_sha256"]:
                message = (f"already imported as {existing.get('id', case_path.parent.name)} "
                           f"(folder {chosen['folder']} / SeriesNumber {chosen['number']})")
                print(message)
                return

    for uid, row in sorted(series.items(), key=lambda item: item[1]["number"]):
        orientation = "axial" if row["axial"] else "other"
        print(f"{row['folder']} / SeriesNumber {row['number']} | "
              f"{row['description']} | {row['modality']} | "
              f"{row['sop_class']} | {row['kernel']} | "
              f"slice {row['slice_thickness']:.2f} mm | spacing {row['spacing']:.2f} mm | "
              f"{orientation} | {row['size']} | {row['count']} files")
    print(f"Skipped unreadable/non-DICOM files: {unreadable}")
    for uid, reason in sorted(reasons.items(), key=lambda item: series[item[0]]["number"]):
        print(f"Skipped {series[uid]['folder']} / SeriesNumber "
              f"{series[uid]['number']}: {reason}")
    case_id = args.id or next_case_id(LOCAL_CASES)
    print(f"Pick: folder {chosen['folder']} / SeriesNumber {chosen['number']} "
          f"({chosen['description']}) -> {case_id}")
    if args.dry_run:
        return

    output_dir = LOCAL_CASES / case_id
    output_dir.mkdir(parents=True, exist_ok=True)
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(chosen["files"])
    image = reader.Execute()
    chosen["size"] = list(image.GetSize())
    target = output_dir / f"{case_id}_CT.nii.gz"
    sitk.WriteImage(image, str(target))
    header = nib.load(target).header
    if any(bytes(header[key]).rstrip(b"\0") for key in ("descrip", "aux_file", "intent_name")):
        raise AssertionError("NIfTI header contains non-empty descriptive fields")
    document = {
        "id": case_id,
        "source": {
            "series_uid_sha256": chosen["series_uid_sha256"],
            "folder": chosen["folder"],
            "series_number": chosen["number"],
            "series_description": chosen["description"],
            "convolution_kernel": chosen["kernel"],
            "spacing_mm": [float(v) for v in image.GetSpacing()],
            "size": chosen["size"],
        },
        "skipped_series": [
            {"folder": series[uid]["folder"],
             "series_number": series[uid]["number"], "reason": reason}
            for uid, reason in sorted(reasons.items(),
                                      key=lambda item: series[item[0]]["number"])
        ],
        "import_tool_version": VERSION,
        "import_date": date.today().isoformat(),
    }
    (output_dir / "case.json").write_text(json.dumps(document, indent=2) + "\n")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
