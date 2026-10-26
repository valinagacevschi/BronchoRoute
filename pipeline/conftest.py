# Real-data guard: fail the session if playwright/tests mutate production case.json /
# app_state / out/cases under the real pipeline tree, or create ~/.lungnav* paths.
from __future__ import annotations

from pathlib import Path

import pytest
from termcolor import cprint

REAL_PIPELINE = Path(__file__).resolve().parent
HOME = Path.home()


def _guard_targets() -> list[Path]:
    targets: list[Path] = []
    app_state = REAL_PIPELINE / "app_state"
    if app_state.is_dir():
        targets.extend(p for p in app_state.rglob("*") if p.is_file())
    out_cases = REAL_PIPELINE / "out" / "cases"
    if out_cases.is_dir():
        targets.extend(p for p in out_cases.rglob("*") if p.is_file())
    local = REAL_PIPELINE / "data" / "extracted" / "local"
    if local.is_dir():
        targets.extend(local.glob("*/case.json"))
    return targets


def _snapshot(paths: list[Path]) -> dict[str, tuple[int, int] | None]:
    out: dict[str, tuple[int, int] | None] = {}
    for path in paths:
        key = str(path.resolve())
        try:
            st = path.stat()
            out[key] = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            out[key] = None
    return out


def _lungnav_home_paths() -> list[Path]:
    return sorted(HOME.glob(".lungnav*"))


@pytest.fixture(scope="session", autouse=True)
def _real_data_mtime_guard():
    """Refuse silent writes into the real pipeline state / case.json / ~/.lungnav*."""
    before_paths = _guard_targets()
    before = _snapshot(before_paths)
    before_home = {str(p.resolve()) for p in _lungnav_home_paths()}
    cprint(f"real-data guard: watching {len(before)} paths under {REAL_PIPELINE}", "cyan")
    yield
    after_paths = _guard_targets()
    after = _snapshot(after_paths)
    changed: list[str] = []
    for key, prev in before.items():
        now = after.get(key)
        if now != prev:
            changed.append(key)
    for key in after:
        if key not in before:
            changed.append(key)
    if changed:
        sample = "\n  ".join(changed[:12])
        raise AssertionError(
            "real pipeline data changed during tests (isolation broken):\n  " + sample
        )
    settings = REAL_PIPELINE / "app_state" / "settings.json"
    if settings.is_file() and str(settings.resolve()) not in before:
        raise AssertionError(f"tests created real settings.json at {settings}")
    after_home = {str(p.resolve()) for p in _lungnav_home_paths()}
    new_home = sorted(after_home - before_home)
    if new_home:
        raise AssertionError(
            "tests created ~/.lungnav* paths (use fake HOME under tmp):\n  "
            + "\n  ".join(new_home[:12])
        )
