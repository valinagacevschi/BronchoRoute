# Changes: spike — MONAI lung nodule detector run over existing cases.
"""Run the MONAI lung_nodule_ct_detection bundle over local CT cases.

Spike only. Not wired into the job pipeline. Writes one JSON per case with
detections in RAS mm, the box size, and whether the centre falls inside the
case lung mask.

Model: MONAI/lung_nodule_ct_detection, RetinaNet 3D on LUNA16/LIDC, ~80 MB.
Code is Apache-2.0; LUNA16 and LIDC data are CC BY 4.0 / CC BY 3.0. The bundle
card says "example, not to be used for diagnostic purposes".

Not a declared dependency. Install once, then download the bundle:
    uv pip install monai
    python -c "from huggingface_hub import snapshot_download as d; \
        d('MONAI/lung_nodule_ct_detection', local_dir='models/lung-nodule')"
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import nibabel as nib
import numpy as np
import torch
from monai.apps.detection.networks.retinanet_network import (
    RetinaNet,
    resnet_fpn_feature_extractor,
)
from monai.apps.detection.networks.retinanet_detector import RetinaNetDetector
from monai.apps.detection.transforms.dictionary import (
    AffineBoxToWorldCoordinated,
    ClipBoxToImaged,
    ConvertBoxModed,
)
from monai.apps.detection.utils.anchor_utils import AnchorGeneratorWithAnchorShape
from monai.networks.nets import resnet50
from monai.transforms import (
    Compose,
    EnsureChannelFirstd,
    EnsureTyped,
    LoadImaged,
    Orientationd,
    ScaleIntensityRanged,
    Spacingd,
)

BUNDLE = Path(__file__).resolve().parent / "models" / "lung-nodule"
PATCH = [256, 256, 128]
SCORE_THRESH = 0.02


def build_detector(device: torch.device, patch: list[int] = PATCH) -> RetinaNetDetector:
    backbone = resnet50(
        spatial_dims=3,
        n_input_channels=1,
        conv1_t_stride=[2, 2, 1],
        conv1_t_size=[7, 7, 7],
    )
    extractor = resnet_fpn_feature_extractor(backbone, 3, False, [1, 2], None)
    net = RetinaNet(
        spatial_dims=3,
        num_classes=1,
        num_anchors=3,
        feature_extractor=extractor,
        size_divisible=[16, 16, 8],
        use_list_output=False,
    )
    state = torch.load(BUNDLE / "models" / "model.pt", map_location="cpu",
                       weights_only=True)
    net.load_state_dict(state)
    net.to(device).eval()

    detector = RetinaNetDetector(
        network=net,
        anchor_generator=AnchorGeneratorWithAnchorShape(
            feature_map_scales=[1, 2, 4],
            base_anchor_shapes=[[6, 8, 4], [8, 6, 5], [10, 10, 6]],
        ),
        debug=False,
        spatial_dims=3,
        num_classes=1,
        size_divisible=[16, 16, 8],
    )
    detector.set_target_keys(box_key="box", label_key="label")
    detector.set_box_selector_parameters(
        score_thresh=SCORE_THRESH,
        topk_candidates_per_level=1000,
        nms_thresh=0.22,
        detections_per_img=300,
    )
    detector.set_sliding_window_inferer(
        roi_size=patch, overlap=0.25, sw_batch_size=1, mode="constant",
        device=str(device),
    )
    detector.eval()
    return detector


def preprocess() -> Compose:
    return Compose([
        LoadImaged(keys="image", reader="NibabelReader"),
        EnsureChannelFirstd(keys="image"),
        Orientationd(keys="image", axcodes="RAS"),
        Spacingd(keys="image", pixdim=[0.703125, 0.703125, 1.25]),
        ScaleIntensityRanged(
            keys="image", a_min=-1024.0, a_max=300.0,
            b_min=0.0, b_max=1.0, clip=True,
        ),
        EnsureTyped(keys="image"),
    ])


def postprocess() -> Compose:
    return Compose([
        ClipBoxToImaged(
            box_keys="box", label_keys="label",
            box_ref_image_keys="image", remove_empty=True,
        ),
        AffineBoxToWorldCoordinated(
            box_keys="box", box_ref_image_keys="image", affine_lps_to_ras=False,
        ),
        ConvertBoxModed(box_keys="box", src_mode="xyzxyz", dst_mode="cccwhd"),
    ])


def lung_mask_for(ct: Path) -> Path | None:
    """Prefer the TotalSegmentator lobes mask, fall back to the AeroPath label."""
    ref = Path("ts_out") / ct.parent.name / "lobes.nii.gz"
    if ref.is_file():
        return ref
    candidates = sorted(ct.parent.glob("*_label_lungs.nii.gz"))
    return candidates[0] if candidates else None


def inside_lung(point_ras: np.ndarray, mask: nib.Nifti1Image | None) -> bool | None:
    if mask is None:
        return None
    inv = np.linalg.inv(mask.affine)
    ijk = inv @ np.array([point_ras[0], point_ras[1], point_ras[2], 1.0])
    ijk = np.rint(ijk[:3]).astype(int)
    if any(i < 0 or i >= mask.shape[axis] for axis, i in enumerate(ijk)):
        return False
    return bool(np.asanyarray(mask.dataobj)[tuple(ijk)] > 0)


def run_case(ct: Path, detector, prep, post, device: torch.device) -> dict:
    started = time.monotonic()
    sample = prep({"image": str(ct)})
    image = sample["image"]
    with torch.no_grad():
        pred = detector([image], use_inferer=True)
    boxes = post({"image": image, "box": pred[0]["box"],
                  "label": pred[0]["label"],
                  "label_scores": pred[0]["label_scores"]})
    elapsed = time.monotonic() - started

    mask_path = lung_mask_for(ct)
    mask = nib.load(mask_path) if mask_path else None
    detections = []
    for box, score in zip(
        boxes["box"].detach().cpu().numpy(),
        boxes["label_scores"].detach().cpu().numpy(),
    ):
        cx, cy, cz, w, h, d = [float(v) for v in box]
        centre = np.array([cx, cy, cz])
        size_mm = [w, h, d]
        detections.append({
            "score": round(float(score), 4),
            "ras_mm": [round(v, 2) for v in centre],
            "size_mm": [round(v, 2) for v in size_mm],
            "diameter_mm": round(max(size_mm), 2),
            "inside_lung": inside_lung(centre, mask),
        })
    detections.sort(key=lambda item: item["score"], reverse=True)
    return {
        "case": ct.parent.name,
        "ct": str(ct),
        "device": str(device),
        "lung_mask": str(mask_path) if mask_path else None,
        "seconds": round(elapsed, 1),
        "detections": detections,
    }


def apply_to_case(result: dict, case_id: str, api: str, min_score: float) -> None:
    """Push detections into a case record as candidates for clinician review."""
    items = [
        {
            "ras_mm": d["ras_mm"],
            "diameter_mm": min(60.0, max(2.0, d["diameter_mm"])),
            "score": d["score"],
        }
        for d in result["detections"] if d["score"] >= min_score
    ][:20]
    if not items:
        print(f"  nothing above {min_score}; not applied")
        return
    body = json.dumps({
        "source": "monai_lung_nodule",
        "candidates": items,
    }).encode()
    req = urllib.request.Request(
        f"{api.rstrip('/')}/api/cases/{urllib.parse.quote(case_id)}/candidates",
        data=body,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        print(f"  applied {len(items)} candidates to {case_id} ({response.status})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cts", nargs="*", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("out/nodule-spike"))
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--summarize", action="store_true",
                    help="summarise existing results and exit")
    ap.add_argument("--apply", metavar="CASE_ID",
                    help="after each case, push detections to the local app as candidates")
    ap.add_argument("--api", default="http://127.0.0.1:8765")
    ap.add_argument("--apply-min-score", type=float, default=0.1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.summarize:
        summarize(args.out)
        return
    if not args.cts:
        ap.error("give at least one CT, or --summarize")

    device = torch.device(args.device)
    detector = build_detector(device)
    prep, post = preprocess(), postprocess()

    for ct in args.cts:
        print(f"{ct.parent.name}: running", flush=True)
        result = run_case(ct, detector, prep, post, device)
        target = args.out / f"{result['case']}.json"
        target.write_text(json.dumps(result, indent=2) + "\n")
        inside = sum(1 for d in result["detections"] if d["inside_lung"])
        print(f"  {len(result['detections'])} detections, {inside} inside lung, "
              f"{result['seconds']} s -> {target}", flush=True)
        if args.apply:
            apply_to_case(result, args.apply, args.api, args.apply_min_score)


THRESHOLDS = [0.5, 0.2, 0.1, 0.05, 0.02]


def summarize(out_dir: Path) -> dict:
    """Count detections per score threshold, split by lung containment."""
    rows = []
    for path in sorted(out_dir.glob("*.json")):
        if path.name == "summary.json":
            continue
        data = json.loads(path.read_text())
        detections = data.get("detections") or []
        per_threshold = {}
        for threshold in THRESHOLDS:
            kept = [d for d in detections if d["score"] >= threshold]
            inside = [d for d in kept if d.get("inside_lung")]
            per_threshold[f"{threshold:.2f}"] = {
                "detections": len(kept),
                "inside_lung": len(inside),
                "outside_lung": len(kept) - len(inside) if data.get("lung_mask") else None,
            }
        rows.append({
            "case": data.get("case"),
            "seconds": data.get("seconds"),
            "lung_mask": bool(data.get("lung_mask")),
            "per_threshold": per_threshold,
        })

    summary = {"thresholds": THRESHOLDS, "cases": rows}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"{'case':>10}  " + "  ".join(f">={t:.2f}" for t in THRESHOLDS))
    for row in rows:
        counts = "  ".join(
            f"{row['per_threshold'][f'{t:.2f}']['detections']:>6}"
            for t in THRESHOLDS
        )
        print(f"{row['case']:>10}  {counts}")
    return summary


if __name__ == "__main__":
    main()
