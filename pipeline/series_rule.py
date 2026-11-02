# Changes: classify and rank header facts without reading source DICOM.
"""Keep Series eligibility identical for scanning and import overrides."""

from __future__ import annotations

import re

SCOUT = re.compile(r"SCOUT|LOCALI[SZ]ER|TOPOGRAM|SURVIEW|SCANOGRAM", re.I)
REFORMAT = re.compile(r"\bMPR\b|\bMIP\b|\bMINIP\b|\bVRT?\b|SAG|COR(?:ONAL)?\b|3D|THICK", re.I)
DERIVED_VENDOR = re.compile(r"RAYSCAPE|REPORT|SECONDARY|SCREEN ?SAVE|DOSE|SUMMARY", re.I)
CONTRAST = re.compile(r"ARTERIAL|ARTER|VENOUS|VENOS|PORTAL|TARDIV|DELAY|ANGIO|\bCTA\b|"
                      r"C\+|POST ?CONTRAST|\bKM\b|\bIV\b|FAZA", re.I)
NATIVE = re.compile(r"NATIV|NON[ -]?CONTRAST", re.I)
# Siemens sharpness numbers below 50 are soft; 50 and above are sharp.
# GE: STANDARD/SOFT soft, LUNG/BONE/BONEPLUS/DETAIL sharp. Philips: B/C soft, L/YA/YB sharp.
# Canon: FC0x/FC1x soft, FC5x/FC8x lung.
SOFT_NAME = re.compile(r"STANDARD|SOFT|^[BC]$|^[BCFI]R?[A-Z]?\d|^FC0|^FC1", re.I)
SHARP_NAME = re.compile(r"LUNG|BONE|DETAIL|EDGE|SHARP|^L$|^Y[ABC]|^FC5|^FC8", re.I)
SIEMENS_NUM = re.compile(r"^[A-Z]{1,3}(\d{2})[a-z]?", re.I)

MAX_SPACING_MM = 1.5
MIN_THORAX_Z_MM = 250.0       # adult apex-to-costophrenic ~ 250-300 mm
MIN_SLICES = 100


def first_kernel(kernel) -> str:
    if not isinstance(kernel, str):
        kernel = next(iter(kernel or []), "")
    return kernel.strip().strip("[]\'\"").split("\\")[0].split(",")[0].strip(" \'\"")


def kernel_class(kernel) -> str:
    k = first_kernel(kernel)
    if not k:
        return "unknown"
    # Canon FC numbers encode families, not Siemens sharpness.
    if re.match(r"^FC[01]\d", k, re.I):
        return "soft"
    if re.match(r"^FC[58]\d", k, re.I):
        return "sharp"
    if re.match(r"^FC", k, re.I):
        return "unknown"
    m = SIEMENS_NUM.match(k)
    if m:
        return "sharp" if int(m.group(1)) >= 50 else "soft"
    if SHARP_NAME.search(k):
        return "sharp"
    if SOFT_NAME.search(k):
        return "soft"
    return "unknown"


def has_contrast(row: dict) -> bool:
    return bool((CONTRAST.search(row["description"]) or row.get("contrast_agent"))
                and not NATIVE.search(row["description"]))


def reject_reason(r: dict) -> str | None:
    if r["sop_class"] != "CT Image Storage" or r["modality"] != "CT":
        if DERIVED_VENDOR.search(r["description"]):
            return "not a CT image (derived/vendor report)"
        return "not a CT image"
    if SCOUT.search(r["description"]) or "LOCALIZER" in [t.upper() for t in r.get("image_type", [])]:
        return "localizer"
    it = [t.upper() for t in r.get("image_type", [])]
    if "DERIVED" in it and any(tag in it for tag in ("SECONDARY", "REFORMATTED", "MPR", "MIP")):
        return "derived image/report"
    if DERIVED_VENDOR.search(r["description"]):
        return "derived/vendor report"
    if not r["axial"]:
        return "not axial (reformat)"
    if REFORMAT.search(r["description"]):
        return "reformat (MPR/MIP)"
    if r["size"][0] != r["size"][1]:
        return f"non-square matrix {r['size'][0]}x{r['size'][1]} (reformat/capture)"
    # Coverage and step explain the failure better than a resulting low slice count.
    if r["z_span_mm"] < MIN_THORAX_Z_MM:
        return f"z coverage {r['z_span_mm']:.0f} mm < {MIN_THORAX_Z_MM:.0f} mm (partial thorax / gated)"
    if r["spacing"] > MAX_SPACING_MM:
        return f"slice step {r['spacing']:.2f} mm > {MAX_SPACING_MM} mm"
    if r["count"] < MIN_SLICES:
        return f"fewer than {MIN_SLICES} slices"
    if not r["uniform"]:
        return "non-uniform slice spacing"
    if has_contrast(r):
        return "contrast phase"
    return None


def score(r: dict) -> tuple:
    """Lower is better. Kernel class, then thinner spacing, then longer coverage."""
    kc = kernel_class(r["kernel"])
    return (
        # Soft/standard first: on this archive Bf37 gave 11-17% more branches and
        # centreline than Br60 at identical geometry (research/series-kernel.md).
        {"soft": 0, "unknown": 1, "sharp": 2}[kc],
        r["spacing"],
        -r["z_span_mm"],
        -r["count"],
    )


def rank_key(row: dict) -> tuple:
    return (*score(row), int(row["number"]), row.get("series_uid_sha256", ""),
            row["description"], first_kernel(row["kernel"]))


def classify_series(rows: list[dict]) -> list[dict]:
    """Recommend only the vendor validated against real samples."""
    results = []
    for row in rows:
        reason = reject_reason(row)
        results.append(dict(row, status="not_usable" if reason else "usable",
                            reason=reason or "lower-ranked Series",
                            kernel_class=kernel_class(row["kernel"]),
                            contrast=has_contrast(row)))
    survivors = sorted((r for r in results if r["status"] == "usable"), key=rank_key)
    recommended = next((r for r in survivors
                        if "SIEMENS" in r.get("manufacturer", "").upper()), None)
    for row in survivors:
        if row is recommended:
            row["status"] = "recommended"
            row["reason"] = ""
        elif "SIEMENS" not in row.get("manufacturer", "").upper():
            row["reason"] = "non-Siemens kernel, untested"
    return results
