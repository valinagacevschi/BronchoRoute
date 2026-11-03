from build_graph import exported_lobes, trachea_snomed
from snomed import LOBES, SNOMED_SOURCE, TRACHEA


def test_exported_lobe_codes_match_vendored_mapping():
    assert {row["name"]: row["snomed"] for row in exported_lobes()} == {
        info["name"]: info["snomed"] for info in LOBES.values()
    }
    assert {row["value"] for row in exported_lobes()} == set(LOBES)


def test_only_trachea_airway_gets_code():
    assert trachea_snomed("trachea") == TRACHEA
    assert trachea_snomed("right main bronchus") is None
    assert trachea_snomed(None) is None


def test_snomed_source_is_the_mapping_csv():
    assert SNOMED_SOURCE.endswith("totalsegmentator_snomed_mapping.csv")
