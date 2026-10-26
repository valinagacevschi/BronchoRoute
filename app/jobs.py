"""Background pipeline jobs (Phase 4a — segmentation).

One job per case at a time. Launcher uses a new session, clean env, and arg
lists only. Job state persists to ts_out/<id>/job.json so restart detects a
live pid (409) or marks a dead one failed. Default 45 min timeout kills the
process group; shutdown terminates remaining groups.

Changes: pin job.json path on JobState at first persist; reset_jobs_for_tests
joins worker threads so late writes cannot hit real paths.TS_OUT after
monkeypatch teardown.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from termcolor import cprint

import paths
import repo_root
from atomic_json import atomic_write_json, json_file_lock
from app.caseref import CaseRef
from app.cases import load_case_record
from app.versions import register_artifact
from app.workflow import (
    SEGMENT_ARTIFACT_NAMES,
    WorkflowStep,
    require_prior_steps,
    segment_artifact_paths,
)

# Redact paths under data/: keep flag names; keep only basename of the path.
# Handles quotes, parentheses, --flag= prefixes, absolute and relative forms.
_DATA_PATH_RE = re.compile(
    r"(?P<prefix>(?:^|[\s\"'=(])(?:--[\w-]+[= ])?)"
    r"(?P<path>(?:/|\.\.?/)?(?:[^\s\"')]*?/)?data/[^\s\"')]+)"
)

DEFAULT_JOB_TIMEOUT_S = 45 * 60

LaunchFn = Callable[[list[str], Path], subprocess.Popen]


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _default_launcher(cmd: list[str], cwd: Path) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
        env=_clean_env(),
    )


_launcher: LaunchFn = _default_launcher
_job_timeout_s: float = DEFAULT_JOB_TIMEOUT_S


def set_job_launcher(fn: LaunchFn | None) -> None:
    """Tests monkeypatch the subprocess launcher via this hook."""
    global _launcher
    _launcher = fn if fn is not None else _default_launcher


def set_job_timeout_s(seconds: float | None) -> None:
    """Tests shorten the job timeout; None restores the default."""
    global _job_timeout_s
    _job_timeout_s = DEFAULT_JOB_TIMEOUT_S if seconds is None else float(seconds)


def redact_log_line(line: str) -> str:
    """Replace any path under data/ with its basename; keep flag/quote prefix."""

    def _repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        path = match.group("path") or ""
        return prefix + Path(path).name

    return _DATA_PATH_RE.sub(_repl, line)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class JobState:
    case_id: str
    kind: str
    status: str = "queued"  # queued|running|succeeded|failed
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    pid: int | None = None
    log_lines: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pgid: int | None = field(default=None, repr=False)
    # Pinned at first persist so background threads keep writing the tmp-tree
    # path even after tests restore real paths.TS_OUT.
    _json_path: Path | None = field(default=None, repr=False)

    def append_log(self, line: str) -> None:
        cleaned = redact_log_line(line.rstrip("\n"))
        with self._lock:
            self.log_lines.append(cleaned)
            if len(self.log_lines) > 500:
                self.log_lines = self.log_lines[-500:]

    def set_error(self, message: str) -> None:
        self.error = redact_log_line(str(message))

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            elapsed = 0.0
            if self.started_at is not None:
                end = self.finished_at if self.finished_at is not None else now
                elapsed = max(0.0, end - self.started_at)
            return {
                "case_id": self.case_id,
                "kind": self.kind,
                "status": self.status,
                "elapsed_s": round(elapsed, 2),
                "error": self.error,
                "pid": self.pid,
                "log_tail": self.log_lines[-50:],
            }


_jobs_lock = threading.Lock()
_latest: dict[str, JobState] = {}
_running: dict[str, JobState] = {}
_restore_lock = threading.Lock()
_restored: set[str] = set()
_worker_threads: list[threading.Thread] = []
_worker_threads_lock = threading.Lock()


class JobConflict(Exception):
    """A job is already running for this case → HTTP 409."""

    def __init__(self, case_id: str):
        super().__init__(f"job already running for {case_id}")
        self.case_id = case_id


def _job_json_path(case_id: str) -> Path:
    return CaseRef.parse(case_id).ts_out / "job.json"


def _persist_job(job: JobState) -> None:
    if job._json_path is None:
        job._json_path = _job_json_path(job.case_id)
    path = job._json_path
    path.parent.mkdir(parents=True, exist_ok=True)
    wall_start = None
    if path.is_file():
        try:
            prev = json.loads(path.read_text())
            wall_start = prev.get("wall_start")
        except (OSError, json.JSONDecodeError):
            wall_start = None
    if job.status == "running" and not wall_start:
        wall_start = _utc_now()
    if job.status not in {"queued", "running"}:
        wall_start = wall_start  # keep for audit; status is terminal
    payload = {
        "case_id": job.case_id,
        "kind": job.kind,
        "status": job.status,
        "pid": job.pid,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "wall_start": wall_start,
    }
    atomic_write_json(path, payload)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _kill_process_group(pgid: int | None, pid: int | None) -> None:
    target = pgid if pgid is not None else pid
    if target is None or target <= 0:
        return
    try:
        os.killpg(target, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    time.sleep(0.05)
    try:
        os.killpg(target, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def restore_job_from_disk(case_id: str) -> JobState | None:
    """Load ts_out job.json; live pid → running conflict; dead → failed."""
    with _restore_lock:
        if case_id in _restored:
            with _jobs_lock:
                return _latest.get(case_id)
        _restored.add(case_id)

    path = CaseRef.parse(case_id).ts_out / "job.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    status = data.get("status")
    pid = data.get("pid")
    job = JobState(
        case_id=case_id,
        kind=str(data.get("kind") or "segment"),
        status=str(status or "failed"),
        started_at=data.get("started_at"),
        finished_at=data.get("finished_at"),
        error=data.get("error"),
        pid=int(pid) if isinstance(pid, int) else None,
    )
    if status == "running" and isinstance(pid, int) and _pid_alive(pid):
        with _jobs_lock:
            _running[case_id] = job
            _latest[case_id] = job
        return job
    if status in {"running", "queued"}:
        job.status = "failed"
        job.set_error("server restarted")
        job.finished_at = time.monotonic()
        _persist_job(job)
    with _jobs_lock:
        _latest[case_id] = job
    return job


def latest_job(case_id: str) -> dict | None:
    restore_job_from_disk(case_id)
    with _jobs_lock:
        job = _latest.get(case_id)
        return job.snapshot() if job else None


def _pipeline_cwd() -> Path:
    return Path(repo_root.ensure()) / "pipeline"


def _segment_commands(ref: CaseRef) -> list[list[str]]:
    ts_out = ref.ts_out
    out_dir = paths.OUT / ref.case_id
    case_dir = (
        paths.LOCAL_CASES / ref.case_id
        if ref.kind == "local"
        else paths.AEROPATH / ref.case_id
    )
    return [
        [
            "uv", "run", "python", "run_totalsegmentator.py",
            str(ref.ct), "-o", str(ts_out), "--device", "mps",
        ],
        [
            "uv", "run", "python", "build_graph.py",
            str(case_dir),
            "--airways", str(ref.airways),
            "--lobes", str(ref.lobes),
            "--bridges", str(ref.bridges),
            "-o", str(out_dir),
        ],
    ]


def register_segment_artifacts(
    case_id: str,
    clinician: str,
    *,
    registered_existing: bool = False,
) -> dict:
    """Register CT + five segment artifacts and append segmentation_done in one lock."""
    ref = CaseRef.parse(case_id)
    paths_map = segment_artifact_paths(ref)
    missing = [n for n, p in paths_map.items() if p is None or not Path(p).is_file()]
    if missing:
        raise FileNotFoundError(f"segmentation artifacts missing: {missing}")
    if not ref.ct.is_file():
        raise FileNotFoundError(f"CT not found for {case_id}")

    case_path = ref.case_json
    data = {
        "artifacts": list(SEGMENT_ARTIFACT_NAMES),
        "registered_existing": bool(registered_existing),
    }
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with json_file_lock(case_path):
        case = load_case_record(ref)
        register_artifact(case, "ct", ref.ct)
        for name in SEGMENT_ARTIFACT_NAMES:
            register_artifact(case, name, paths_map[name])
        entry = {
            "step": int(WorkflowStep.SEGMENT),
            "kind": "segmentation_done",
            "data": data,
            "clinician": clinician,
            "at": at,
        }
        case.setdefault("records", []).append(entry)
        atomic_write_json(case_path, case)
        return entry


def _run_job(job: JobState, cmds: list[list[str]], clinician: str) -> None:
    cwd = _pipeline_cwd()
    job.status = "running"
    job.started_at = time.monotonic()
    _persist_job(job)
    cprint(f"job {job.kind} start {job.case_id}", "cyan")
    deadline = time.monotonic() + _job_timeout_s
    try:
        for cmd in cmds:
            job.append_log("$ " + redact_log_line(" ".join(cmd)))
            proc = _launcher(cmd, cwd)
            job.pid = proc.pid
            try:
                job._pgid = os.getpgid(proc.pid)
            except (ProcessLookupError, OSError):
                job._pgid = proc.pid
            _persist_job(job)
            assert proc.stdout is not None

            def _pump_stdout() -> None:
                try:
                    for line in proc.stdout:
                        job.append_log(line)
                except Exception:  # noqa: BLE001
                    pass

            reader = threading.Thread(
                target=_pump_stdout, name=f"job-log-{job.case_id}", daemon=True
            )
            reader.start()
            while True:
                try:
                    code = proc.wait(timeout=0.5)
                    break
                except TypeError:
                    # Fake launchers in tests may not accept timeout=.
                    code = proc.wait()
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        _kill_process_group(job._pgid, proc.pid)
                        raise TimeoutError(
                            f"job timed out after {_job_timeout_s:.0f}s"
                        )
            reader.join(timeout=2.0)
            if time.monotonic() > deadline and code is None:
                _kill_process_group(job._pgid, proc.pid)
                raise TimeoutError(f"job timed out after {_job_timeout_s:.0f}s")
            if code != 0:
                raise RuntimeError(f"command exited {code}: {redact_log_line(' '.join(cmd))}")
        register_segment_artifacts(job.case_id, clinician)
        job.status = "succeeded"
        job.pid = None
        job.append_log("segmentation_done")
        cprint(f"job {job.kind} ok {job.case_id}", "green")
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.set_error(str(exc))
        job.append_log(f"ERROR: {redact_log_line(str(exc))}")
        cprint(f"job {job.kind} failed {job.case_id}: {job.error}", "red")
        if job._pgid is not None or job.pid is not None:
            _kill_process_group(job._pgid, job.pid)
    finally:
        job.finished_at = time.monotonic()
        job.pid = None
        _persist_job(job)
        with _jobs_lock:
            _running.pop(job.case_id, None)


def start_segment_job(case_id: str, clinician: str) -> dict:
    """Start TotalSegmentator + build_graph; 409 if already running."""
    require_prior_steps(case_id, int(WorkflowStep.SEGMENT))
    ref = CaseRef.parse(case_id)
    if not ref.ct.is_file():
        raise FileNotFoundError(f"CT not found for {case_id}")

    restore_job_from_disk(case_id)

    job = JobState(case_id=case_id, kind="segment")
    with _jobs_lock:
        existing = _running.get(case_id)
        if existing is not None and existing.status in {"queued", "running"}:
            raise JobConflict(case_id)
        # Disk restore may have left a running entry for a live foreign pid.
        if case_id in _running:
            raise JobConflict(case_id)
        _running[case_id] = job
        _latest[case_id] = job

    # Pin path while caller still has paths.* patched (tests / request).
    job._json_path = _job_json_path(case_id)
    cmds = _segment_commands(ref)
    thread = threading.Thread(
        target=_run_job,
        args=(job, cmds, clinician),
        name=f"segment-{case_id}",
        daemon=True,
    )
    with _worker_threads_lock:
        _worker_threads.append(thread)
    thread.start()
    return job.snapshot()


def terminate_all_jobs() -> None:
    """Kill process groups for in-memory running jobs (server shutdown)."""
    with _jobs_lock:
        jobs = list(_running.values())
    for job in jobs:
        cprint(f"shutdown: terminate job {job.case_id} pid={job.pid}", "yellow")
        _kill_process_group(job._pgid, job.pid)
        job.status = "failed"
        job.set_error("server shutdown")
        job.finished_at = time.monotonic()
        try:
            _persist_job(job)
        except Exception:  # noqa: BLE001
            pass
    with _jobs_lock:
        _running.clear()


def reset_jobs_for_tests() -> None:
    """Clear in-memory job tables and join workers (unit tests only)."""
    terminate_all_jobs()
    with _worker_threads_lock:
        threads = list(_worker_threads)
        _worker_threads.clear()
    for thread in threads:
        thread.join(timeout=5.0)
    with _jobs_lock:
        _latest.clear()
        _running.clear()
    with _restore_lock:
        _restored.clear()
    set_job_timeout_s(None)
    set_job_launcher(None)


__all__ = [
    "DEFAULT_JOB_TIMEOUT_S",
    "JobConflict",
    "JobState",
    "latest_job",
    "redact_log_line",
    "register_segment_artifacts",
    "reset_jobs_for_tests",
    "restore_job_from_disk",
    "set_job_launcher",
    "set_job_timeout_s",
    "start_segment_job",
    "terminate_all_jobs",
]
