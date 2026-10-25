"""Evaluate TotalSegmentator airway fallback masks on AeroPath cases 1–6."""

import argparse
import json
import subprocess
import sys

from compare_segmentation import load, voxel_scores
from paths import AEROPATH, OUT, PIPELINE, TS_OUT

WANT = [
    "trachea", "right main bronchus", "left main bronchus",
    "right upper lobe bronchus", "right middle lobe bronchus",
    "right lower lobe bronchus", "left upper lobe bronchus",
    "left lower lobe bronchus",
]


def run(command: list[str]) -> None:
    print("$", " ".join(command), flush=True)
    subprocess.run(command, cwd=PIPELINE, check=True)


def ts_suffix(variant: str, n: int, legacy: bool) -> str:
    return f"{variant}_legacy_{n}" if legacy else f"{variant}_{n}"


def out_dir_for(variant: str, legacy: bool = False):
    return OUT / (f"{variant}_legacy" if legacy else variant)


def bridge_count(seg_dir) -> int:
    path = seg_dir / "airway_bridges.json"
    if not path.exists():
        return 0
    return len(json.loads(path.read_text()).get("bridges", []))


def score_case(n: int, variant: str, legacy: bool = False) -> dict:
    """Score one case from graph + airways already on disk."""
    seg_dir = TS_OUT / ts_suffix(variant, n, legacy)
    graph_path = out_dir_for(variant, legacy) / f"{n}_graph.json"
    truth = AEROPATH / str(n) / f"{n}_CT_HR_label_airways.nii.gz"
    graph = json.loads(graph_path.read_text())
    edges = graph["edges"]
    names = {edge["airway_name"] for edge in edges if edge["airway_name"]}
    named = len(names & set(WANT))
    scores = voxel_scores(load(truth), load(seg_dir / "airways.nii.gz"))
    label = f"{n}L" if legacy else str(n)
    return {
        "case": n,
        "label": label,
        "legacy": legacy,
        "branches": len(edges),
        "gens": max(edge["generation"] for edge in edges),
        "lobed": sum(bool(edge["lobe"]) for edge in edges),
        "root_r": max(edge["radius_mm"] for edge in edges),
        "found": sorted(names),
        "named": named,
        "passed": named == 8,
        "dice": scores["dice"],
        "sensitivity": scores["sensitivity"],
        "precision": scores["precision"],
        "bridges": bridge_count(seg_dir),
    }


def run_case(n: int, variant: str, legacy: bool = False,
             higher_order: bool = False) -> dict:
    suffix = ts_suffix(variant, n, legacy)
    seg_dir = TS_OUT / suffix
    graph_out = out_dir_for(variant, legacy)
    case_dir = AEROPATH / str(n)
    ct = case_dir / f"{n}_CT_HR.nii.gz"

    command = [sys.executable, "run_totalsegmentator.py", str(ct),
               "-o", str(seg_dir)]
    if legacy:
        command.append("--legacy")
    if higher_order:
        command.append("--higher-order")
    run(command)
    run([sys.executable, "build_graph.py", str(case_dir), "--airways",
         str(seg_dir / "airways.nii.gz"), "--lobes", str(seg_dir / "lobes.nii.gz"),
         "-o", str(graph_out)])
    row = score_case(n, variant, legacy=legacy)
    print(f"case {row['label']}: Dice {row['dice']:.3f}, "
          f"sensitivity {row['sensitivity']:.3f}, "
          f"precision {row['precision']:.3f}, branches {row['branches']}, "
          f"generations {row['gens']}, named {row['named']}/8, "
          f"bridges {row['bridges']}")
    return row


def print_report(rows: list[dict]) -> None:
    print("\n═══ fallback naming completeness ═══")
    print(f"{'case':>8}{'branches':>10}{'gens':>6}{'lobed':>7}"
          f"{'root r':>9}{'named':>8}{'pass':>7}")
    for row in rows:
        print(f"{row['label']:>8}{row['branches']:>10}{row['gens']:>6}"
              f"{row['lobed']:>7}{row['root_r']:>8.1f}{row['named']:>6}/8"
              f"{('yes' if row['passed'] else 'NO'):>7}")

    print("\nper-structure, . = found, X = missing")
    for name in WANT:
        marks = "".join(" ." if name in set(row["found"]) else " X" for row in rows)
        print(f"  {name:<28}{marks}")


def print_metrics_table(rows: list[dict]) -> None:
    print(f"\n{'case':>8}{'dice':>8}{'sens':>8}{'prec':>8}"
          f"{'branches':>10}{'gens':>6}{'named':>8}{'bridges':>9}")
    for row in rows:
        print(f"{row['label']:>8}{row['dice']:>8.3f}{row['sensitivity']:>8.3f}"
              f"{row['precision']:>8.3f}{row['branches']:>10}{row['gens']:>6}"
              f"{row['named']:>6}/8{row['bridges']:>9}")


def results_path(variant: str):
    return out_dir_for(variant) / "results.json"


def write_results(variant: str, higher_order: bool, rows: list[dict]) -> None:
    path = results_path(variant)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "variant": variant,
        "higher_order": higher_order,
        "cases": [row for row in rows if not row.get("legacy")],
        "legacy_cases": [row for row in rows if row.get("legacy")],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


def load_results(variant: str) -> dict:
    path = results_path(variant)
    if path.exists():
        return json.loads(path.read_text())
    # Rebuild from existing graphs + airways when possible.
    cases = []
    missing = []
    for n in range(1, 7):
        graph = out_dir_for(variant) / f"{n}_graph.json"
        airways = TS_OUT / f"{variant}_{n}" / "airways.nii.gz"
        if not graph.exists() or not airways.exists():
            missing.append(n)
            continue
        cases.append(score_case(n, variant))
    if missing or len(cases) != 6:
        how = (
            f"missing {path}; regenerate with:\n"
            f"  env -u PYTHONPATH uv run python eval_fallback.py "
            f"--variant {variant} [--higher-order]"
        )
        if missing:
            how += f"\n  (also missing artifacts for cases {missing})"
        sys.exit(how)
    print(f"note: {path} missing; scored existing graphs/airways for {variant}")
    payload = {"variant": variant, "higher_order": None, "cases": cases,
               "legacy_cases": []}
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def compare_variants(a: str, b: str) -> None:
    left = load_results(a)
    right = load_results(b)
    by_a = {row["case"]: row for row in left["cases"]}
    by_b = {row["case"]: row for row in right["cases"]}
    cases = sorted(set(by_a) & set(by_b))
    if len(cases) != 6:
        sys.exit(f"--compare needs cases 1–6 in both variants; "
                 f"have {sorted(set(by_a) & set(by_b))}")

    print(f"\n═══ compare {a} vs {b} ═══")
    print(f"{'case':>4}  {'dice':^15}  {'branches':^13}  {'gens':^11}  "
          f"{'named':^11}  {'bridges':^11}")
    print(f"{'':>4}  {a:>7}{b:>8}  {a:>6}{b:>7}  {a:>5}{b:>6}  "
          f"{a:>5}{b:>6}  {a:>5}{b:>6}")
    better_tree = 0
    all_named = True
    for n in cases:
        ra, rb = by_a[n], by_b[n]
        print(f"{n:>4}  {ra['dice']:>7.3f}{rb['dice']:>8.3f}  "
              f"{ra['branches']:>6}{rb['branches']:>7}  "
              f"{ra['gens']:>5}{rb['gens']:>6}  "
              f"{ra['named']:>3}/8{rb['named']:>4}/8  "
              f"{ra['bridges']:>5}{rb['bridges']:>6}")
        if rb["branches"] >= ra["branches"] and rb["gens"] >= ra["gens"]:
            better_tree += 1
        if rb["named"] < 8:
            all_named = False

    adopt = better_tree >= 5 and all_named
    print(f"\nadopt verdict ({b} vs {a}): "
          f">= branches and >= generations on {better_tree}/6 cases; "
          f"8/8 names on all 6: {'yes' if all_named else 'NO'}")
    print(f"adopt {b}? {'YES' if adopt else 'NO'}")


def evaluate(variant: str, higher_order: bool) -> None:
    if higher_order and variant == "fallback":
        sys.exit("--higher-order would overwrite the baseline; "
                 "pass a distinct --variant (e.g. --variant hor)")

    rows = []
    for n in range(1, 7):
        row = run_case(n, variant, higher_order=higher_order)
        rows.append(row)
        if not row["passed"]:
            print(f"\n═══ legacy retry: case {n} ═══")
            legacy_row = run_case(n, variant, legacy=True,
                                  higher_order=higher_order)
            rows.append(legacy_row)
            if not legacy_row["passed"]:
                print(f"MANUAL REVIEW: case {n}")

    print_report(rows)
    print_metrics_table(rows)
    write_results(variant, higher_order, rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="fallback")
    parser.add_argument("--higher-order", action="store_true")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"),
                        help="side-by-side table and adopt verdict from "
                             "out/<A>/results.json and out/<B>/results.json")
    args = parser.parse_args()
    if args.compare:
        compare_variants(args.compare[0], args.compare[1])
        return
    evaluate(args.variant, args.higher_order)


if __name__ == "__main__":
    main()
