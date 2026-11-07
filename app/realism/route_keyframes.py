"""On-demand zero-roll route keyframes, queued after fork frames."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

from atomic_json import atomic_write_json

from app.caseref import CaseRef
from app.cases import case_payload
from app.realism import worker as w

SPACING_MM = 2.0
_states: dict[str, dict[str, Any]] = {}
_lock = threading.RLock()
_start_lock = threading.Lock()


def plan_route(case_id: str, payload: dict, model_sha: str):
    fly = payload["fly"]
    stations = np.asarray(fly["s"], dtype=float)
    end = float(stations[-1])
    samples = [float(s) for s in np.arange(0, end, SPACING_MM)] + [end]
    origin = np.asarray(payload["origin_mm"])
    poses = []

    def interp(field, s):
        values = np.asarray(fly[field])
        return np.array([np.interp(s, stations, values[:, axis]) for axis in range(3)])

    for s in samples:
        forward = interp("t", s)
        forward /= np.linalg.norm(forward)
        up = interp("up", s)
        up -= forward * np.dot(up, forward)
        up /= np.linalg.norm(up)
        poses.append({
            "hold_s": s,
            "position_ras_mm": (interp("p", s) + origin).tolist(),
            "forward": forward.tolist(),
            "up": up.tolist(),
        })
    digest = {
        "protocol": "realism-route-v1",
        "model_sha": model_sha,
        "steps": w.STEPS,
        "route": payload.get("route", []),
        "spacing": SPACING_MM,
        "poses": [w._round_pose(p) for p in poses],
    }
    raw = json.dumps(digest, sort_keys=True, separators=(",", ":"))
    key = hashlib.sha256(raw.encode()).hexdigest()[:16]
    frames = [{
        "index": i,
        "s": s,
        "url": f"/api/cases/{case_id}/realism/route/{key}/kf-{i}.png",
        "ready": False,
    } for i, s in enumerate(samples)]
    return key, frames, poses


def _directory(case_id: str, key: str) -> Path:
    return w._cache_dir(case_id, key) / "route"


def route_status(case_id: str) -> dict:
    CaseRef.parse(case_id)
    with _lock:
        state = _states.get(case_id)
        if state is None:
            ok, reason, _ = w.verify_model()
            state = dict(
                status="idle" if ok else "unavailable", reason=reason, key=None, frames=[],
            )
        body = json.loads(json.dumps(state))
    body.update(
        done=sum(f["ready"] for f in body["frames"]), total=len(body["frames"]),
    )
    return body


def _generate(case_id, key, frames, poses, cancel, previous):
    directory = _directory(case_id, key)
    try:
        # The existing case fork worker owns the model until it finishes.
        if previous is not None:
            previous.join()
        if cancel.is_set():
            return
        if w.realism_status(case_id)["status"] != "done":
            raise RuntimeError("fork frame generation failed")
        if all(frame["ready"] for frame in frames):
            with _lock:
                _states[case_id]["status"] = "done"
            return
        image = nib.load(str(CaseRef.parse(case_id).airways))
        mask = (np.asanyarray(image.dataobj) > 0).astype(np.uint8)
        for frame, pose in zip(frames, poses, strict=True):
            if not w._wait_while_busy(cancel):
                return
            depth, valid = w.render_depth(
                mask, image.affine, np.asarray(pose["position_ras_mm"]),
                np.asarray(pose["forward"]), np.asarray(pose["up"]),
            )
            encoded = w.brea_geometry_image(depth, valid, size=256)
            rgb = w._run_euler(encoded, w.group_seed(case_id, "route"), cancel)
            if rgb is None or cancel.is_set():
                return
            w._atomic_write_png(directory / f"kf-{frame['index']}.png", rgb)
            with _lock:
                frame["ready"] = True
                atomic_write_json(directory / "manifest.json", [
                    {k: f[k] for k in ("index", "s", "url")}
                    for f in frames if f["ready"]
                ])
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
    key, frames, poses = plan_route(case_id, case_payload(case_id), sha)
    directory = _directory(case_id, key)
    with _lock:
        state = _states.get(case_id)
        if state and state["key"] == key and state["status"] == "running":
            return route_status(case_id)
    fork_state = w.start_realism(case_id)
    with _lock:
        manifest = directory / "manifest.json"
        if manifest.is_file():
            cached = json.loads(manifest.read_text())
            if len(cached) == len(frames) and all(
                (directory / f"kf-{i}.png").is_file() for i in range(len(frames))
            ):
                for f in frames:
                    f["ready"] = True
                if fork_state["status"] == "done":
                    _states[case_id] = dict(status="done", reason=None, key=key, frames=frames)
                    return route_status(case_id)
    with w._worker_lock:
        previous = w._active_thread
        cancel = threading.Event()
        with _lock:
            _states[case_id] = dict(status="running", reason=None, key=key, frames=frames)
        thread = threading.Thread(
            target=_generate,
            args=(case_id, key, frames, poses, cancel, previous),
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
    CaseRef.parse(case_id)
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        return None
    path = _directory(case_id, key) / f"kf-{index}.png"
    return path if path.is_file() else None
