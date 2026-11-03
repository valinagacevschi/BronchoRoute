"""SNOMED CT concepts used by the airway graph export.

Source CSV (SlicerTotalSegmentator totalsegmentator_snomed_mapping.csv):
https://raw.githubusercontent.com/lassoan/SlicerTotalSegmentator/main/
TotalSegmentator/Resources/totalsegmentator_snomed_mapping.csv

LOBES is keyed by the integer label written into lobes.nii.gz (1..5).
"""

SNOMED_SOURCE = (
    "https://raw.githubusercontent.com/lassoan/SlicerTotalSegmentator/main/"
    "TotalSegmentator/Resources/totalsegmentator_snomed_mapping.csv"
)

# Codes and meanings copied from the source CSV above. Laterality is included
# where the source provides a distinct modifier; the right middle lobe has no
# modifier in the source row. `key` is the TotalSegmentator mask stem.
LOBES = {
    1: {
        "key": "lung_upper_lobe_left",
        "name": "left upper lobe",
        "snomed": {
            "code": "45653009",
            "meaning": "Upper lobe of lung",
            "laterality_code": "7771000",
        },
    },
    2: {
        "key": "lung_lower_lobe_left",
        "name": "left lower lobe",
        "snomed": {
            "code": "90572001",
            "meaning": "Lower lobe of lung",
            "laterality_code": "7771000",
        },
    },
    3: {
        "key": "lung_upper_lobe_right",
        "name": "right upper lobe",
        "snomed": {
            "code": "45653009",
            "meaning": "Upper lobe of lung",
            "laterality_code": "24028007",
        },
    },
    4: {
        "key": "lung_middle_lobe_right",
        "name": "right middle lobe",
        "snomed": {
            "code": "72481006",
            "meaning": "Middle lobe of right lung",
        },
    },
    5: {
        "key": "lung_lower_lobe_right",
        "name": "right lower lobe",
        "snomed": {
            "code": "90572001",
            "meaning": "Lower lobe of lung",
            "laterality_code": "24028007",
        },
    },
}

TRACHEA = {"code": "44567001", "meaning": "Trachea"}
