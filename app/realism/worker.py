# Changes: add _run_euler_from warm-start Euler for chained route frames, plus
# image/tensor helpers (#67).
# Changes: snap orifices to depth openings after render_depth; PROTOCOL realism-v2 (#60c).
"""Background realism frames: group close forks, cache by route/pose/model, stream PNGs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
from PIL import Image
from termcolor import cprint

import paths
from atomic_json import atomic_write_json
from app.caseref import CaseRef
from app.cases import case_payload, load_case_record
from app.jobs import job_in_progress
from app.realism.brea_geometry import brea_geometry_image
from app.realism.depth import render_depth
from app.realism.poses import (
    ORIFICE_DIR_DISTANCE_MM,
    _point_along,
    fisheye_project,
    fork_poses,
    orifice_points,
    snap_orifices_to_openings,
)

PROTOCOL = "realism-v2"
STEPS = 12
GROUP_JOIN_MM = 3.0
MODEL_SHA256 = "419adbd774b0c0dad2eae9746c923b4f9265d5f1ab3a908fef9b3b2a784b58df"
PNG_NAME_RE = re.compile(r"^group-\d+$")

_hash_cache: dict[tuple[str, int, float], str] = {}
_ort_session = None
_ort_session_path: str | None = None
_ort_lock = threading.Lock()

_states_lock = threading.RLock()
_states: dict[str, dict[str, Any]] = {}

_worker_lock = threading.Lock()
_active_thread: threading.Thread | None = None
_active_cancel: threading.Event | None = None
_active_case: str | None = None
_active_key: str | None = None


def group_forks(forks: list[dict]) -> list[list[dict]]:
    """Walk route-order forks; join when fork.s - group_first.s < 3.0 mm."""
    groups: list[list[dict]] = []
    for fork in forks:
        if not groups:
            groups.append([fork])
            continue
        first = groups[-1][0]
        if float(fork["s"]) - float(first["s"]) < GROUP_JOIN_MM:
            groups[-1].append(fork)
        else:
            groups.append([fork])
    return groups


def group_seed(case_id: str, node: Any) -> int:
    digest = hashlib.sha256(f"{case_id}:{node}".encode()).hexdigest()
    return int(digest[:15], 16)


def resolve_model_path() -> Path | None:
    env = os.environ.get("BRONCHOROUTE_REALISM_MODEL", "").strip()
    if env:
        return Path(env).expanduser()
    candidate = Path(paths.PIPELINE) / "models" / "realism" / "broncho-flow-int8.onnx"
    return candidate


def stub_enabled() -> bool:
    if os.environ.get("BRONCHOROUTE_REALISM_STUB") != "1":
        return False
    return os.environ.get("BRONCHOROUTE_TESTING") == "1" or os.environ.get(
        "BRONCHOROUTE_SMOKE"
    ) == "1"


def _file_sha256(path: Path) -> str:
    key = (str(path), path.stat().st_size, path.stat().st_mtime)
    cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    _hash_cache[key] = hexdigest
    return hexdigest


def verify_model() -> tuple[bool, str | None, str | None]:
    """Return (ok, reason, model_sha). Stub uses synthetic sha 'stub'."""
    if stub_enabled():
        return True, None, "stub"
    path = resolve_model_path()
    if path is None or not path.is_file():
        return False, "model not found", None
    try:
        digest = _file_sha256(path)
    except OSError:
        return False, "model not found", None
    if digest != MODEL_SHA256:
        return False, "model hash mismatch", None
    return True, None, digest


def _round_pose(pose: dict) -> dict:
    return {
        "hold_s": round(float(pose["hold_s"]), 2),
        "position_ras_mm": [round(float(v), 2) for v in pose["position_ras_mm"]],
        "forward": [round(float(v), 3) for v in pose["forward"]],
        "up": [round(float(v), 3) for v in pose["up"]],
    }


def cache_key(
    *,
    model_sha: str,
    route: list,
    group_poses: list[dict],
) -> str:
    payload = {
        "protocol": PROTOCOL,
        "model_sha": model_sha,
        "steps": STEPS,
        "route": route,
        "poses": [_round_pose(pose) for pose in group_poses],
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _edges_full(case_id: str, payload: dict) -> list[dict]:
    from app.review import effective_for_case

    record = load_case_record(CaseRef.parse(case_id))
    graph = effective_for_case(case_id, record)["graph"]
    origin = np.asarray(payload["origin_mm"], dtype=float)

    def rel(point):
        return [float(point[i]) - float(origin[i]) for i in range(3)]

    return [
        {**edge, "centreline_mm": [rel(p) for p in edge["centreline_mm"]]}
        for edge in graph["edges"]
    ]


def _orifices_for_group(
    payload: dict,
    group: list[dict],
    pose: dict,
    edges_full: list[dict],
) -> list[dict]:
    position = np.asarray(pose["position_ras_mm"], dtype=float)
    forward = np.asarray(pose["forward"], dtype=float)
    up = np.asarray(pose["up"], dtype=float)
    origin = np.asarray(payload["origin_mm"], dtype=float)
    rows: list[dict] = []
    for fork in group:
        for child, sample in zip(
            fork["children"],
            orifice_points(payload, fork, edges_full),
            strict=True,
        ):
            u, v, inside = fisheye_project(
                sample["point_ras_mm"], position, forward, up
            )
            edge = edges_full[child["edge"]]
            centreline = edge.get("centreline_mm", edge.get("c"))
            point_dir = _point_along(centreline, ORIFICE_DIR_DISTANCE_MM)
            point_dir_ras = (np.asarray(point_dir, dtype=float) + origin).tolist()
            u_dir, v_dir, dir_inside = fisheye_project(
                point_dir_ras, position, forward, up
            )
            if not dir_inside or u_dir != u_dir or v_dir != v_dir:
                u_dir, v_dir = u, v
            rows.append({
                "fork": fork["fork"],
                "edge": sample["edge"],
                "on_route": bool(sample["on_route"]),
                "name": child.get("name"),
                "u": u,
                "v": v,
                "inside": inside,
                "u_dir": u_dir,
                "v_dir": v_dir,
            })
    return rows


def _frame_url(case_id: str, key: str, group_index: int) -> str:
    return f"/api/cases/{case_id}/realism/{key}/group-{group_index}.png"


def plan_frames(
    case_id: str,
    payload: dict,
    model_sha: str,
) -> tuple[str, list[dict], list[list[dict]], list[dict]]:
    """Return key, frames (ready false), groups, group poses."""
    forks = list(payload.get("forks") or [])
    groups = group_forks(forks)
    all_poses = fork_poses(payload)
    pose_by_fork = {pose["fork"]: pose for pose in all_poses}
    edges_full = _edges_full(case_id, payload)
    group_poses: list[dict] = []
    frames: list[dict] = []
    for index, group in enumerate(groups):
        pose = pose_by_fork[group[0]["fork"]]
        group_poses.append(pose)
        frames.append({
            "group": index,
            "forks": [fork["fork"] for fork in group],
            "hold_s": float(pose["hold_s"]),
            "ready": False,
            "url": None,
            "orifices": _orifices_for_group(payload, group, pose, edges_full),
        })
    key = cache_key(
        model_sha=model_sha,
        route=list(payload.get("route") or []),
        group_poses=group_poses,
    )
    return key, frames, groups, group_poses


def _cache_dir(case_id: str, key: str) -> Path:
    return CaseRef.parse(case_id).ts_out / "realism" / key


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / "manifest.json"


def _cache_complete(cache_dir: Path, frame_count: int) -> bool:
    manifest = _manifest_path(cache_dir)
    if not manifest.is_file():
        return False
    try:
        frames = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(frames, list) or len(frames) != frame_count:
        return False
    for index in range(frame_count):
        if not (cache_dir / f"group-{index}.png").is_file():
            return False
    return True


def _load_manifest_frames(case_id: str, key: str, cache_dir: Path) -> list[dict]:
    frames = json.loads(_manifest_path(cache_dir).read_text())
    for frame in frames:
        frame["ready"] = True
        frame["url"] = _frame_url(case_id, key, int(frame["group"]))
    return frames


def _snapshot_state(case_id: str) -> dict[str, Any]:
    with _states_lock:
        state = _states.get(case_id)
        if state is None:
            ok, reason, _sha = verify_model()
            if not ok:
                return {
                    "status": "unavailable",
                    "reason": reason,
                    "key": None,
                    "frames": [],
                }
            return {"status": "idle", "reason": None, "key": None, "frames": []}
        return {
            "status": state["status"],
            "reason": state.get("reason"),
            "key": state.get("key"),
            "frames": json.loads(json.dumps(state.get("frames") or [])),
        }


def _set_state(case_id: str, **fields: Any) -> None:
    with _states_lock:
        state = _states.setdefault(case_id, {
            "status": "idle",
            "reason": None,
            "key": None,
            "frames": [],
        })
        state.update(fields)


def _stub_rgb(encoded: np.ndarray) -> np.ndarray:
    condition = encoded.astype(np.float64)
    rgb = np.stack(
        [
            80.0 + 0.6 * condition,
            30.0 + 0.3 * condition,
            25.0 + 0.25 * condition,
        ],
        axis=-1,
    )
    return np.clip(np.rint(rgb), 0, 255).astype(np.uint8)


def _get_ort_session(model_path: Path):
    global _ort_session, _ort_session_path
    path_key = str(model_path.resolve())
    with _ort_lock:
        if _ort_session is not None and _ort_session_path == path_key:
            return _ort_session
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(
            path_key,
            options,
            providers=["CPUExecutionProvider"],
        )
        _ort_session = session
        _ort_session_path = path_key
        cprint(f"realism ORT session loaded: {model_path.name}", "cyan")
        return session


def _run_euler(
    encoded: np.ndarray,
    seed: int,
    cancel: threading.Event,
) -> np.ndarray | None:
    if stub_enabled():
        if cancel.is_set():
            return None
        return _stub_rgb(encoded)

    depth_tensor = encoded.astype(np.float32)[None, None] / np.float32(127.5) - np.float32(
        1
    )
    rng = np.random.default_rng(seed)
    image = rng.standard_normal((1, 3, 256, 256)).astype(np.float32)
    session = _get_ort_session(resolve_model_path())
    dt = np.float32(1.0 / STEPS)
    for step in range(STEPS):
        if cancel.is_set():
            return None
        velocity = session.run(
            None,
            {
                "x": image,
                "t": np.asarray([step * dt], dtype=np.float32),
                "depth": depth_tensor,
            },
        )[0]
        image = image + dt * velocity
    image = np.clip(image, -1.0, 1.0)
    return (
        ((image[0].transpose(1, 2, 0) + 1.0) * 127.5)
        .round()
        .clip(0, 255)
        .astype(np.uint8)
    )


def _rgb_to_image(rgb: np.ndarray) -> np.ndarray:
    """Convert an HWC uint8 image to a float32 NCHW tensor in [-1, 1]."""
    return (
        rgb.astype(np.float32).transpose(2, 0, 1)[None] / np.float32(127.5)
        - np.float32(1)
    )


def _image_to_rgb(image: np.ndarray) -> np.ndarray:
    """Convert a float32 NCHW tensor in [-1, 1] to an HWC uint8 image."""
    return (
        ((image[0].transpose(1, 2, 0) + 1.0) * 127.5)
        .round()
        .clip(0, 255)
        .astype(np.uint8)
    )


def _run_euler_from(
    image_prev: np.ndarray,
    encoded: np.ndarray,
    t0: float,
    steps: int,
    seed: int,
    cancel: threading.Event,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Warm-start Euler from image_prev. Returns (image tensor, rgb)."""
    if stub_enabled():
        if cancel.is_set():
            return None
        rgb = _stub_rgb(encoded)
        return _rgb_to_image(rgb), rgb

    depth_tensor = encoded.astype(np.float32)[None, None] / np.float32(127.5) - np.float32(
        1
    )
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((1, 3, 256, 256)).astype(np.float32)
    image = (
        np.float32(t0) * image_prev + np.float32(1.0 - t0) * noise
    ).astype(np.float32)
    session = _get_ort_session(resolve_model_path())
    dt = np.float32((1.0 - t0) / steps)
    for step in range(steps):
        if cancel.is_set():
            return None
        velocity = session.run(
            None,
            {
                "x": image,
                "t": np.asarray([np.float32(t0) + step * dt], dtype=np.float32),
                "depth": depth_tensor,
            },
        )[0]
        image = image + dt * velocity
    image = np.clip(image, -1.0, 1.0).astype(np.float32)
    return image, _image_to_rgb(image)


def _atomic_write_png(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    Image.fromarray(rgb).save(tmp, format="PNG")
    os.replace(tmp, path)


def _wait_while_busy(cancel: threading.Event) -> bool:
    """Sleep-poll while segmentation runs. False if cancelled."""
    while job_in_progress():
        if cancel.is_set():
            return False
        time.sleep(1.0)
    return not cancel.is_set()


def _delete_other_keys(case_id: str, keep_key: str) -> None:
    root = CaseRef.parse(case_id).ts_out / "realism"
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir() and child.name != keep_key and not (child / "route").is_dir():
            shutil.rmtree(child, ignore_errors=True)


def _remove_key_dir(case_id: str, key: str) -> None:
    cache_dir = _cache_dir(case_id, key)
    if cache_dir.is_dir():
        shutil.rmtree(cache_dir, ignore_errors=True)


def _worker_main(
    case_id: str,
    key: str,
    groups: list[list[dict]],
    group_poses: list[dict],
    frames: list[dict],
    payload: dict,
    cancel: threading.Event,
) -> None:
    cprint(f"realism worker start {case_id} key={key} groups={len(groups)}", "cyan")
    cache_dir = _cache_dir(case_id, key)
    try:
        if cancel.is_set():
            _remove_key_dir(case_id, key)
            return
        cache_dir.mkdir(parents=True, exist_ok=True)
        ref = CaseRef.parse(case_id)
        img = nib.load(str(ref.airways))
        mask = (np.asanyarray(img.dataobj) > 0).astype(np.uint8)
        affine = img.affine
        working = [dict(frame) for frame in frames]

        for index, (group, pose) in enumerate(zip(groups, group_poses, strict=True)):
            if not _wait_while_busy(cancel):
                _remove_key_dir(case_id, key)
                return
            if cancel.is_set():
                _remove_key_dir(case_id, key)
                return
            position = np.asarray(pose["position_ras_mm"], dtype=float)
            forward = np.asarray(pose["forward"], dtype=float)
            up = np.asarray(pose["up"], dtype=float)
            depth, valid = render_depth(mask, affine, position, forward, up)
            encoded = brea_geometry_image(depth, valid, size=256)
            snapped = snap_orifices_to_openings(
                depth, valid, working[index]["orifices"]
            )
            seed = group_seed(case_id, group[0]["node"])
            rgb = _run_euler(encoded, seed, cancel)
            if rgb is None or cancel.is_set():
                _remove_key_dir(case_id, key)
                return
            png_path = cache_dir / f"group-{index}.png"
            _atomic_write_png(png_path, rgb)
            working[index] = {
                **working[index],
                "orifices": snapped,
                "ready": True,
                "url": _frame_url(case_id, key, index),
            }
            atomic_write_json(_manifest_path(cache_dir), working)
            _set_state(case_id, frames=working, status="running", key=key, reason=None)
            cprint(f"realism frame ready group={index} forks={working[index]['forks']}", "green")

        if cancel.is_set():
            _remove_key_dir(case_id, key)
            return
        atomic_write_json(_manifest_path(cache_dir), working)
        _delete_other_keys(case_id, key)
        _set_state(case_id, frames=working, status="done", key=key, reason=None)
        cprint(f"realism done {case_id} key={key}", "green")
    except Exception as exc:  # noqa: BLE001
        cprint(f"realism failed {case_id}: {exc}", "red")
        _set_state(
            case_id,
            status="failed",
            reason=str(exc),
            key=key,
            frames=frames,
        )


def _launch_worker(
    case_id: str,
    key: str,
    groups: list[list[dict]],
    group_poses: list[dict],
    frames: list[dict],
    payload: dict,
) -> None:
    global _active_thread, _active_cancel, _active_case, _active_key
    with _worker_lock:
        if (
            _active_thread is not None
            and _active_thread.is_alive()
            and _active_case == case_id
            and _active_key == key
        ):
            return
        if _active_thread is not None and _active_thread.is_alive():
            if _active_cancel is not None:
                _active_cancel.set()
            _active_thread.join(timeout=120.0)
            if (
                _active_case == case_id
                and _active_key is not None
                and _active_key != key
            ):
                _remove_key_dir(case_id, _active_key)
        cancel = threading.Event()
        _set_state(
            case_id,
            status="running",
            reason=None,
            key=key,
            frames=frames,
        )
        thread = threading.Thread(
            target=_worker_main,
            args=(case_id, key, groups, group_poses, frames, payload, cancel),
            name=f"realism-{case_id}",
            daemon=True,
        )
        _active_thread = thread
        _active_cancel = cancel
        _active_case = case_id
        _active_key = key
        thread.start()


def realism_status(case_id: str) -> dict[str, Any]:
    CaseRef.parse(case_id)
    return _snapshot_state(case_id)


def start_realism(case_id: str) -> dict[str, Any]:
    """Plan poses, hit cache or start/cancel worker. Same body as GET."""
    CaseRef.parse(case_id)
    ok, reason, model_sha = verify_model()
    if not ok:
        body = {
            "status": "unavailable",
            "reason": reason,
            "key": None,
            "frames": [],
        }
        _set_state(case_id, **body)
        return body

    payload = case_payload(case_id)
    key, frames, groups, group_poses = plan_frames(case_id, payload, model_sha)
    cache_dir = _cache_dir(case_id, key)
    if _cache_complete(cache_dir, len(frames)):
        loaded = _load_manifest_frames(case_id, key, cache_dir)
        _set_state(case_id, status="done", reason=None, key=key, frames=loaded)
        cprint(f"realism cache hit {case_id} key={key}", "cyan")
        return _snapshot_state(case_id)

    with _states_lock:
        state = _states.get(case_id)
        if (
            state
            and state.get("status") == "running"
            and state.get("key") == key
            and _active_thread is not None
            and _active_thread.is_alive()
        ):
            return _snapshot_state(case_id)

    _launch_worker(case_id, key, groups, group_poses, frames, payload)
    return _snapshot_state(case_id)


def realism_png_path(case_id: str, key: str, name: str) -> Path | None:
    """Resolve a PNG under the case cache; None → 404."""
    CaseRef.parse(case_id)
    if not PNG_NAME_RE.fullmatch(name):
        return None
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        return None
    path = _cache_dir(case_id, key) / f"{name}.png"
    if not path.is_file():
        return None
    return path


def reset_realism_state_for_tests() -> None:
    """Drop in-memory worker state between tests."""
    global _active_thread, _active_cancel, _active_case, _active_key
    with _worker_lock:
        if _active_cancel is not None:
            _active_cancel.set()
        if _active_thread is not None and _active_thread.is_alive():
            _active_thread.join(timeout=30.0)
        _active_thread = None
        _active_cancel = None
        _active_case = None
        _active_key = None
    with _states_lock:
        _states.clear()


__all__ = [
    "GROUP_JOIN_MM",
    "MODEL_SHA256",
    "STEPS",
    "cache_key",
    "group_forks",
    "group_seed",
    "plan_frames",
    "realism_png_path",
    "realism_status",
    "reset_realism_state_for_tests",
    "resolve_model_path",
    "start_realism",
    "stub_enabled",
    "verify_model",
]
