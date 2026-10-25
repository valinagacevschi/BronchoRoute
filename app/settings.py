"""Local clinician identity (role + initials) for record stamping."""

from __future__ import annotations

import json
from pathlib import Path

import paths
from atomic_json import atomic_write_json, json_file_lock
from review_signoff import valid_reviewer


def app_state_dir() -> Path:
    return paths.PIPELINE / "app_state"


def settings_path() -> Path:
    return app_state_dir() / "settings.json"


def clinician_string(role: str, initials: str) -> str:
    return f"{role.strip()}, {initials.strip()}"


def validate_clinician(role: str, initials: str) -> str:
    if any(ch in role for ch in "<>'\""):
        raise ValueError("role must not contain <>'\"")
    value = clinician_string(role, initials)
    if not valid_reviewer(value):
        raise ValueError(
            "clinician must be a role followed by comma and initials "
            "(e.g. Radiologist, A.B.)"
        )
    return value


def read_settings(path: Path | None = None) -> dict:
    target = settings_path() if path is None else path
    if not target.is_file():
        return {}
    return json.loads(target.read_text())


def get_clinician(path: Path | None = None) -> dict | None:
    data = read_settings(path)
    role = data.get("role")
    initials = data.get("initials")
    if not role or not initials:
        return None
    return {
        "role": role,
        "initials": initials,
        "clinician": clinician_string(role, initials),
    }


def put_clinician(role: str, initials: str, path: Path | None = None) -> dict:
    target = settings_path() if path is None else path
    validate_clinician(role, initials)
    payload = {"role": role.strip(), "initials": initials.strip()}
    with json_file_lock(target):
        atomic_write_json(target, payload)
    return {
        **payload,
        "clinician": clinician_string(payload["role"], payload["initials"]),
    }
