"""Artifact version chain stored on case.json.

Used by phase 4 workflow freeze/sign-off; phase 1 only exposes register_artifact
and GET /api/cases/{id}/versions.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import paths


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_to_pipeline(path: Path) -> str:
    resolved = path.resolve()
    return str(resolved.relative_to(paths.PIPELINE.resolve()))


def register_artifact(case: dict, name: str, path: Path | str) -> dict:
    """Append artifact to the open version, or open n+1 if the last is frozen."""
    artifact_path = Path(path)
    versions = case.setdefault("versions", [])
    entry = {
        "path": relative_to_pipeline(artifact_path),
        "sha256": sha256_file(artifact_path),
    }
    if versions and not versions[-1].get("frozen"):
        versions[-1].setdefault("artifacts", {})[name] = entry
    else:
        n = (versions[-1]["n"] + 1) if versions else 1
        versions.append({"n": n, "artifacts": {name: entry}, "frozen": False})
    return case


def current_version(case: dict) -> int | None:
    versions = case.get("versions") or []
    if not versions:
        return None
    return versions[-1].get("n")
