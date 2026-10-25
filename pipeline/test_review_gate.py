import json

from make_prototype_data import review_gate
from review_signoff import main as signoff_main, valid_reviewer


def flag(lobe="right middle lobe"):
    return {"kind": "small_lobe", "lobe": lobe, "ml": 12.0,
            "median_ml": 100.0}


def signoff(lobe="right middle lobe", decision="approved"):
    return {"kind": "small_lobe", "lobe": lobe, "decision": decision,
            "finding": "Reviewed against source mask", "reviewer": "Radiologist, A.B.",
            "date": "2026-10-25"}


def test_unresolved_flag_is_refused():
    resolved, unresolved, refused = review_gate([flag()], [])
    assert resolved == []
    assert unresolved == [flag()]
    assert refused


def test_approved_matching_signoff_resolves_flag():
    resolved, unresolved, refused = review_gate([flag()], [signoff()])
    assert resolved == [{"kind": "small_lobe", "lobe": "right middle lobe",
                         "finding": "Reviewed against source mask",
                         "reviewer": "Radiologist, A.B.", "date": "2026-10-25"}]
    assert unresolved == []
    assert not refused


def test_rejected_matching_signoff_remains_refused():
    resolved, unresolved, refused = review_gate([flag()], [signoff(decision="rejected")])
    assert resolved == []
    assert unresolved == [flag()]
    assert refused


def test_signoff_for_different_lobe_does_not_resolve():
    resolved, unresolved, refused = review_gate([flag()], [signoff(lobe="left upper lobe")])
    assert resolved == []
    assert unresolved == [flag()]
    assert refused


def test_allow_review_overrides_unresolved_and_rejected():
    resolved, unresolved, refused = review_gate(
        [flag()], [signoff(decision="rejected")], allow_review=True
    )
    assert resolved == []
    assert unresolved == [flag()]
    assert not refused


def test_reviewer_rejects_full_names():
    assert not valid_reviewer("John Smith")
    assert not valid_reviewer("Dr John Smith, J.S.")
    assert valid_reviewer("Radiologist, A.B.")


def test_signoff_cli_replaces_matching_decision_in_temporary_case(tmp_path, monkeypatch):
    case_dir = tmp_path / "data/extracted/local/local-test"
    case_dir.mkdir(parents=True)
    case_path = case_dir / "case.json"
    case_path.write_text(json.dumps({"id": "local-test", "review_signoff": [
        signoff(decision="rejected")
    ]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["review_signoff.py", "local-test",
        "--kind", "small_lobe", "--lobe", "right middle lobe",
        "--decision", "approved", "--finding", "Reviewed", "--reviewer",
        "Radiologist, A.B.", "--date", "2026-10-25"])

    signoff_main()

    record = json.loads(case_path.read_text())
    assert record["id"] == "local-test"
    assert record["review_signoff"] == [{
        "kind": "small_lobe", "lobe": "right middle lobe", "decision": "approved",
        "finding": "Reviewed", "reviewer": "Radiologist, A.B.", "date": "2026-10-25",
    }]
