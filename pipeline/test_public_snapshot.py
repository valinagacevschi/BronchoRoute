# Changes: public snapshot allow-list, scanner failures, and the unpushed commit.
"""The public snapshot drops internal paths and refuses leaked identifiers."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "public-snapshot.py"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(),
    reason="scripts/public-snapshot.py is not part of the public tree",
)

DENIED = (
    ".codex-briefs",
    "docs",
    "deploy",
    "Dockerfile",
    ".dockerignore",
    ".gitlab-ci.yml",
    "sponsors.json",
    "scripts/container-smoke.sh",
    "scripts/stage-case-data.sh",
    "scripts/public-snapshot.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("public_snapshot", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("public-snapshot") / "tree"
    env = os.environ.copy()
    env["BRONCHOROUTE_PRIVATE_TERMS"] = "Testname"
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(out), "--ref", "HEAD"],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    return out, proc.stdout, proc.stderr


def test_refuses_out_dir_inside_source_repo():
    dest = REPO / "pipeline" / "tmp" / "public-snapshot-refuse"
    if dest.exists():
        shutil.rmtree(dest)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(dest)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "inside the source repo" in proc.stderr
    assert not dest.exists()


def test_refuses_non_empty_out_dir(tmp_path):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep").write_text("already here", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(dest)],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "not empty" in proc.stderr
    assert (dest / "keep").is_file()


def test_snapshot_contents(built):
    out, stdout, _stderr = built
    for rel in DENIED:
        assert not (out / rel).exists(), rel
    assert not (out / "desktop" / "node_modules").exists()
    assert not (out / "desktop" / "build").exists()
    assert not (out / "desktop" / "dist").exists()
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "BronchoRoute" in readme
    assert "Not a medical device" in readme or "not a medical device" in readme
    assert "GPL-3.0-or-later" in readme
    assert "git.xelasys.ro" not in readme
    assert "gitlab" not in readme.casefold()
    for name in ("LICENSE", "COPYING.md", "THIRD_PARTY_NOTICES.md"):
        path = out / name
        assert path.is_file() and path.stat().st_size > 0
    assert (out / "scripts" / "third_party_notices.py").is_file()
    assert (out / "prototype" / "renderer.template.html").is_file()
    # Splice tests read the rendered file. The server does not serve it.
    assert (out / "prototype" / "renderer.html").is_file()
    source_paths = (REPO / "pipeline" / "paths.py").read_text(encoding="utf-8")
    snapshot_paths = (out / "pipeline" / "paths.py").read_text(encoding="utf-8")
    assert "lung-nav.appcenter.ro" in source_paths
    assert "lung-nav.appcenter.ro" not in snapshot_paths
    assert "rewrite pipeline/paths.py:5:" in stdout
    assert ".codex-briefs/" in stdout
    assert "docs/" in stdout
    assert "scanner: clean" in stdout
    assert "push (not run):" in stdout
    assert "git push" not in stdout
    manifest = (out.parent / "SNAPSHOT_MANIFEST.txt").read_text(encoding="utf-8")
    source = subprocess.check_output(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    assert source in manifest
    assert "README.md" in manifest
    assert "Testname" not in manifest


def test_one_local_commit_and_no_push(built):
    out, _stdout, _stderr = built
    count = subprocess.check_output(
        ["git", "-C", str(out), "rev-list", "--count", "HEAD"],
        text=True,
    ).strip()
    assert count == "1"
    version = json.loads((REPO / "desktop" / "package.json").read_text(encoding="utf-8"))["version"]
    subject = subprocess.check_output(
        ["git", "-C", str(out), "log", "-1", "--format=%s"],
        text=True,
    ).strip()
    author = subprocess.check_output(
        ["git", "-C", str(out), "log", "-1", "--format=%an <%ae>"],
        text=True,
    ).strip()
    assert subject == f"BronchoRoute {version} — public snapshot"
    assert author == "Vali Nagacevschi <vali@appcenter.ro>"
    url = subprocess.check_output(
        ["git", "-C", str(out), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    assert url == "https://github.com/valinagacevschi/BronchoRoute.git"
    remote_branches = subprocess.check_output(
        ["git", "-C", str(out), "branch", "-r"],
        text=True,
    )
    assert remote_branches.strip() == ""
    upstream = subprocess.run(
        ["git", "-C", str(out), "rev-parse", "--abbrev-ref", "@{u}"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert upstream.returncode != 0


def test_scanner_passes_on_snapshot_and_fails_on_injected_copy(built, tmp_path):
    out, _stdout, _stderr = built
    module = _load()
    clean = module.scan_tree(out, private_terms=["Testname"])
    assert clean.hits == []

    copy = tmp_path / "copy"
    shutil.copytree(out, copy, ignore=shutil.ignore_patterns(".git"))
    (copy / "leak-host.txt").write_text("see https://git.xelasys.ro/vali\n", encoding="utf-8")
    (copy / "leak-term.txt").write_text("note Testname\n", encoding="utf-8")
    (copy / "leak-accent.txt").write_text("note Téstname\n", encoding="utf-8")
    (copy / "leak-uid.txt").write_text(
        "uid 1.2.840.113619.2.55.3.4271041578.9876.1234567890\n",
        encoding="utf-8",
    )
    (copy / "standard-uid.txt").write_text(
        "sop 1.2.840.10008.5.1.4.1.1.2\n",
        encoding="utf-8",
    )
    (copy / "leak.nii.gz").write_bytes(b"not a volume")
    (copy / "leak-big.dat").write_bytes(b"0" * (6 * 1024 * 1024))

    dirty = module.scan_tree(copy, private_terms=["Testname"])
    kinds = {(hit.path, hit.kind) for hit in dirty.hits}
    assert ("leak-host.txt", "host") in kinds
    assert ("leak-term.txt", "private-term") in kinds
    assert ("leak-accent.txt", "private-term") in kinds
    assert ("leak-uid.txt", "dicom-uid") in kinds
    assert ("leak.nii.gz", "medical-file") in kinds
    assert ("leak-big.dat", "large-file") in kinds
    assert ("standard-uid.txt", "dicom-uid") not in kinds
    rendered = "\n".join(module.format_hit(hit) for hit in dirty.hits)
    assert "Testname" not in rendered
    assert "Téstname" not in rendered
