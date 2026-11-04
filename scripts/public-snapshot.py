#!/usr/bin/env python3
# Changes: deny pipeline/test_public_snapshot.py (private snapshot tooling test).
# Changes: history-free public snapshot — allow-list, host-comment strip, privacy scanner.
"""Build a BronchoRoute public snapshot from a git ref.

Exports tracked files at --ref, keeps the public allow-list, strips comment
lines that name the private hosts, and refuses the tree if the scanner hits.
Creates one local commit and a remote URL. Does not push.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from termcolor import cprint

REPO = Path(__file__).resolve().parents[1]
ORIGIN = "https://github.com/valinagacevschi/BronchoRoute.git"
AUTHOR_NAME = "Vali Nagacevschi"
AUTHOR_EMAIL = "vali@appcenter.ro"
MAX_BYTES = 5 * 1024 * 1024

KEEP_FILES = frozenset({
    ".gitignore",
    "COPYING.md",
    "GLOSSARY.md",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "scripts/third_party_notices.py",
})
KEEP_DIRS = frozenset({"app", "desktop", "pipeline", "prototype"})
LICENCE_FILES = ("LICENSE", "COPYING.md", "THIRD_PARTY_NOTICES.md")

# Comment lines naming these hosts are removed. Anywhere else, the build stops.
PRIVATE_HOSTS = ("git.xelasys.ro", "lung-nav.appcenter.ro")
HOST_TERMS = (
    "xelasys",
    "lung-nav.appcenter.ro",
    "/Users/",
    "hermes",
    "kubectl",
    "glab",
    "gitlab",
)
MEDICAL_SUFFIXES = frozenset({
    ".dcm", ".nii", ".npz", ".bin", ".zip", ".pkl", ".mha", ".nrrd",
})
UID_RE = re.compile(r"\b1\.2\.(?:826|840)\.[0-9.]{10,}")
FIXTURE_DATE_RE = re.compile(r"\b(?:19|20)\d{6}\b")
PATIENT_WORD_RE = re.compile(r"\b(?:patient|patientname)\b", re.IGNORECASE)
TRACKER_RE = re.compile(
    r"git\.xelasys\.ro|lung-nav\.appcenter\.ro|\bglab\b|gitlab",
    re.IGNORECASE,
)
HASH_COMMENT = frozenset({".py", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".conf"})
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

DICOM_ROOT_REASON = (
    "DICOM standard root 1.2.840.10008 (registry UID, or a synthetic suffix "
    "on that root in unit tests); not an organisation UID from an Archive"
)
SYNTHETIC_UID_REASON = (
    "synthetic Series UID on the pydicom test branch "
    "1.2.826.0.1.3680043.10.999; unit tests only"
)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    kind: str
    detail: str


@dataclass(frozen=True)
class LineWaiver:
    path: str
    line: str
    term: str
    reason: str


@dataclass(frozen=True)
class ScanResult:
    hits: list[Hit]
    waivers: tuple[str, ...]


class SnapshotError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# Exact source lines. A new line that merely resembles one of these still fails.
LINE_WAIVERS = (
    LineWaiver(
        "app/versions.py",
        r'_ABS_PATH_RE = re.compile(r"(?:/Users/|/home/|/tmp/|[A-Za-z]:\\)")',
        "/Users/",
        "regex rejects absolute paths in saved report text",
    ),
    LineWaiver(
        "desktop/lib.test.js",
        'resolveSaveImageBaseDir("   ", "/Users/someone/Pictures"),',
        "/Users/",
        "synthetic Pictures directory in the save-path unit test",
    ),
    LineWaiver(
        "desktop/lib.test.js",
        '"/Users/someone/Pictures",',
        "/Users/",
        "synthetic Pictures directory in the save-path unit test",
    ),
    LineWaiver(
        "desktop/lib.test.js",
        'saveImageDefaultPath("/Users/someone/Pictures", "local-001_endoluminal_20261102-0001.png"),',
        "/Users/",
        "synthetic Pictures directory in the save-path unit test",
    ),
    LineWaiver(
        "desktop/lib.test.js",
        'path.join("/Users/someone/Pictures", "BronchoRoute", "local-001_endoluminal_20261102-0001.png"),',
        "/Users/",
        "synthetic Pictures directory in the save-path unit test",
    ),
    LineWaiver(
        "pipeline/test_p4a_workflow.py",
        '("abs /Users/x/data/extracted/local/ct.nii.gz", "ct.nii.gz"),',
        "/Users/",
        "unit test asserts basename redaction of an absolute path",
    ),
    LineWaiver(
        "pipeline/test_p4d_walkthrough_signoff.py",
        '"statement": "Saved under /Users/val/secret/path",',
        "/Users/",
        "sign-off input the report must reject; the test checks the path is absent",
    ),
    LineWaiver(
        "pipeline/test_p4d_walkthrough_signoff.py",
        'assert not re.search(r"/Users/|/home/|/tmp/", text)',
        "/Users/",
        "assertion that a report contains no home-directory path",
    ),
    LineWaiver(
        "pipeline/test_server_settings.py",
        'assert redact_log_line("/Users/x/Downloads/CT-1.zip") == "[redacted]"',
        "/Users/",
        "unit test asserts log redaction of an absolute path",
    ),
    LineWaiver(
        "pipeline/fixtures/series_inventory.json",
        '"description": "Patient Protocol",',
        "fixture-patient",
        "Siemens series description in the header inventory, not a patient name",
    ),
    LineWaiver(
        "THIRD_PARTY_NOTICES.md",
        "- pygltflib 1.16.5 — MIT — https://gitlab.com/dodgyville/pygltflib",
        "gitlab",
        "upstream pygltflib project URL on gitlab.com, not the private GitLab",
    ),
)


def fold(text: str) -> str:
    """Casefold and strip accents so a private term matches either spelling."""
    pieces = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in pieces if not unicodedata.combining(ch))
    return stripped.casefold()


def private_terms_from_env(env: dict[str, str] | None = None) -> list[str]:
    raw = (env if env is not None else os.environ).get("BRONCHOROUTE_PRIVATE_TERMS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def refuse_destination(out: Path, repo: Path) -> None:
    if is_inside(out, repo):
        raise SnapshotError(2, f"refusing {out}: it is inside the source repo {repo}")
    if out.exists() and not out.is_dir():
        raise SnapshotError(2, f"refusing {out}: not a directory")
    if out.is_dir() and any(out.iterdir()):
        raise SnapshotError(2, f"refusing {out}: directory exists and is not empty")


def exclusion_reason(rel: str, text: str | None = None) -> str | None:
    """None means the path stays. A string is why it was dropped."""
    parts = rel.split("/")
    if rel == "pipeline/test_public_snapshot.py":
        return "private snapshot tooling test"
    if rel.startswith("pipeline/fixtures/realism/"):
        return "realism port reference arrays (.npz); the scanner bans binary arrays"
    if parts[0] in KEEP_DIRS or rel in KEEP_FILES:
        if parts[0] == "desktop" and set(parts[1:]) & {"node_modules", "build", "dist"}:
            return "desktop dependency or build output (node_modules, build, or dist)"
        if (
            rel.startswith("pipeline/")
            and rel.endswith(".md")
            and text is not None
            and TRACKER_RE.search(text)
        ):
            return "pipeline note references the private tracker"
        return None
    if rel.startswith(".codex-briefs/"):
        return "internal agent brief"
    if rel.startswith("docs/"):
        return "internal product, design, evidence, and issue-tracker notes"
    if rel.startswith("deploy/") or rel in {"Dockerfile", ".dockerignore", ".gitlab-ci.yml"}:
        return "private lung-nav web deploy"
    if rel == "sponsors.json":
        return "internal sponsorship file"
    if rel.startswith("scripts/"):
        return "operator script; the public tree keeps scripts/third_party_notices.py only"
    return "not on the public allow-list"


def _comment_kind(rel: str) -> str:
    name = Path(rel).name
    suffix = Path(rel).suffix.lower()
    if suffix in HASH_COMMENT or name == ".gitignore":
        return "hash"
    if suffix in {".js", ".css", ".mjs", ".cjs"}:
        return "slash"
    if suffix in {".html", ".md", ".xml", ".plist"}:
        return "html"
    return ""


def is_comment_line(rel: str, line: str) -> bool:
    stripped = line.strip()
    kind = _comment_kind(rel)
    if kind == "hash":
        return stripped.startswith("#")
    if kind == "slash":
        return stripped.startswith("//") or (stripped.startswith("/*") and stripped.endswith("*/"))
    if kind == "html":
        return stripped.startswith("<!--") and stripped.endswith("-->")
    return False


def _hosts_in(line: str) -> list[str]:
    folded = line.casefold()
    return [host for host in PRIVATE_HOSTS if host.casefold() in folded]


def rewrite_private_hosts(root: Path) -> list[tuple[str, int, str]]:
    """Drop comment lines that name a private host. Fail on any other hit.

    Rewrites files under root only. The source checkout is untouched.
    """
    rewrites: list[tuple[str, int, str]] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        if b"\0" in raw[:4096]:
            for host in PRIVATE_HOSTS:
                if host.encode("ascii") in raw:
                    raise SnapshotError(
                        3,
                        f"private host in non-comment file {rel} ({host})",
                    )
            continue
        text = raw.decode("utf-8")
        kept: list[str] = []
        changed = False
        for number, line in enumerate(text.splitlines(keepends=True), 1):
            found = _hosts_in(line)
            if not found:
                kept.append(line)
                continue
            if is_comment_line(rel, line):
                changed = True
                shown = line.strip()
                rewrites.append((rel, number, shown))
                cprint(f"rewrite {rel}:{number}: {shown}", "yellow")
                continue
            raise SnapshotError(
                3,
                f"private host in non-comment code {rel}:{number}: {found[0]}",
            )
        if changed:
            path.write_text("".join(kept), encoding="utf-8", newline="\n")
    if not rewrites:
        cprint("rewrite: none", "green")
    return rewrites


def _line_waiver(rel: str, stripped: str, term: str) -> str | None:
    for waiver in LINE_WAIVERS:
        if waiver.path == rel and waiver.line == stripped and waiver.term == term:
            return waiver.reason
    return None


def _uid_waiver(uid: str) -> str | None:
    if uid.startswith("1.2.840.10008."):
        return DICOM_ROOT_REASON
    if "1.2.826.0.1.3680043.10.999." in uid:
        return SYNTHETIC_UID_REASON
    return None


def _is_fixture_json(rel: str) -> bool:
    return rel.casefold().endswith(".json") and "/fixtures/" in f"/{rel.casefold()}"


def _medical_suffix(name: str) -> str | None:
    folded = name.casefold()
    if folded.endswith(".nii.gz"):
        return ".nii.gz"
    suffix = Path(folded).suffix
    if suffix in MEDICAL_SUFFIXES:
        return suffix
    return None


def _files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if ".git" in path.relative_to(root).parts:
            continue
        found.append(path)
    found.sort(key=lambda item: item.relative_to(root).as_posix())
    return found


def scan_tree(root: Path, private_terms: list[str] | None = None) -> ScanResult:
    terms = private_terms if private_terms is not None else private_terms_from_env()
    folded_terms = [(term, fold(term)) for term in terms if fold(term)]
    hits: list[Hit] = []
    waiver_reasons: list[str] = []
    seen_waivers: set[str] = set()

    def waive(reason: str) -> None:
        if reason not in seen_waivers:
            seen_waivers.add(reason)
            waiver_reasons.append(reason)

    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        size = path.stat().st_size
        if size > MAX_BYTES:
            hits.append(Hit(rel, 0, "large-file", f"{size} bytes > {MAX_BYTES}"))
        medical = _medical_suffix(path.name)
        if medical:
            hits.append(Hit(rel, 0, "medical-file", medical))
        raw = path.read_bytes()
        if b"\0" in raw[:4096]:
            folded_bytes = raw.casefold() if raw.isascii() else raw.lower()
            for term in HOST_TERMS:
                token = term.casefold().encode("utf-8")
                if token in folded_bytes:
                    hits.append(Hit(rel, 0, "host", term))
            continue
        text = raw.decode("utf-8")
        fixture = _is_fixture_json(rel)
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            for term in HOST_TERMS:
                if term.casefold() not in line.casefold():
                    continue
                reason = _line_waiver(rel, stripped, term)
                if reason:
                    waive(reason)
                    continue
                hits.append(Hit(rel, number, "host", term))
            folded_line = fold(line)
            for original, folded_term in folded_terms:
                if folded_term in folded_line:
                    hits.append(Hit(rel, number, "private-term", ""))
                    break
            for match in UID_RE.finditer(line):
                uid = match.group(0)
                reason = _uid_waiver(uid)
                if reason:
                    waive(reason)
                    continue
                hits.append(Hit(rel, number, "dicom-uid", uid))
                break
            if not fixture:
                continue
            if FIXTURE_DATE_RE.search(line):
                hits.append(Hit(rel, number, "fixture-date", FIXTURE_DATE_RE.search(line).group(0)))
            if PATIENT_WORD_RE.search(line):
                reason = _line_waiver(rel, stripped, "fixture-patient")
                if reason:
                    waive(reason)
                else:
                    hits.append(Hit(rel, number, "fixture-patient", "patient"))
    hits.sort(key=lambda hit: (hit.path, hit.line, hit.kind, hit.detail))
    return ScanResult(hits, tuple(waiver_reasons))


def format_hit(hit: Hit) -> str:
    place = f"{hit.path}:{hit.line}" if hit.line else hit.path
    if hit.kind == "private-term":
        return f"{place}: private-term"
    return f"{place}: {hit.kind} {hit.detail}".rstrip()


def _read_version(tree: Path) -> str:
    package = json.loads((tree / "desktop" / "package.json").read_text(encoding="utf-8"))
    version = str(package["version"])
    if not VERSION_RE.fullmatch(version):
        raise SnapshotError(5, f"desktop/package.json version is not x.y.z: {version}")
    return version


def _apply_allow_list(root: Path) -> list[tuple[str, str]]:
    excluded: list[tuple[str, str]] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        text = None
        if rel.startswith("pipeline/") and rel.endswith(".md"):
            text = path.read_text(encoding="utf-8")
        reason = exclusion_reason(rel, text)
        if reason is None:
            continue
        excluded.append((rel, reason))
        path.unlink()
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        if directory == root or any(directory.iterdir()):
            continue
        directory.rmdir()
    excluded.sort()
    return excluded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _git(args: list[str], cwd: Path) -> None:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(args, cwd=cwd, check=True, env=env)


def _commit_snapshot(out: Path, version: str) -> str:
    _git(["git", "-c", "init.templateDir=", "init", "-b", "main"], out)
    _git(["git", "add", "-A"], out)
    _git(
        [
            "git",
            "-c", f"user.name={AUTHOR_NAME}",
            "-c", f"user.email={AUTHOR_EMAIL}",
            "-c", "commit.gpgsign=false",
            "commit",
            "-m", f"BronchoRoute {version} — public snapshot",
        ],
        out,
    )
    _git(["git", "remote", "add", "origin", ORIGIN], out)
    sha = subprocess.check_output(
        ["git", "-C", str(out), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    return sha


def _write_manifest(
    manifest: Path,
    files: list[Path],
    root: Path,
    source: str,
    ref: str,
    version: str,
    snapshot_commit: str,
) -> None:
    lines = [
        "BronchoRoute public snapshot",
        f"source_commit: {source}",
        f"source_ref: {ref}",
        f"version: {version}",
        f"snapshot_commit: {snapshot_commit}",
        f"files: {len(files)}",
        "",
    ]
    for path in files:
        rel = path.relative_to(root).as_posix()
        lines.append(f"{_sha256(path)}  {rel}")
    lines.append("")
    manifest.write_text("\n".join(lines), encoding="utf-8")


def _safe_remove(out: Path, repo: Path) -> None:
    if not out.exists():
        return
    resolved = out.resolve()
    source = repo.resolve()
    if resolved == source or is_inside(resolved, source) or is_inside(source, resolved):
        cprint(f"not deleting {resolved}", "red", file=sys.stderr)
        return
    shutil.rmtree(resolved)
    cprint(f"removed incomplete snapshot {resolved}", "yellow", file=sys.stderr)


def build(out: Path, ref: str, version: str | None) -> Path:
    repo = REPO
    refuse_destination(out, repo)
    readme_src = repo / "public" / "README.md"
    if not readme_src.is_file():
        raise SnapshotError(5, f"missing {readme_src}")
    source = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", f"{ref}^{{commit}}"],
        text=True,
    ).strip()
    cprint(f"source: {source} ({ref})", "cyan")
    out.mkdir(parents=True, exist_ok=True)
    cprint(f"git archive {source}", "cyan")
    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", source],
        check=True,
        stdout=subprocess.PIPE,
    )
    subprocess.run(["tar", "-x", "-C", str(out)], input=archive.stdout, check=True)
    excluded = _apply_allow_list(out)
    cprint(f"excluded: {len(excluded)}", "yellow")
    for rel, reason in excluded:
        cprint(f"  {rel} — {reason}", "yellow")
    pipeline_notes = [rel for rel, reason in excluded if reason.startswith("pipeline note")]
    if not pipeline_notes:
        cprint("deny-list: no pipeline markdown notes reference the private tracker", "green")
    cprint(
        "renderer.html: kept — desktop/lib.test.js and "
        "pipeline/test_topology_rotation_playwright.py read it; "
        "app/server.py serves renderer.template.html. "
        "The embed is the anonymized AeroPath graph (no name, UID, or date).",
        "green",
    )
    shutil.copyfile(readme_src, out / "README.md")
    cprint("README.md: copied from public/README.md", "cyan")
    rewrite_private_hosts(out)
    result = scan_tree(out, private_terms_from_env())
    if result.hits:
        for hit in result.hits:
            cprint(f"scanner: {format_hit(hit)}", "red", file=sys.stderr)
        raise SnapshotError(4, f"scanner: {len(result.hits)} hit(s)")
    cprint(f"scanner: clean ({len(_files(out))} files)", "green")
    for reason in result.waivers:
        cprint(f"  waiver: {reason}", "cyan")
    for name in LICENCE_FILES:
        path = out / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SnapshotError(5, f"licence file missing at snapshot root: {name}")
    cprint("licence: LICENSE, COPYING.md, THIRD_PARTY_NOTICES.md", "green")
    chosen = version or _read_version(out)
    if not VERSION_RE.fullmatch(chosen):
        raise SnapshotError(5, f"version is not x.y.z: {chosen}")
    files = _files(out)
    cprint(f"files: {len(files)}", "green")
    snapshot_commit = _commit_snapshot(out, chosen)
    cprint(f"commit: {snapshot_commit}", "green")
    cprint(f"remote: origin {ORIGIN}", "green")
    manifest = out.parent / "SNAPSHOT_MANIFEST.txt"
    _write_manifest(manifest, files, out, source, ref, chosen, snapshot_commit)
    cprint(f"manifest: {manifest}", "cyan")
    push = f"git -C {out} push -u origin main"
    cprint(f"push (not run): {push}", "yellow")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a local BronchoRoute public snapshot. Does not push.")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--version", default=None, help="snapshot commit version (default: desktop/package.json)")
    args = parser.parse_args(argv)
    out = args.out_dir
    created = False
    try:
        if not out.exists():
            created = True
        elif out.is_dir() and not any(out.iterdir()):
            created = True
        build(out, args.ref, args.version)
    except SnapshotError as exc:
        cprint(str(exc), "red", file=sys.stderr)
        if created:
            _safe_remove(out, REPO)
        return exc.code
    except subprocess.CalledProcessError as exc:
        cprint(f"command failed: {exc.cmd}", "red", file=sys.stderr)
        if created:
            _safe_remove(out, REPO)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
