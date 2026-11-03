# Changes: allow filevault_note_shown on the shared settings API.
# Changes: allow ram_warning_shown on the public settings allowlist.
# Changes: one atomic settings store preserves switches and unknown fields.
# Changes: app_state under LUNGNAV_CACHE_DIR when set (RO data root).
"""Local clinician identity (role + initials) and per-clinician CT view prefs.

Phase 3 review: clinician_prefs map keyed by "role, initials"; ClinicianNotSet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import paths
from atomic_json import atomic_write_json, json_file_lock
from review_signoff import valid_reviewer

CT_VIEWS = frozenset({"feet", "head"})
DEFAULT_CT_VIEW = "feet"


class ClinicianNotSet(ValueError):
    """No clinician identity is stored; maps to HTTP 412."""


def app_state_dir() -> Path:
    if paths.CACHE_DIR is not None:
        return paths.CACHE_DIR / "app_state"
    return paths.PIPELINE / "app_state"


def settings_path() -> Path:
    return app_state_dir() / "settings.json"


def clinician_string(role: str, initials: str) -> str:
    letters = "".join(ch.upper() + "." for ch in initials if ch.isalpha())
    value = f"{role.strip() or 'Clinician'}, {letters}"
    # Free-text roles need not fit the legacy reviewer grammar.
    if not valid_reviewer(value):
        value = f"Clinician, {letters}"
    return value


def validate_clinician(role: str, initials: str) -> str:
    if not role or not role.strip() or not initials or not initials.strip():
        raise ValueError("role and initials must be non-empty")
    if any(ch in role for ch in "<>'\""):
        raise ValueError("role must not contain <>'\"")
    value = f"{role.strip()}, {initials.strip()}"
    if not valid_reviewer(value):
        raise ValueError(
            "clinician must be a role followed by comma and initials "
            "(e.g. Radiologist, A.B.)"
        )
    return value


def validate_ct_view(value: str) -> str:
    if value not in CT_VIEWS:
        raise ValueError(f"ct_view must be one of {sorted(CT_VIEWS)}")
    return value


def read_settings(path: Path | None = None) -> dict:
    target = settings_path() if path is None else path
    if not target.is_file():
        return {}
    return json.loads(target.read_text())


def _prefs_map(data: dict) -> dict[str, dict]:
    raw = data.get("clinician_prefs")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for key, pref in raw.items():
        if not isinstance(key, str) or not isinstance(pref, dict):
            continue
        view = pref.get("ct_view", DEFAULT_CT_VIEW)
        if view not in CT_VIEWS:
            view = DEFAULT_CT_VIEW
        out[key] = {**pref, "ct_view": view}
    return out


def _view_for_clinician(data: dict, clinician: str) -> str:
    prefs = _prefs_map(data)
    if clinician in prefs:
        return prefs[clinician]["ct_view"]
    # Legacy single ct_view field when it belongs to the current clinician.
    legacy = data.get("ct_view")
    if (
        legacy in CT_VIEWS
        and data.get("role")
        and data.get("initials")
        and clinician_string(str(data["role"]), str(data["initials"])) == clinician
    ):
        return legacy
    return DEFAULT_CT_VIEW


def get_clinician(path: Path | None = None) -> dict | None:
    data = read_settings(path)
    role = data.get("role", "")
    initials = data.get("initials")
    if not initials:
        return None
    clinician = clinician_string(role, initials)
    return {
        "role": role,
        "initials": initials,
        "clinician": clinician,
        "ct_view": _view_for_clinician(data, clinician),
    }


def put_clinician(
    role: str,
    initials: str,
    path: Path | None = None,
    *,
    ct_view: str | None = None,
) -> dict:
    target = settings_path() if path is None else path
    validate_clinician(role, initials)
    with json_file_lock(target):
        existing = read_settings(target)
        prefs = _prefs_map(existing)
        key = clinician_string(role, initials)
        if ct_view is not None:
            view = validate_ct_view(ct_view)
        elif key in prefs:
            view = prefs[key]["ct_view"]
        else:
            view = DEFAULT_CT_VIEW
        prefs[key] = {**prefs.get(key, {}), "ct_view": view}
        payload: dict[str, Any] = {
            **existing,
            "role": role.strip(),
            "initials": initials.strip(),
            "ct_view": view,
            "clinician_prefs": prefs,
        }
        atomic_write_json(target, payload)
    return {
        "role": payload["role"],
        "initials": payload["initials"],
        "clinician": key,
        "ct_view": view,
    }


def put_ct_view(ct_view: str, path: Path | None = None) -> dict:
    """Update only ct_view for the current clinician; requires identity."""
    target = settings_path() if path is None else path
    view = validate_ct_view(ct_view)
    with json_file_lock(target):
        existing = read_settings(target)
        role = existing.get("role", "")
        initials = existing.get("initials")
        if not initials:
            raise ClinicianNotSet("clinician not set")
        key = clinician_string(role, initials)
        prefs = _prefs_map(existing)
        prefs[key] = {**prefs.get(key, {}), "ct_view": view}
        payload = {
            **existing,
            "role": role,
            "initials": initials,
            "ct_view": view,
            "clinician_prefs": prefs,
        }
        atomic_write_json(target, payload)
    return {
        "role": role,
        "initials": initials,
        "clinician": key,
        "ct_view": view,
    }


PUBLIC_DEFAULTS = {
    "initials": "",
    "role": "",
    "updates_enabled": True,
    "disclaimer_ack": False,
    "filevault_note_shown": False,
    "ram_warning_shown": False,
}


def get_settings(path: Path | None = None) -> dict:
    data = read_settings(path)
    return {key: data.get(key, default) for key, default in PUBLIC_DEFAULTS.items()}


def put_settings(patch: dict, path: Path | None = None) -> dict:
    if not isinstance(patch, dict) or patch.keys() - PUBLIC_DEFAULTS.keys():
        raise ValueError("unknown settings fields")
    for key, value in patch.items():
        if key == "initials":
            if not isinstance(value, str) or not re.fullmatch(r"(?:[A-Za-z]\.?){1,4}", value):
                raise ValueError("Initials must be 1–4 letters with optional dots.")
        elif key == "role":
            if not isinstance(value, str) or len(value) > 40 or any(c in value for c in "<>'\""):
                raise ValueError("Role must be at most 40 characters without < > ' or double quotes.")
        elif type(value) is not bool:
            raise ValueError(f"{key} must be a boolean")
    target = settings_path() if path is None else path
    with json_file_lock(target):
        data = read_settings(target)
        if {"role", "initials"} & patch.keys():
            prefs = _prefs_map(data)
            if data.get("initials"):
                old_key = clinician_string(data.get("role", ""), data["initials"])
                prefs[old_key] = {
                    **prefs.get(old_key, {}),
                    "ct_view": _view_for_clinician(data, old_key),
                }
            next_data = {**data, **patch}
            if next_data.get("initials"):
                key = clinician_string(next_data.get("role", ""), next_data["initials"])
                data["ct_view"] = prefs.get(key, {}).get("ct_view", DEFAULT_CT_VIEW)
            data["clinician_prefs"] = prefs
        data.update(patch)
        atomic_write_json(target, data)
    return {key: data.get(key, default) for key, default in PUBLIC_DEFAULTS.items()}
