# Changes: fork-window anchors around holds chained by warm-start Euler (t0=0.45,
# 6 steps); PROTOCOL realism-route-v3 (#67).
# Changes: route keyframes are fork-hold + ≤80 mm anchors with 16-bit depth PNGs;
# fork anchors reuse the generated group PNG instead of rerunning the model (#66).
"""On-demand zero-roll route keyframes, queued after fork frames."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from PIL import Image

from atomic_json import atomic_write_json

from app.caseref import CaseRef
from app.cases import case_payload
from app.realism import worker as w

MAX_GAP_MM = 80.0
FORK_WINDOW_MM = (-25.0, -16.0, -8.0, 5.0, 12.0, 20.0)
WARM_T0 = 0.45
WARM_STEPS = 6
PROTOCOL = "realism-route-v3"
_states: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()
_start_lock = threading.Lock()


def _pose_at(payload: dict, station: float) -> dict:
    """Interpolate the fly frame at an arbitrary route station."""
    fly = payload["fly"]
    stations = np.asarray(fly["s"], dtype=float)
    origin = np.asarray(payload["origin_mm"], dtype=float)

    def interp(field):
        values = np.asarray(fly[field], dtype=float)
        return np.array([
            np.interp(station, stations, values[:, axis]) for axis in range(3)
        ])

    forward = interp("t")
    forward /= np.linalg.norm(forward)
    up = interp("up")
    up -= forward * np.dot(up, forward)
    up /= np.linalg.norm(up)
    return {
        "hold_s": float(station),
        "position_ras_mm": (interp("p") + origin).tolist(),
        "forward": forward.tolist(),
        "up": up.tolist(),
    }


def _fill_gaps(anchors: list[dict], payload: dict) -> list[dict]:
    """Insert evenly spaced anchors so no consecutive gap exceeds MAX_GAP_MM."""
    filled: list[dict] = []
    for index, anchor in enumerate(anchors):
        filled.append(anchor)
        if index + 1 >= len(anchors):
            continue
        gap = anchors[index + 1]["s"] - anchor["s"]
        steps = max(1, int(math.ceil(gap / MAX_GAP_MM - 1e-9)))
        for step in range(1, steps):
            station = anchor["s"] + gap * step / steps
            filled.append({
                "s": station,
                "pose": _pose_at(payload, station),
                "group": None,
            })
    return filled


def plan_anchors(payload: dict) -> list[dict]:
    """Fork-group holds, fork-window anchors, then straight anchors, ordered."""
    groups = w.group_forks(list(payload.get("forks") or []))
    poses = w.fork_poses(payload)
    pose_by_fork = {pose["fork"]: pose for pose in poses}
    anchors = [
        {
            "s": float(pose_by_fork[group[0]["fork"]]["hold_s"]),
            "pose": pose_by_fork[group[0]["fork"]],
            "group": index,
        }
        for index, group in enumerate(groups)
    ]
    fly_stations = np.asarray(payload["fly"]["s"], dtype=float)
    end = float(fly_stations[-1])
    for group in groups:
        hold_s = float(pose_by_fork[group[0]["fork"]]["hold_s"])
        for delta in FORK_WINDOW_MM:
            station = min(max(hold_s + delta, 0.0), end)
            anchors.append({
                "s": station,
                "pose": _pose_at(payload, station),
                "group": None,
            })
    anchors.append({"s": end, "pose": _pose_at(payload, end), "group": None})
    anchors.append({"s": 0.0, "pose": _pose_at(payload, 0.0), "group": None})
    # Sort by station; on a tie a fork anchor wins so its PNG can be reused.
    anchors.sort(key=lambda anchor: (anchor["s"], anchor["group"] is None))
    unique: list[dict] = []
    for anchor in anchors:
        if unique and abs(anchor["s"] - unique[-1]["s"]) < 1e-9:
            if unique[-1]["group"] is None and anchor["group"] is not None:
                unique[-1] = anchor
            continue
        unique.append(anchor)
    return _fill_gaps(unique, payload)


def plan_route(case_id: str, payload: dict, model_sha: str):
    anchors = plan_anchors(payload)
    digest = {
        "protocol": PROTOCOL,
        "model_sha": model_sha,
        "steps": w.STEPS,
        "route": payload.get("route", []),
        "max_gap_mm": MAX_GAP_MM,
        "anchors": [
            {"s": round(anchor["s"], 4), "group": anchor["group"],
             "pose": w._round_pose(anchor["pose"])}
            for anchor in anchors
        ],
    }
    raw = json.dumps(digest, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(raw.encode()).hexdigest()[:16]
    frames = [{
        "index": index,
        "s": anchor["s"],
        "group": anchor["group"],
        "url": f"/api/cases/{case_id}/realism/route/{key}/kf-{index}.png",
        "depth_url": (
            f"/api/cases/{case_id}/realism/route/{key}/kf-{index}.depth.png"
        ),
        "ready": False,
    } for index, anchor in enumerate(anchors)]
    return key, frames, [anchor["pose"] for anchor in anchors], anchors


def _directory(case_id: str, key: str) -> Path:
    return w._cache_dir(case_id, key) / "route"


def _manifest_body(frames: list[dict]) -> dict:
    return {
        "stations": [frame["s"] for frame in frames],
        "frames": [
            {field: frame[field] for field in ("index", "s", "url", "depth_url", "group")}
            for frame in frames if frame["ready"]
        ],
    }


def _cached_frames(
    directory: Path, frames: list[dict],
) -> list[dict] | None:
    """Return frames marked ready when the cached route matches the plan."""
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        body = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    # An old-style manifest was a bare list; it cannot name depth files.
    if not isinstance(body, dict):
        return None
    stations = body.get("stations")
    entries = body.get("frames")
    if not isinstance(stations, list) or not isinstance(entries, list):
        return None
    if len(stations) != len(frames) or len(entries) != len(frames):
        return None
    if any(abs(float(station) - frame["s"]) > 1e-6
           for station, frame in zip(stations, frames)):
        return None
    for index, frame in enumerate(frames):
        entry = entries[index]
        if not isinstance(entry, dict) or int(entry.get("index", -1)) != index:
            return None
        if not (directory / f"kf-{index}.png").is_file():
            return None
        if not (directory / f"kf-{index}.depth.png").is_file():
            return None
    for frame in frames:
        frame["ready"] = True
    return frames


def route_status(case_id: str) -> dict:
    CaseRef.parse(case_id)
    with _lock:
        state = _states.get(case_id)
        if state is None:
            ok, reason, _ = w.verify_model()
            state = dict(
                status="idle" if ok else "unavailable", reason=reason, key=None,
                frames=[],
            )
        body = json.loads(json.dumps(state))
    body.update(
        done=sum(f["ready"] for f in body["frames"]), total=len(body["frames"]),
    )
    return body


def _atomic_write_depth_png(path: Path, depth: np.ndarray, valid: np.ndarray) -> None:
    """Write uint16 = round(depth_mm × 10); no hit = 65535."""
    values = np.where(
        valid,
        np.clip(np.rint(np.asarray(depth, dtype=float) * 10.0), 0, 65534),
        65535,
    ).astype(np.uint16)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    Image.fromarray(values).save(tmp, format="PNG")
    os.replace(tmp, path)


def _atomic_copy(source: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    tmp.write_bytes(source.read_bytes())
    os.replace(tmp, path)


def _generate(case_id, key, frames, poses, anchors, cancel, previous):
    directory = _directory(case_id, key)
    try:
        # The existing case fork worker owns the model until it finishes.
        if previous is not None:
            previous.join()
        if cancel.is_set():
            return
        fork_state = w.realism_status(case_id)
        if fork_state["status"] != "done":
            raise RuntimeError("fork frame generation failed")
        if all(frame["ready"] for frame in frames):
            with _lock:
                _states[case_id]["status"] = "done"
            return
        image = nib.load(str(CaseRef.parse(case_id).airways))
        mask = (np.asanyarray(image.dataobj) > 0).astype(np.uint8)
        fork_dir = w._cache_dir(case_id, fork_state["key"])
        route_seed = w.group_seed(case_id, "route")
        previous_image: np.ndarray | None = None
        for frame, pose, anchor in zip(frames, poses, anchors, strict=True):
            if not w._wait_while_busy(cancel):
                return
            depth, valid = w.render_depth(
                mask, image.affine, np.asarray(pose["position_ras_mm"]),
                np.asarray(pose["forward"]), np.asarray(pose["up"]),
            )
            _atomic_write_depth_png(
                directory / f"kf-{frame['index']}.depth.png", depth, valid,
            )
            if anchor["group"] is not None:
                source = fork_dir / f"group-{anchor['group']}.png"
                if not source.is_file():
                    raise RuntimeError("fork frame missing")
                destination = directory / f"kf-{frame['index']}.png"
                _atomic_copy(source, destination)
                previous_image = w._rgb_to_image(
                    np.asarray(Image.open(destination).convert("RGB")),
                )
            else:
                encoded = w.brea_geometry_image(depth, valid, size=256)
                if previous_image is None:
                    rgb = w._run_euler(encoded, route_seed, cancel)
                    if rgb is None or cancel.is_set():
                        return
                    previous_image = w._rgb_to_image(rgb)
                else:
                    result = w._run_euler_from(
                        previous_image, encoded, WARM_T0, WARM_STEPS,
                        route_seed, cancel,
                    )
                    if result is None or cancel.is_set():
                        return
                    previous_image, rgb = result
                w._atomic_write_png(directory / f"kf-{frame['index']}.png", rgb)
            with _lock:
                frame["ready"] = True
                atomic_write_json(directory / "manifest.json", _manifest_body(frames))
        with _lock:
            _states[case_id]["status"] = "done"
    except Exception as exc:
        with _lock:
            _states[case_id].update(status="failed", reason=str(exc))
    finally:
        if cancel.is_set():
            with _lock:
                _states[case_id].update(status="idle", reason="cancelled")


def start_route(case_id):
    with _start_lock:
        return _start_route(case_id)


def _start_route(case_id):
    ok, reason, sha = w.verify_model()
    if not ok:
        return route_status(case_id)
    key, frames, poses, anchors = plan_route(
        case_id, case_payload(case_id), sha,
    )
    directory = _directory(case_id, key)
    with _lock:
        state = _states.get(case_id)
        if state and state["key"] == key and state["status"] == "running":
            return route_status(case_id)
    fork_state = w.start_realism(case_id)
    with _lock:
        cached = _cached_frames(directory, frames)
        if cached is not None and fork_state["status"] == "done":
            _states[case_id] = dict(status="done", reason=None, key=key, frames=cached)
            return route_status(case_id)
    with w._worker_lock:
        previous = w._active_thread
        cancel = threading.Event()
        with _lock:
            _states[case_id] = dict(status="running", reason=None, key=key, frames=frames)
        thread = threading.Thread(
            target=_generate,
            args=(case_id, key, frames, poses, anchors, cancel, previous),
            name=f"realism-route-{case_id}",
            daemon=True,
        )
        w._active_thread, w._active_cancel = thread, cancel
        w._active_case, w._active_key = case_id, key
        thread.start()
    return route_status(case_id)


def cancel_route(case_id):
    with w._worker_lock:
        if w._active_case == case_id and w._active_cancel is not None:
            with _lock:
                state = _states.get(case_id)
                if state and state["status"] == "running":
                    w._active_cancel.set()
                    state.update(status="idle", reason="cancelled")
    return route_status(case_id)


def route_png_path(case_id, key, index):
    return _png_path(case_id, key, f"kf-{index}.png")


def route_depth_png_path(case_id, key, index):
    return _png_path(case_id, key, f"kf-{index}.depth.png")


def _png_path(case_id, key, name):
    CaseRef.parse(case_id)
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        return None
    path = _directory(case_id, key) / name
    return path if path.is_file() else None
