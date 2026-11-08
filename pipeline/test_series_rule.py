# Changes: pin Archive selection and safe override behavior to the Series spec.
import json
from pathlib import Path

import pytest

from import_dicom import classify_series, kernel_class, pick_series
from series_rule import rank_key


def inventory():
    return json.loads((Path(__file__).parent / "fixtures/series_inventory.json").read_text())


def survivor(**changes):
    row = next(r for r in inventory() if " ".join(r["description"].split()) == "TAP 1.5 Bf37 3")
    return dict(row, **changes)


def test_inventory():
    rows = inventory()
    results = classify_series(rows)
    assert [" ".join(r["description"].split()) for r in results if r["status"] == "recommended"] == ["TAP 1.5 Bf37 3"]
    usable = sorted((r for r in results if r["status"] == "usable"), key=rank_key)
    assert [" ".join(r["description"].split()) for r in usable] == ["TAP 1.5 Br60 3", "Lung 1.5 Br60 3"]
    rejected = [r for r in results if r["status"] == "not_usable"]
    assert len(rejected) == 18
    for row in rejected:
        desc = row["description"].upper()
        reason = row["reason"]
        if "GAT NATIV" in desc:
            assert "coverage" in reason
        elif "RAYSCAPE" in desc:
            assert "derived" in reason
        elif "TOPOGRAM" in desc:
            assert "localizer" in reason
        elif "MPR" in desc:
            assert "reformat" in reason
        elif "5.0" in desc:
            assert "step" in reason
        elif "VENOS" in desc or "ARTERIAL" in desc:
            assert "contrast" in reason
        else:
            assert "CT" in reason


@pytest.mark.parametrize("kernel,expected", [
    (["Bf37f", "3"], "soft"),
    ("['Bf37f', '3']", "soft"),
    ("Bf37f\\3", "soft"),
    ("Br60f", "sharp"),
    ("Q49", "soft"),
    ("Q50", "sharp"),
    ("STANDARD", "soft"),
    ("SOFT", "soft"),
    ("BONEPLUS", "sharp"),
    ("EDGE", "sharp"),
    ("C", "soft"),
    ("YA", "sharp"),
    ("FC08", "soft"),
    ("FC18", "soft"),
    ("FC51", "sharp"),
    ("FC81", "sharp"),
    ("FC30", "unknown"),
    ("", "unknown"),
    ("OTHER", "unknown"),
])
def test_kernel(kernel, expected):
    assert kernel_class(kernel) == expected


def test_non_siemens_and_native_override():
    result = classify_series([survivor(manufacturer="GE MEDICAL SYSTEMS", kernel="STANDARD", description="Nativ Arterial", contrast_agent=True)])[0]
    assert result["status"] == "usable"
    assert result["contrast"] is False
    assert "untested" in result["reason"]


def test_ties_and_override():
    rows = {"b": survivor(number=9), "a": survivor(number=8)}
    assert pick_series(rows, None)[0] == "a"
    assert pick_series(dict(reversed(list(rows.items()))), None)[0] == "a"
    assert pick_series(rows, "9")[0] == "b"
    rows["b"]["spacing"] = 1.51
    with pytest.raises(SystemExit, match="step"):
        pick_series(rows, "9")


@pytest.mark.parametrize("changes,reason", [
    ({"sop_class": "Secondary Capture Image Storage"}, "CT"),
    ({"modality": "MR"}, "CT"),
    ({"image_type": ["ORIGINAL", "LOCALIZER"]}, "localizer"),
    ({"image_type": ["DERIVED", "SECONDARY"]}, "derived"),
    ({"description": "Rayscape report"}, "derived"),
    ({"description": "MPR sag"}, "reformat"),
    ({"axial": False}, "reformat"),
    ({"size": [512, 747, 369]}, "reformat"),
    ({"count": 99}, "100 slices"),
    ({"uniform": False}, "non-uniform"),
    ({"spacing": 1.5001}, "step"),
    ({"z_span_mm": 249.9}, "coverage"),
    ({"contrast_agent": True}, "contrast"),
    ({"description": "Venos TAP"}, "contrast"),
])
def test_rejection_rules(changes, reason):
    result = classify_series([survivor(**changes)])[0]
    assert result["status"] == "not_usable"
    assert reason in result["reason"]


@pytest.mark.parametrize("manufacturer,kernel", [
    ("GE MEDICAL SYSTEMS", "STANDARD"),
    ("Philips", "B"),
    ("Canon", "FC08"),
    ("", "unknown"),
])
def test_unvalidated_vendors_require_explicit_choice(manufacturer, kernel):
    row = survivor(manufacturer=manufacturer, kernel=kernel)
    assert classify_series([row])[0]["status"] == "usable"
    with pytest.raises(SystemExit, match="choose a usable"):
        pick_series({"key": row}, None)
    assert pick_series({"key": row}, str(row["number"]))[0] == "key"


def test_siemens_recommended_even_when_ge_ranks_first():
    rows = [survivor(manufacturer="GE", kernel="STANDARD", spacing=0.5),
            survivor(kernel="Br60f")]
    assert [r["status"] for r in classify_series(rows)] == ["usable", "recommended"]


def test_hashed_selector_disambiguates_duplicate_numbers():
    rows = {"a": survivor(number=8, series_uid_sha256="a" * 64),
            "b": survivor(number=8, series_uid_sha256="b" * 64)}
    with pytest.raises(SystemExit, match="ambiguous"):
        pick_series(rows, "8")
    assert pick_series(rows, "b" * 64)[0] == "b"


@pytest.mark.parametrize("better,worse", [
    ({"kernel": "Bf37f", "spacing": 1.5}, {"kernel": "Br60f", "spacing": 0.5}),
    ({"kernel": "unknown"}, {"kernel": "Br60f"}),
    ({"spacing": 0.8}, {"spacing": 1.0}),
    ({"z_span_mm": 400}, {"z_span_mm": 350}),
    ({"count": 400}, {"count": 350}),
])
def test_rank_priorities(better, worse):
    rows = {"worse": survivor(number=1, **worse),
            "better": survivor(number=9, **better)}
    assert pick_series(rows, None)[0] == "better"
