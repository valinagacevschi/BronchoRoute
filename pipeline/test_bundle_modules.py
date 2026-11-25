# Changes: assert electron-builder extraResources cover reachable pipeline/app modules (#63).
"""Packaged app must ship every local module the bundled code imports."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from termcolor import cprint

pytestmark = pytest.mark.server

REPO = Path(__file__).resolve().parents[1]
DESKTOP_PACKAGE = REPO / "desktop" / "package.json"
PIPE = REPO / "pipeline"
APP = REPO / "app"

# Non-.py files the walk cannot see; keep in sync when loaders gain assets.
KNOWN_DATA_FILES = ("app/realism/brea-depth-profile.json",)


def _extra_resource(package: dict, from_path: str) -> dict:
    for entry in package["build"]["extraResources"]:
        if entry.get("from") == from_path:
            return entry
    raise AssertionError(f"no extraResources entry for {from_path!r}")


def _expand_filter(root: Path, patterns: list[str]) -> set[str]:
    """Expand electron-builder filters: a pattern without '/' is top-level only."""
    selected: set[str] = set()
    for pat in patterns:
        negate = pat.startswith("!")
        body = pat[1:] if negate else pat
        hits = {
            path.relative_to(root).as_posix()
            for path in root.glob(body)
            if path.is_file()
        }
        if negate:
            selected -= hits
        else:
            selected |= hits
    return {rel for rel in selected if "__pycache__" not in rel.split("/")}


def _bundled_sets(package: dict) -> tuple[set[str], set[str]]:
    """Return (pipeline-relative paths, app-relative paths) selected by filters."""
    pipe_entry = _extra_resource(package, "../pipeline")
    app_entry = _extra_resource(package, "../app")
    pipe_files = _expand_filter(PIPE, list(pipe_entry["filter"]))
    app_files = _expand_filter(APP, list(app_entry["filter"]))
    return pipe_files, app_files


def _repo_key(kind: str, rel: str) -> str:
    return f"{kind}/{rel}"


def _resolve_app(mod: str) -> Path | None:
    """Map 'app.foo.bar' to app/foo/bar.py or app/foo/bar/__init__.py."""
    if mod == "app":
        init = APP / "__init__.py"
        return init if init.is_file() else None
    if not mod.startswith("app."):
        return None
    parts = mod.split(".")[1:]
    base = APP.joinpath(*parts)
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    init = base / "__init__.py"
    return init if init.is_file() else None


def _resolve_pipeline(name: str) -> Path | None:
    candidate = PIPE / f"{name}.py"
    return candidate if candidate.is_file() else None


def _resolve_from(
    path: Path,
    module: str | None,
    level: int,
    name: str | None = None,
) -> Path | None:
    if level:
        # Relative import: climb `level` packages from this file's package.
        pkg = path.parent
        for _ in range(level - 1):
            pkg = pkg.parent
        if module:
            target = pkg.joinpath(*module.split("."))
        elif name:
            target = pkg / name
        else:
            return None
        if target.with_suffix(".py").is_file():
            return target.with_suffix(".py")
        init = target / "__init__.py"
        return init if init.is_file() else None
    if module is None:
        return None
    top = module.split(".", 1)[0]
    if top == "app":
        return _resolve_app(module)
    if "." in module:
        # Non-app dotted imports are third-party / stdlib.
        return None
    return _resolve_pipeline(module)


def _imports_in(path: Path) -> list[tuple[str | None, int, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str | None, int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, 0, []))
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names if alias.name != "*"]
            found.append((node.module, node.level, names))
    return found


def _local_targets(path: Path) -> list[Path]:
    targets: list[Path] = []
    for module, level, names in _imports_in(path):
        if level:
            resolved = _resolve_from(path, module, level)
            if resolved is not None:
                targets.append(resolved)
            elif module is None:
                for name in names:
                    resolved = _resolve_from(path, None, level, name)
                    if resolved is not None:
                        targets.append(resolved)
            continue
        if module is None:
            continue
        # import app.x.y  /  from app.x import y  /  import estimated_airway
        resolved = _resolve_from(path, module, 0)
        if resolved is not None:
            targets.append(resolved)
            continue
        # from estimated_airway import estimate_airway  (module is estimated_airway)
        # already handled; for `import a, b` each alias.name is a full module
    return targets


def _kind_and_rel(path: Path) -> tuple[str, str] | None:
    try:
        return "pipeline", path.relative_to(PIPE).as_posix()
    except ValueError:
        pass
    try:
        return "app", path.relative_to(APP).as_posix()
    except ValueError:
        return None


def _reachable_from(bundled_pipe: set[str], bundled_app: set[str]) -> set[str]:
    """Walk AST imports from bundled .py files; return repo keys of local modules."""
    queue: list[Path] = []
    for rel in bundled_pipe:
        if rel.endswith(".py"):
            queue.append(PIPE / rel)
    for rel in bundled_app:
        if rel.endswith(".py"):
            queue.append(APP / rel)

    seen: set[str] = set()
    reachable: set[str] = set()
    while queue:
        path = queue.pop()
        keyed = _kind_and_rel(path)
        if keyed is None:
            continue
        key = _repo_key(*keyed)
        if key in seen:
            continue
        seen.add(key)
        reachable.add(key)
        if not path.is_file():
            continue
        for target in _local_targets(path):
            tkey = _kind_and_rel(target)
            if tkey is None:
                continue
            queue.append(target)
    return reachable


def test_extra_resources_cover_reachable_local_modules():
    cprint("bundle module closure vs extraResources", "cyan")
    package = json.loads(DESKTOP_PACKAGE.read_text(encoding="utf-8"))
    bundled_pipe, bundled_app = _bundled_sets(package)
    bundled_keys = {_repo_key("pipeline", r) for r in bundled_pipe} | {
        _repo_key("app", r) for r in bundled_app
    }

    reachable = _reachable_from(bundled_pipe, bundled_app)
    missing = sorted(key for key in reachable if key not in bundled_keys)

    for rel in KNOWN_DATA_FILES:
        if rel not in bundled_keys:
            missing.append(rel)

    assert not missing, (
        "extraResources filters omit reachable local modules or known data files:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


if __name__ == "__main__":
    # Manual peek for debugging filter expansion.
    pkg = json.loads(DESKTOP_PACKAGE.read_text(encoding="utf-8"))
    pipe, app = _bundled_sets(pkg)
    print("pipeline", len(pipe), file=sys.stderr)
    print("app", sorted(app), file=sys.stderr)
