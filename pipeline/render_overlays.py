"""Render review overlays comparing expert and fallback airway masks."""

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np
from PIL import Image, ImageDraw


def orient_ras_slice(array: np.ndarray, superior_up: bool = True) -> np.ndarray:
    """Map canonical RAS plane axes to radiological screen directions."""
    view = array.T
    if superior_up:
        view = np.flip(view, axis=0)
    return np.flip(view, axis=1)


def trachea_toward_superior(mask: np.ndarray) -> bool:
    counts = np.count_nonzero(mask, axis=(0, 1))
    occupied = np.flatnonzero(counts)
    edge_size = max(1, len(occupied) // 10)
    inferior_end = np.max(counts[occupied[:edge_size]])
    superior_end = np.max(counts[occupied[-edge_size:]])
    return bool(superior_end >= inferior_end)


def fit_physical(image: np.ndarray, spacing: tuple[float, float], max_size=(1200, 1200)) -> Image.Image:
    height, width = image.shape[:2]
    physical_w = width * spacing[0]
    physical_h = height * spacing[1]
    scale = min(max_size[0] / physical_w, max_size[1] / physical_h)
    size = (max(1, round(physical_w * scale)), max(1, round(physical_h * scale)))
    return Image.fromarray(image).resize(size, Image.Resampling.NEAREST)


def draw_overlay(ct: np.ndarray, expert: np.ndarray | None, fallback: np.ndarray,
                 affine: np.ndarray, plane: str, index: int, case: str,
                 projection: bool = False) -> Image.Image:
    if plane == "coronal":
        ct_slice = (np.zeros((ct.shape[0], ct.shape[2]), dtype=ct.dtype)
                    if projection else ct[:, index, :])
        expert_slice = (np.any(expert, axis=1) if projection else expert[:, index, :]) if expert is not None else np.zeros_like(ct_slice, dtype=bool)
        fallback_slice = np.any(fallback, axis=1) if projection else fallback[:, index, :]
        spacing = tuple(nib.affines.voxel_sizes(affine)[[0, 2]])
    else:
        ct_slice = ct[:, :, index]
        expert_slice = expert[:, :, index] if expert is not None else np.zeros_like(ct_slice, dtype=bool)
        fallback_slice = fallback[:, :, index]
        spacing = tuple(nib.affines.voxel_sizes(affine)[[0, 1]])
    coronal_superior_up = trachea_toward_superior(
        fallback if expert is None else expert)
    ct_slice = orient_ras_slice(ct_slice, coronal_superior_up if plane == "coronal" else True)
    expert_slice = orient_ras_slice(expert_slice, coronal_superior_up if plane == "coronal" else True)
    fallback_slice = orient_ras_slice(fallback_slice, coronal_superior_up if plane == "coronal" else True)
    grey = (np.zeros(ct_slice.shape, dtype=np.uint8) if projection else
            np.clip((ct_slice.astype(np.float32) + 600 + 750) / 1500 * 255,
                    0, 255).astype(np.uint8))
    rgb = np.repeat(grey[..., None], 3, axis=2).astype(np.float32)
    both = expert_slice & fallback_slice
    colors = np.zeros((*grey.shape, 3), dtype=np.float32)
    colors[fallback_slice & ~expert_slice] = (255, 35, 35)
    colors[expert_slice & ~fallback_slice] = (0, 235, 255)
    colors[both] = (255, 230, 0)
    mask = expert_slice | fallback_slice
    rgb[mask] = rgb[mask] * 0.45 + colors[mask] * 0.55
    rendered = fit_physical(np.clip(rgb, 0, 255).astype(np.uint8), spacing)
    canvas = Image.new("RGB", (rendered.width, rendered.height + 48), "black")
    canvas.paste(rendered, (0, 48))
    draw = ImageDraw.Draw(canvas)
    plane_label = "coronal MIP" if projection else plane
    draw.text((8, 5), f"Case {case} | {plane_label} | slice {index}", fill="white")
    legend = ("fallback only (no expert mask): red" if expert is None else
              "Both: yellow   Fallback only: red   Expert only: cyan")
    draw.text((8, 25), legend, fill="white")
    return canvas


def render_case(case: str) -> list[Path]:
    local = case.startswith("local-")
    root = (Path("data/extracted/local") / case if local else
            Path("data/extracted/AeroPath") / case)
    ct_img = nib.as_closest_canonical(
        nib.load(root / (f"{case}_CT.nii.gz" if local else f"{case}_CT_HR.nii.gz")))
    expert_path = None if local else root / f"{case}_CT_HR_label_airways.nii.gz"
    expert_img = (nib.as_closest_canonical(nib.load(expert_path))
                  if expert_path else None)
    fallback_img = nib.as_closest_canonical(nib.load(
        Path("ts_out") / (case if local else f"fallback_{case}") / "airways.nii.gz"))
    ct = np.asanyarray(ct_img.dataobj)
    expert = np.asanyarray(expert_img.dataobj) > 0 if expert_img else None
    fallback = np.asanyarray(fallback_img.dataobj) > 0
    if ct.shape != fallback.shape or (expert is not None and ct.shape != expert.shape):
        raise ValueError(f"case {case}: CT and mask shapes differ")
    out_dir = Path("out/overlays") / str(case)
    out_dir.mkdir(parents=True, exist_ok=True)
    airway = fallback if expert is None else expert
    coordinates = np.argwhere(airway)
    if not len(coordinates):
        raise ValueError(f"case {case}: airway mask is empty")
    coronal = np.unique(np.linspace(coordinates[:, 1].min(), coordinates[:, 1].max(), 6).round().astype(int))
    if local:
        lung_z = coordinates[:, 2]
    else:
        lung_img = nib.as_closest_canonical(
            nib.load(root / f"{case}_CT_HR_label_lungs.nii.gz"))
        lung = np.asanyarray(lung_img.dataobj) > 0
        lung_z = np.where(lung)[2]
    axial = np.unique(np.linspace(lung_z.min(), lung_z.max(), 3).round().astype(int))
    outputs = []
    for plane, indices in (("coronal", coronal), ("axial", axial)):
        for index in indices:
            path = out_dir / f"{plane}_{index:04d}.png"
            draw_overlay(ct, expert, fallback, ct_img.affine, plane, int(index), case).save(path)
            outputs.append(path)
    mip_path = out_dir / "coronal_mip.png"
    draw_overlay(ct, expert, fallback, ct_img.affine, "coronal", 0, case,
                 projection=True).save(mip_path)
    outputs.append(mip_path)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="+")
    args = parser.parse_args()
    for case in args.cases:
        if not case.startswith("local-") and not case.isdigit():
            parser.error(f"invalid case id: {case}")
        for path in render_case(case):
            print(path)


if __name__ == "__main__":
    main()
