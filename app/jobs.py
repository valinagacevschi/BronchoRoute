# Changes: no-lungs and empty-graph reasons keep their own step names.
# Changes: one plain failure sentence naming the step that stopped.
# Changes: stamp pipeline_version when processing finishes; reprocess skips the import gate.
# Changes: import lifecycle cleanup, cancellable jobs, step progress and guarded smoke launcher.
# Changes: surface empty airway failures as a plain graph-step error.
# Changes: viewer-only startup skips recovery, tmp, and file logs; redact Archive paths only.
# Changes: persistent process registry, global job guard and redacted rotating logs.
"""Background pipeline jobs (Phase 4a — segmentation).

One job across all Cases at a time. Launcher uses a new session, clean env, and arg
lists only. Job state persists to ts_out/<id>/job.json so restart detects a
live pid (409) or marks a dead one failed. Default 45 min timeout kills the
process group; shutdown terminates remaining groups.

Changes: pin job.json path on JobState at first persist; reset_jobs_for_tests
joins worker threads so late writes cannot hit real paths.TS_OUT after
monkeypatch teardown.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import shlex
import shutil
import sys
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

import paths
import repo_root
from atomic_json import atomic_write_json, json_file_lock
from app.caseref import CASE_ID_RE, CaseRef
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
# Home and system prefixes only, so API routes and script names stay in the log.
_FS_PATH_RE = re.compile(
    r"(?<!\w)/(?:Users|Volumes|private|var|tmp|home)/[^\s\"']+"
)

DEFAULT_JOB_TIMEOUT_S = 45 * 60

LaunchFn = Callable[[list[str], Path], subprocess.Popen]


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("BRONCHOROUTE_TOKEN", None)
    # Unit-test monkeypatches do not cross a subprocess boundary.
    env["LUNGNAV_DATA_ROOT"] = str(paths.PIPELINE)
    env["LUNGNAV_TMP_DIR"] = str(paths.TMP_DIR)
    env["LUNGNAV_LOG_DIR"] = str(paths.LOG_DIR)
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


def _redact_data_root(line: str) -> str:
    """Hide paths.PIPELINE when the data root is outside the home prefixes."""
    root = str(paths.PIPELINE).rstrip("/")
    if not root.startswith("/") or root == "/":
        return line
    pattern = r"(?<!\w)" + re.escape(root) + r"(?:/[^\s\"']*)?(?![\w.-])"
    return re.sub(pattern, "[path]", line)


_STEP_NAMES = ("graph", "airway model", "segmentation", "reading")


def _step_in_log(lines: list[str]) -> str:
    return next((step for step in _STEP_NAMES if f"step: {step}" in lines), "reading")


def _last_logged_step(lines: list[str]) -> str:
    """Latest step line. Timeout and unexpected exits follow this, not a known reason."""
    step = "reading"
    for line in lines:
        for name in ("reading", "segmentation", "airway model", "graph"):
            if line == f"step: {name}":
                step = name
                break
    return step


def _failure_message(job: "JobState", *, timed_out: bool = False) -> str:
    """One sentence for every failed import or reprocess. Cancellation is separate."""
    with job._lock:
        lines = list(job.log_lines)
    if timed_out:
        step, reason = _last_logged_step(lines), "timed out"
    elif any("segmentation: no lungs found" in line for line in lines):
        step, reason = "segmentation", "no lungs found"
    elif any("graph: no airway tree in lung" in line for line in lines):
        step, reason = "graph", "no airway tree in lung"
    else:
        step, reason = _last_logged_step(lines), "the step stopped unexpectedly"
    return f"{step} step failed: {reason}. Try another Series"


def redact_log_line(line: str) -> str:
    """Drop identifier lines; keep routes while hiding Archive paths."""
    if re.search(
        r"patient[ _]?(?:name|id|birth)|birth[ _]?date|\bdob\b|archive|\.zip\b",
        line, re.IGNORECASE,
    ):
        return "[redacted]"

    def _repl(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        path = match.group("path") or ""
        return prefix + Path(path).name

    line = _DATA_PATH_RE.sub(_repl, line)
    line = re.sub(r"\b[0-9]+(?:\.[0-9]+){3,}\b", "[redacted]", line)
    line = _FS_PATH_RE.sub("[path]", line)
    return _redact_data_root(line)


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
    import_id: str | None = None
    _cleanup_dirs: tuple[Path, ...] = field(default_factory=tuple, repr=False)
    _case_path: Path | None = field(default=None, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pgid: int | None = field(default=None, repr=False)
    # Pinned at first persist so background threads keep writing the tmp-tree
    # path even after tests restore real paths.TS_OUT.
    _json_path: Path | None = field(default=None, repr=False)
    _pid_path: Path | None = field(default=None, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, repr=False)

    def append_log(self, line: str) -> None:
        cleaned = redact_log_line(line.rstrip("\n"))
        logging.getLogger("bronchoroute.jobs").info(cleaned)
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
                "status": "running" if self.import_id and not self._done.is_set() else self.status,
                "elapsed_s": round(elapsed, 2),
                "estimate_s": 390,
                "step": _step_in_log(self.log_lines),
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
    """Keep concurrent pipelines from exhausting local memory."""

    def __init__(self, case_id: str):
        super().__init__("another pipeline job is already running; wait for it to finish")
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
    if os.environ.get("BRONCHOROUTE_FAKE_SEGMENT") == "1" and (
        os.environ.get("BRONCHOROUTE_TESTING") == "1"
        or os.environ.get("BRONCHOROUTE_SMOKE") == "1"
    ):
        return [[sys.executable, "fake_segment.py", ref.case_id]]
    return [
        [
            sys.executable, "run_totalsegmentator.py",
            str(ref.ct), "-o", str(ts_out), "--device", "mps",
        ],
        [
            sys.executable, "build_graph.py",
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
        # Same write as the segmentation record, so a finished Case is never unstamped.
        case["pipeline_version"] = paths.PIPELINE_VERSION
        case["processed_at"] = at
        atomic_write_json(case_path, case)
        return entry


def _run_job(job: JobState, cmds: list[list[str]], clinician: str) -> None:
    cwd = _pipeline_cwd()
    job.status = "running"
    job.started_at = time.monotonic()
    _persist_job(job)
    logging.getLogger(__name__).info("pipeline job started")
    deadline = time.monotonic() + _job_timeout_s
    try:
        job.append_log("step: reading")
        for cmd in cmds:
            job.append_log("step: graph" if "build_graph.py" in cmd else "step: segmentation")
            with job._lock:
                if job._cancelled.is_set():
                    raise RuntimeError("cancelled")
                proc = _launcher(cmd, cwd)
                job.pid = proc.pid
                try:
                    job._pgid = os.getpgid(proc.pid)
                except (ProcessLookupError, OSError):
                    job._pgid = proc.pid
                _record_pid(job)
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
            _clear_pid(job)
            if time.monotonic() > deadline and code is None:
                _kill_process_group(job._pgid, proc.pid)
                raise TimeoutError(f"job timed out after {_job_timeout_s:.0f}s")
            if job._cancelled.is_set():
                raise RuntimeError("cancelled")
            if code != 0:
                raise RuntimeError("pipeline step failed")
        if job._cancelled.is_set():
            raise RuntimeError("cancelled")
        register_segment_artifacts(job.case_id, clinician)
        job.status = "succeeded"
        job.pid = None
        job.append_log("segmentation_done")
        logging.getLogger(__name__).info("pipeline job succeeded")
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        if job._cancelled.is_set():
            job.set_error("cancelled")
        elif isinstance(exc, TimeoutError):
            job.set_error(_failure_message(job, timed_out=True))
        else:
            job.set_error(_failure_message(job))
        job.append_log("ERROR: " + redact_log_line(job.error or ""))
        logging.getLogger(__name__).error("pipeline job failed: %s", job.error)
        if job._pgid is not None or job.pid is not None:
            _kill_process_group(job._pgid, job.pid)
    finally:
        if job.import_id:
            from app.imports import release_import
            release_import(job.import_id)
            if job.status == "succeeded" and job._case_path:
                with json_file_lock(job._case_path):
                    document = json.loads(job._case_path.read_text())
                    document.pop("import_processing", None)
                    atomic_write_json(job._case_path, document)
            else:
                for directory in job._cleanup_dirs:
                    shutil.rmtree(directory, ignore_errors=True)
        _clear_pid(job)
        job.finished_at = time.monotonic()
        job.pid = None
        _persist_job(job)
        with _jobs_lock:
            _running.pop(job.case_id, None)
        job._done.set()


def job_in_progress() -> bool:
    """True while the single pipeline job has not finished."""
    with _jobs_lock:
        return bool(_running)


def start_segment_job(
    case_id: str,
    clinician: str,
    *,
    import_id: str | None = None,
    reprocess: bool = False,
) -> dict:
    """Start TotalSegmentator + build_graph; 409 if another job is running."""
    # Reprocess reads the stored CT. It does not repeat Series confirmation.
    if import_id is None and not reprocess:
        require_prior_steps(case_id, int(WorkflowStep.SEGMENT))
    ref = CaseRef.parse(case_id)
    if not ref.ct.is_file():
        raise FileNotFoundError(f"CT not found for {case_id}")

    restore_job_from_disk(case_id)

    job = JobState(case_id=case_id, kind="segment", import_id=import_id)
    job._case_path = ref.case_json
    job._cleanup_dirs = (paths.LOCAL_CASES / case_id, paths.TS_OUT / case_id, paths.OUT / case_id)
    with _jobs_lock:
        if _running:
            raise JobConflict(case_id)
        _running[case_id] = job
        _latest[case_id] = job

    # Pin path while caller still has paths.* patched (tests / request).
    job._json_path = _job_json_path(case_id)
    job._pid_path = paths.PIPELINE / "app_state" / "jobs.pids"
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


def cancel_latest_job(case_id: str) -> dict:
    restore_job_from_disk(case_id)
    with _jobs_lock:
        job = _latest.get(case_id)
    if job is None:
        raise ValueError("no processing job")
    with job._lock:
        if job.status not in {"queued", "running"}:
            return {"status": job.status}
        job._cancelled.set()
        _kill_process_group(job._pgid, job.pid)
    if not job._done.wait(timeout=10):
        raise ValueError("cancellation still in progress")
    return job.snapshot()


def terminate_all_jobs() -> None:
    """Kill process groups for in-memory running jobs (server shutdown)."""
    with _jobs_lock:
        jobs = list(_running.values())
    for job in jobs:
        job._cancelled.set()
        with job._lock:
            _kill_process_group(job._pgid, job.pid)
            _clear_pid(job)
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
    "job_in_progress",
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


_registry_lock = threading.Lock()


def _record_pid(job: JobState) -> None:
    path = job._pid_path
    if path is None:
        return
    with _registry_lock:
        atomic_write_json(path, [{"pid": job.pid, "pgid": job._pgid, "case_id": job.case_id}])


def _clear_pid(job: JobState) -> None:
    if job._pid_path is not None:
        with _registry_lock:
            job._pid_path.unlink(missing_ok=True)


class _RedactedFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_line(super().format(record))


def initialize_sidecar() -> RotatingFileHandler | None:
    """Recover owned processes and open the rotating log.

    A viewer-only deploy mounts the data root read-only, so skip every write.
    """
    if paths.viewer_only():
        return None
    path = paths.PIPELINE / "app_state" / "jobs.pids"
    if path.is_file():
        entries = json.loads(path.read_text())
        for entry in entries:
            pid = entry["pid"]
            pgid = entry["pgid"]
            if _pid_alive(pid):
                result = subprocess.run(
                    ["ps", "-o", "command=", "-p", str(pid)],
                    capture_output=True, text=True, check=False,
                )
                command = result.stdout.strip()
                if command.startswith(sys.executable + " "):
                    words = shlex.split(command[len(sys.executable):])
                else:
                    words = shlex.split(command)[1:]
                scripts = {"run_totalsegmentator.py", "build_graph.py"}
                if not words or Path(words[0]).name not in scripts:
                    continue
                try:
                    actual_pgid = os.getpgid(pid)
                except ProcessLookupError:
                    actual_pgid = None
                if actual_pgid is not None:
                    if actual_pgid != pgid or pgid != pid:
                        continue
                    _kill_process_group(pgid, pid)
            case_id = entry["case_id"]
            if not CASE_ID_RE.fullmatch(case_id):
                continue
            kind = "local" if case_id.startswith("local-") else "aeropath"
            job_path = CaseRef(case_id, kind).ts_out / "job.json"
            if job_path.is_file():
                data = json.loads(job_path.read_text())
                if data.get("pid") == pid and data.get("status") in {"queued", "running"}:
                    data.update(status="failed", error="app restarted", pid=None)
                    atomic_write_json(job_path, data)
        path.unlink(missing_ok=True)
    # A crash before publication must not leave an unfinished library entry.
    for case_path in paths.LOCAL_CASES.glob("*/case.json"):
        document = json.loads(case_path.read_text())
        if document.get("import_processing"):
            case_id = case_path.parent.name
            if CASE_ID_RE.fullmatch(case_id):
                for directory in (case_path.parent, paths.TS_OUT / case_id, paths.OUT / case_id):
                    shutil.rmtree(directory, ignore_errors=True)
    paths.TMP_DIR.mkdir(parents=True, exist_ok=True)
    for child in paths.TMP_DIR.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        paths.LOG_DIR / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5,
    )
    handler.setFormatter(_RedactedFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error"):
        owner = logging.getLogger(name)
        if not owner.propagate:
            owner.addHandler(handler)
    logger.info("sidecar started")
    return handler


def close_sidecar_log(handler: RotatingFileHandler | None) -> None:
    """Release handlers when lifespan ends, including embedded test servers."""
    if handler is None:
        return
    for name in ("", "uvicorn", "uvicorn.error"):
        logging.getLogger(name).removeHandler(handler)
    handler.close()
