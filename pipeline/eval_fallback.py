"""Evaluate TotalSegmentator airway fallback masks on AeroPath cases 1–6."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WANT = [
    "trachea", "right main bronchus", "left main bronchus",
    "right upper lobe bronchus", "right middle lobe bronchus",
    "right lower lobe bronchus", "left upper lobe bronchus",
    "left lower lobe bronchus",
]


def run(command: list[str]) -> None:
    print("$", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def run_case(n: int, legacy: bool = False) -> Path:
    suffix = f"fallback_legacy_{n}" if legacy else f"fallback_{n}"
    out_dir = Path("ts_out") / suffix
    graph_dir = Path("out") / ("fallback_legacy" if legacy else "fallback")
    case_dir = Path("data/extracted/AeroPath") / str(n)
    ct = case_dir / f"{n}_CT_HR.nii.gz"
    truth = case_dir / f"{n}_CT_HR_label_airways.nii.gz"

    command = [sys.executable, "run_totalsegmentator.py", str(ct),
               "-o", str(out_dir)]
    if legacy:
        command.append("--legacy")
    run(command)
    run([sys.executable, "build_graph.py", str(case_dir), "--airways",
         str(out_dir / "airways.nii.gz"), "--lobes", str(out_dir / "lobes.nii.gz"),
         "-o", str(graph_dir)])
    run([sys.executable, "compare_segmentation.py", str(truth),
         str(out_dir / "airways.nii.gz")])
    return graph_dir / f"{n}_graph.json"


def print_report(rows: list[dict]) -> None:
    print("\n═══ fallback naming completeness ═══")
    print(f"{'case':>8}{'branches':>10}{'gens':>6}{'lobed':>7}{'root r':>9}{'named':>8}{'pass':>7}")
    for row in rows:
        print(f"{row['label']:>8}{row['branches']:>10}{row['gens']:>6}"
              f"{row['lobed']:>7}{row['root_r']:>8.1f}{len(row['found'] & set(WANT)):>6}/8"
              f"{('yes' if row['passed'] else 'NO'):>7}")

    print("\nper-structure, . = found, X = missing")
    for name in WANT:
        marks = "".join(" ." if name in row["found"] else " X" for row in rows)
        print(f"  {name:<28}{marks}")


def main() -> None:
    rows = []
    legacy_rows = []
    for n in range(1, 7):
        graph_path = run_case(n)
        graph = json.loads(graph_path.read_text())
        edges = graph["edges"]
        names = {edge["airway_name"] for edge in edges if edge["airway_name"]}
        passed = set(WANT) <= names
        rows.append({
            "label": str(n),
            "branches": len(edges),
            "gens": max(edge["generation"] for edge in edges),
            "lobed": sum(bool(edge["lobe"]) for edge in edges),
            "root_r": max(edge["radius_mm"] for edge in edges),
            "found": names,
            "passed": passed,
        })
        if not passed:
            print(f"\n═══ legacy retry: case {n} ═══")
            legacy_path = run_case(n, legacy=True)
            legacy_graph = json.loads(legacy_path.read_text())
            legacy_edges = legacy_graph["edges"]
            legacy_names = {edge["airway_name"] for edge in legacy_edges
                            if edge["airway_name"]}
            legacy_passed = set(WANT) <= legacy_names
            legacy_rows.append({
                "label": f"{n}L",
                "branches": len(legacy_edges),
                "gens": max(edge["generation"] for edge in legacy_edges),
                "lobed": sum(bool(edge["lobe"]) for edge in legacy_edges),
                "root_r": max(edge["radius_mm"] for edge in legacy_edges),
                "found": legacy_names,
                "passed": legacy_passed,
            })
            if not legacy_passed:
                print(f"MANUAL REVIEW: case {n}")

    print_report(rows + legacy_rows)


if __name__ == "__main__":
    main()
