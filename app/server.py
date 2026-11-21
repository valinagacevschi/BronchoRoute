# Changes: read-only axial nodule contour artifact route for the CT panes (#73).
# Changes: route target accepts generate_realism so the viewer checkbox controls generation (#68).
# Changes: route depth PNG endpoint before the RGB route so kf-N.depth.png resolves (#66).
# Changes: load optional realism dependencies only when realism is requested.
# Changes: register bearer-authenticated model downloads outside /api (#58).
# Changes: realism GET/POST/PNG routes; start after RouteTarget confirm (#57).
# Changes: expose bridge controls outside legacy review with optional evidence notes.
# Changes: accept an optional Series override reason on import confirm.
# Changes: route candidates and RouteTarget require a Lesion, not workflow steps.
# Changes: PUT lesion accepts an optional diameter_mm.
# Changes: Case Library list, delete, and reprocess routes.
# Changes: preserve Archive import and the shared Settings API after rebase.
# Changes: shared settings API alongside the compatible clinician endpoints.
# Changes: serve About (name, version, licence), LICENSE, notices, and sponsors.
# Changes: hide legacy review behind the default-off feature flag.
# Changes: blank BRONCHOROUTE_TOKEN counts as unset so the launch guard stays off.
# Changes: guarded sidecar token, socket handoff, stdin lifecycle and recovery.
# Changes: deploy env — LUNGNAV_VIEWER_ONLY / CASES / HOST / PORT / ALLOWED_HOSTS;
# Changes: opaque Archive API, bounded browser upload and processing cancellation.
# GET /healthz; TrustedHost from env; bind 0.0.0.0 when LUNGNAV_HOST set.
# Phase 4d review fixes — CSP on report; VersionAlreadyFrozen /
# FreezeDirExists / SignoffArtifactError / ReportNotFrozen → 409; remove ack-return.
"""Local Lung Navigator app server (127.0.0.1 by default).

Phase 4a: series/confirm, register-existing, /records 403, jobs.
Phase 4b: review/bridges/corrections/sign-off endpoints.
Phase 4c: RouteTarget candidates and confirmation.
Phase 4d: walk-through, tracing, sign-off freeze, printable report.
"""

from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import socket
import tempfile
import sys
import threading
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import nibabel as nib
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from app.caseref import CASE_ID_RE, CaseRef
from app.cases import (
    CANDIDATE_SCORE_MAX,
    CANDIDATE_SCORE_MIN,
    MAX_CANDIDATES,
    RECORD_KINDS,
    ReviewBlocked,
    UnknownCase,
    append_record,
    case_payload,
    clear_candidates,
    clear_lesion,
    confirm_candidate,
    confirm_existing_series,
    list_cases,
    load_versions,
    qualifying_route_start_terminals,
    set_candidates,
    set_lesion,
    set_route_start,
    validate_lesion_diameter,
    validate_record_data,
)
from app.ct_crop import build_ct_crop, read_ct_bytes
from app.imports import ImportDuplicate, import_and_process, release_import, scan_series, MAX_ARCHIVE_BYTES
from app.library import delete_case, delete_library, list_library
from app.jobs import (
    JobConflict,
    cancel_latest_job,
    initialize_sidecar,
    close_sidecar_log,
    latest_job,
    register_segment_artifacts,
    start_segment_job,
    terminate_all_jobs,
)
from app.masks import MasksUnavailable, build_masks_crop, read_masks_bytes
from app.models_dl import router as models_router
from app.settings import (
    DEFAULT_CT_VIEW,
    ClinicianNotSet,
    get_clinician,
    get_settings,
    put_clinician,
    put_settings,
    put_ct_view,
    validate_ct_view,
)
from app.review import (
    add_correction,
    bridge_state,
    apply_review_signoff,
    review_state,
    set_bridge_decision,
    undo_correction,
)
from app.route_target import route_target_candidates, set_route_target
from app.signoff import SignoffArtifactError, apply_signoff, version_report_html
from app.versions import (
    REPORT_CSP,
    FreezeDirExists,
    ReportNotFrozen,
    VersionAlreadyFrozen,
)
from app.walkthrough import (
    compute_uncertainty,
    set_walkthrough_decision,
    tracing_sequence,
)
from app.workflow import (
    WORKFLOW_COMPLETION_KINDS,
    WorkflowBlocked,
    WorkflowStep,
    derive_workflow,
    load_graph,
    require_prior_steps,
)
import paths

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE = REPO_ROOT / "prototype"
PRODUCT_NAME = "BronchoRoute"
PRODUCT_LICENCE = "GPL-3.0-or-later"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_ALLOWED_HOSTS = ["127.0.0.1", "localhost"]


def _allowed_hosts() -> list[str]:
    raw = os.environ.get("LUNGNAV_ALLOWED_HOSTS", "").strip()
    if not raw:
        return list(_DEFAULT_ALLOWED_HOSTS)
    hosts = [part.strip() for part in raw.split(",") if part.strip()]
    return hosts or list(_DEFAULT_ALLOWED_HOSTS)


class _DeployGuards(BaseHTTPMiddleware):
    """TrustedHost + viewer-only ( /workflow 404, non-GET /api/* 403 )."""

    async def dispatch(self, request: Request, call_next):
        token = os.environ.get("BRONCHOROUTE_TOKEN", "").strip()
        supplied = request.headers.get("X-BronchoRoute-Token", "")
        if token and not hmac.compare_digest(supplied.encode(), token.encode()):
            return Response("Unauthorized", status_code=401)
        host = (request.headers.get("host") or "").split(":", 1)[0]
        allowed = _allowed_hosts()
        if "*" not in allowed and host not in allowed:
            return Response("Invalid host header", status_code=400)
        path = request.url.path
        parts = path.strip("/").split("/")
        if (
            not paths.legacy_review()
            and len(parts) >= 4
            and parts[:2] == ["api", "cases"]
            and parts[3] in {
                "review", "corrections", "review-signoff", "versions",
                "walkthrough", "tracing", "uncertainty", "signoff",
            }
        ):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if paths.viewer_only():
            if path == "/workflow" or path.startswith("/workflow/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            if path.startswith("/api/") and request.method not in {
                "GET", "HEAD", "OPTIONS",
            }:
                return JSONResponse(
                    {"detail": "viewer-only: writes disabled"},
                    status_code=403,
                )
        return await call_next(request)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    log_handler = initialize_sidecar()
    try:
        yield
    finally:
        terminate_all_jobs()
        close_sidecar_log(log_handler)


app = FastAPI(title="BronchoRoute", docs_url=None, redoc_url=None, lifespan=_lifespan)
app.add_middleware(_DeployGuards)
app.include_router(models_router)


class ClinicianBody(BaseModel):
    role: str | None = None
    initials: str | None = None
    ct_view: Literal["feet", "head"] | None = None

    @field_validator("role")
    @classmethod
    def role_chars(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if any(ch in value for ch in "<>'\""):
            raise ValueError("role must not contain <>'\"")
        return value

    @model_validator(mode="after")
    def require_identity_or_view(self) -> ClinicianBody:
        role_set = self.role is not None
        initials_set = self.initials is not None
        if role_set ^ initials_set:
            raise ValueError("role and initials must be provided together")
        if role_set and initials_set:
            if not str(self.role).strip() or not str(self.initials).strip():
                raise ValueError("role and initials must be non-empty")
            return self
        if self.ct_view is not None:
            return self
        raise ValueError("provide role+initials and/or ct_view")


class RecordBody(BaseModel):
    step: int
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("step")
    @classmethod
    def step_range(cls, value: int) -> int:
        if not 1 <= value <= 8:
            raise ValueError("step must be an integer from 1 to 8")
        return value

    @field_validator("kind")
    @classmethod
    def kind_allowed(cls, value: str) -> str:
        if value not in RECORD_KINDS:
            raise ValueError(f"kind must be one of {sorted(RECORD_KINDS)}")
        return value

    @field_validator("data")
    @classmethod
    def data_allowed(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_record_data(value)
        return value


class LesionBody(BaseModel):
    ras_mm: list[float] = Field(min_length=3, max_length=3)
    diameter_mm: float | None = None

    @field_validator("diameter_mm")
    @classmethod
    def diameter_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        # Reject before set_lesion so a bad diameter is a 422, not a stored default.
        validate_lesion_diameter(value)
        return float(value)

    @field_validator("ras_mm")
    @classmethod
    def finite_coords(cls, value: list[float]) -> list[float]:
        # Reject NaN/inf here; raise ValueError (not leave non-finite in ctx for JSON).
        if len(value) != 3:
            raise ValueError("ras_mm must be a length-3 list")
        cleaned: list[float] = []
        for v in value:
            try:
                f = float(v)
            except (TypeError, ValueError) as exc:
                raise ValueError("ras_mm values must be numbers") from exc
            if not math.isfinite(f):
                raise ValueError("ras_mm values must be finite numbers")
            cleaned.append(f)
        return cleaned


class CandidateItem(LesionBody):
    """A detector suggestion: a lesion shape plus a confidence score."""

    score: float | None = Field(
        default=None,
        ge=CANDIDATE_SCORE_MIN,
        le=CANDIDATE_SCORE_MAX,
        allow_inf_nan=False,
    )


class CandidatesBody(BaseModel):
    candidates: list[CandidateItem] = Field(
        min_length=1, max_length=MAX_CANDIDATES
    )
    source: str = "detector"


class ImportScanBody(BaseModel):
    path: str


class ImportConfirmBody(BaseModel):
    import_id: str
    series_key: str
    override_reason: str = ""


class RouteStartBody(BaseModel):
    node: int


class WalkthroughBody(BaseModel):
    confirmed: bool
    note: str | None = None
    return_to: int | None = None

    @model_validator(mode="after")
    def _return_to_when_no(self) -> "WalkthroughBody":
        if self.confirmed and self.return_to is not None:
            raise ValueError("return_to only applies when confirmed is false")
        if not self.confirmed and self.return_to not in {
            int(WorkflowStep.REVIEW),
            int(WorkflowStep.ROUTE_TARGET),
        }:
            raise ValueError("return_to must be 4 or 6 when confirmed is false")
        return self


class SignoffBody(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)


class RouteTargetBody(BaseModel):
    edge_id: int | None = None
    sample_index: int | None = None
    ras_mm: list[float] | None = None
    # The viewer asks for realism only when its checkbox is on; older clients
    # omit the field and keep the previous auto-start behaviour.
    generate_realism: bool = True

    @model_validator(mode="after")
    def _one_form(self) -> "RouteTargetBody":
        has_edge = self.edge_id is not None
        has_index = self.sample_index is not None
        has_ras = self.ras_mm is not None
        if has_edge != has_index:
            raise ValueError("edge_id and sample_index are both required")
        has_sample = has_edge and has_index
        if has_sample and has_ras:
            raise ValueError("provide either {edge_id, sample_index} or {ras_mm}, not both")
        if not has_sample and not has_ras:
            raise ValueError("provide {edge_id, sample_index} or {ras_mm}")
        if has_ras:
            if not isinstance(self.ras_mm, list) or len(self.ras_mm) != 3:
                raise ValueError("ras_mm must be a length-3 list")
            if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in self.ras_mm):
                raise ValueError("ras_mm values must be finite numbers")
        return self


def _validation_detail(exc: ValidationError) -> str | list[str]:
    msgs = []
    for err in exc.errors():
        msg = err.get("msg", "validation error")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        msgs.append(msg)
    return msgs[0] if len(msgs) == 1 else msgs


async def _json_body(request: Request) -> object:
    try:
        return await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="invalid JSON body") from exc


class SeriesConfirmBody(BaseModel):
    overridden: bool = False


class BridgeDecisionBody(BaseModel):
    decision: Literal["confirmed", "rejected"]
    note: str | None = None


class CorrectionBody(BaseModel):
    kind: Literal["remove_branch"]
    edge_id: int
    note: str


class ReviewSignoffBody(BaseModel):
    kind: str
    lobe: str
    decision: Literal["approved", "rejected"]
    finding: str


def _http_for_case(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownCase):
        return HTTPException(status_code=404, detail="case not found")
    if isinstance(exc, MasksUnavailable):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def _http_for_review(exc: Exception) -> HTTPException:
    """Shared exception→HTTP mapping for review mutators."""
    if isinstance(exc, WorkflowBlocked):
        return HTTPException(status_code=409, detail=exc.reason)
    return _http_for_case(exc)


def _require_clinician() -> dict:
    clinician = get_clinician()
    if clinician is None:
        raise HTTPException(status_code=412, detail="clinician not set")
    return clinician


def _clinician_response(data: dict | None) -> dict:
    if data is None:
        return {"role": None, "initials": None, "ct_view": DEFAULT_CT_VIEW}
    return {
        "role": data["role"],
        "initials": data["initials"],
        "ct_view": data.get("ct_view", DEFAULT_CT_VIEW),
    }


@app.get("/healthz")
def healthz():
    """Liveness/readiness: no data access."""
    return Response(content="ok", media_type="text/plain")


@app.get("/api/features")
def api_features():
    return {
        "legacy_review": paths.legacy_review(),
        "viewer_only": paths.viewer_only(),
    }


@app.get("/api/cases")
def api_cases():
    return list_cases()


@app.get("/api/library")
def api_library():
    return list_library()


@app.delete("/api/library")
def api_library_delete():
    try:
        return delete_library()
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/cases/{case_id}")
def api_case_delete(case_id: str):
    try:
        return delete_case(case_id)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/reprocess")
def api_reprocess(case_id: str):
    clinician = _require_clinician()
    try:
        ref = CaseRef.parse(case_id)
        if ref.kind != "local":
            raise UnknownCase(f"unknown case: {case_id}")
        return start_segment_job(case_id, clinician["clinician"], reprocess=True)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/workflow")
def api_workflow(case_id: str):
    try:
        return derive_workflow(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/imports/scan")
def api_import_scan(body: ImportScanBody):
    try:
        return scan_series(body.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/imports/upload")
async def api_import_upload(request: Request):
    paths.TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(suffix=".zip", dir=paths.TMP_DIR)
    try:
        with os.fdopen(fd, "wb") as target:
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise HTTPException(status_code=413, detail="Archive size limit exceeded")
                target.write(chunk)
        return await run_in_threadpool(scan_series, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        Path(filename).unlink(missing_ok=True)


@app.delete("/api/imports/{import_id}")
def api_import_release(import_id: str):
    release_import(import_id)
    return {"released": True}


@app.post("/api/cases/{case_id}/jobs/latest/cancel")
def api_job_cancel(case_id: str):
    try:
        if not CASE_ID_RE.fullmatch(case_id):
            raise UnknownCase("invalid Case")
        return cancel_latest_job(case_id)
    except (UnknownCase, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/imports")
def api_import_confirm(body: ImportConfirmBody):
    clinician = _require_clinician()
    try:
        return import_and_process(
            body.import_id, body.series_key, clinician["clinician"],
            override_reason=body.override_reason,
        )
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImportDuplicate as exc:
        return JSONResponse(status_code=409, content={"detail": exc.message})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (UnknownCase, FileNotFoundError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/series/confirm")
def api_series_confirm(case_id: str, body: SeriesConfirmBody):
    """Confirm series already present in case.json source (no silent backfill)."""
    clinician = _require_clinician()
    try:
        return confirm_existing_series(
            case_id, clinician["clinician"], overridden=body.overridden
        )
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/segment")
def api_segment(case_id: str):
    clinician = _require_clinician()
    try:
        CaseRef.parse(case_id)
        return start_segment_job(case_id, clinician["clinician"])
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/segment/register-existing")
def api_segment_register_existing(case_id: str):
    """Register on-disk segment outputs when run.json markers match current CT."""
    from run_totalsegmentator import ct_identity

    clinician = _require_clinician()
    try:
        require_prior_steps(case_id, int(WorkflowStep.SEGMENT))
        ref = CaseRef.parse(case_id)
        current = ct_identity(ref.ct)
        for marker in (
            ref.ts_out / "airways_source" / "run.json",
            ref.ts_out / "lobes" / "run.json",
        ):
            if not marker.is_file():
                raise ValueError(f"missing run.json marker: {marker.name} under {marker.parent.name}")
            options = json.loads(marker.read_text())
            marker_ct = options.get("ct") if isinstance(options, dict) else None
            if marker_ct != current:
                raise ValueError(
                    f"run.json CT identity mismatch in {marker.parent.name} "
                    "(re-run segmentation or adopt-cache for the current CT)"
                )
        entry = register_segment_artifacts(
            case_id, clinician["clinician"], registered_existing=True
        )
        return {"record": entry, "registered_existing": True}
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"invalid run.json: {exc}") from exc


@app.get("/api/cases/{case_id}/jobs/latest")
def api_job_latest(case_id: str):
    try:
        if not CASE_ID_RE.fullmatch(case_id):
            raise UnknownCase("invalid Case")
        job = latest_job(case_id)
    except UnknownCase as exc:
        raise _http_for_case(exc) from exc
    if job is None:
        return {"case_id": case_id, "status": "none", "log_tail": [], "elapsed_s": 0}
    return job


@app.put("/api/cases/{case_id}/route-start")
def api_route_start(case_id: str, body: RouteStartBody):
    clinician = _require_clinician()
    try:
        require_prior_steps(case_id, int(WorkflowStep.ROUTE_START))
        return set_route_start(case_id, body.node, clinician["clinician"])
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/route-start/candidates")
def api_route_start_candidates(case_id: str):
    try:
        ref = CaseRef.parse(case_id)
        graph = load_graph(ref=ref)
        return {"candidates": qualifying_route_start_terminals(graph)}
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/route-candidates")
def api_route_candidates(case_id: str):
    try:
        return {"candidates": route_target_candidates(case_id)}
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.put("/api/cases/{case_id}/route-target")
async def api_route_target(case_id: str, request: Request):
    """Parse body manually so NaN/inf yield a plain 422; gate runs under lock."""
    clinician = _require_clinician()
    body = await _json_body(request)
    try:
        body = RouteTargetBody.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    try:
        result = set_route_target(
            case_id,
            clinician["clinician"],
            edge_id=body.edge_id,
            sample_index=body.sample_index,
            ras_mm=body.ras_mm,
        )
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    if body.generate_realism:
        try:
            from app.realism.worker import start_realism

            start_realism(case_id)
        except Exception:  # noqa: BLE001 — confirm already succeeded; realism is best-effort
            pass
    return result


@app.get("/api/cases/{case_id}/realism")
def api_realism_status(case_id: str):
    try:
        from app.realism.worker import realism_status
    except ImportError:
        return {"status": "unavailable", "reason": "realism not installed", "key": None, "frames": []}
    try:
        return realism_status(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/realism/start")
def api_realism_start(case_id: str):
    try:
        from app.realism.worker import start_realism
    except ImportError:
        return {"status": "unavailable", "reason": "realism not installed", "key": None, "frames": []}
    try:
        return start_realism(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


def _route_realism(case_id: str, action: str):
    try:
        from app.realism import route_keyframes
    except ImportError:
        return {"status": "unavailable", "reason": "realism not installed", "key": None,
                "done": 0, "total": 0, "frames": []}
    try:
        return getattr(route_keyframes, action)(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/realism/route")
def api_realism_route(case_id: str):
    return _route_realism(case_id, "route_status")


@app.post("/api/cases/{case_id}/realism/route/start")
def api_realism_route_start(case_id: str):
    return _route_realism(case_id, "start_route")


@app.post("/api/cases/{case_id}/realism/route/cancel")
def api_realism_route_cancel(case_id: str):
    return _route_realism(case_id, "cancel_route")


@app.get("/api/cases/{case_id}/realism/route/{key}/kf-{index}.depth.png")
def api_realism_route_depth_png(case_id: str, key: str, index: int):
    try:
        from app.realism.route_keyframes import route_depth_png_path
    except ImportError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    try:
        path = route_depth_png_path(case_id, key, index)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/cases/{case_id}/realism/route/{key}/kf-{index}.png")
def api_realism_route_png(case_id: str, key: str, index: int):
    try:
        from app.realism.route_keyframes import route_png_path
    except ImportError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    try:
        path = route_png_path(case_id, key, index)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/cases/{case_id}/realism/{key}/{name}.png")
def api_realism_png(case_id: str, key: str, name: str):
    try:
        from app.realism.worker import realism_png_path
    except ImportError:
        raise HTTPException(status_code=404, detail="Not Found") from None
    try:
        path = realism_png_path(case_id, key, name)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/cases/{case_id}/review")
def api_review(case_id: str):
    try:
        return review_state(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_review(exc) from exc


@app.get("/api/cases/{case_id}/bridges")
def api_bridges(case_id: str):
    try:
        return bridge_state(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_review(exc) from exc


@app.put("/api/cases/{case_id}/bridges/{bridge_id}")
def api_bridge_decision(case_id: str, bridge_id: str, body: BridgeDecisionBody):
    clinician = _require_clinician()
    try:
        return set_bridge_decision(
            case_id, bridge_id, body.decision, body.note,
            clinician["clinician"] if paths.legacy_review() else clinician["initials"],
        )
    except (WorkflowBlocked, UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_review(exc) from exc


@app.post("/api/cases/{case_id}/corrections")
def api_add_correction(case_id: str, body: CorrectionBody):
    clinician = _require_clinician()
    try:
        if body.kind != "remove_branch":
            raise ValueError("kind must be remove_branch")
        return add_correction(
            case_id, body.edge_id, body.note, clinician["clinician"]
        )
    except (WorkflowBlocked, UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_review(exc) from exc


@app.delete("/api/cases/{case_id}/corrections/{correction_id}")
def api_undo_correction(case_id: str, correction_id: str):
    clinician = _require_clinician()
    try:
        return undo_correction(case_id, correction_id, clinician["clinician"])
    except (WorkflowBlocked, UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_review(exc) from exc


@app.post("/api/cases/{case_id}/review-signoff")
def api_review_signoff(case_id: str, body: ReviewSignoffBody):
    clinician = _require_clinician()
    try:
        return apply_review_signoff(
            case_id,
            kind=body.kind,
            lobe=body.lobe,
            decision=body.decision,
            finding=body.finding,
            clinician=clinician["clinician"],
        )
    except (WorkflowBlocked, UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_review(exc) from exc


@app.get("/api/cases/{case_id}/payload")
def api_payload(case_id: str, allow_review: bool = Query(False)):
    try:
        return case_payload(case_id, allow_review=allow_review)
    except ReviewBlocked as exc:
        return JSONResponse(status_code=409, content={"unresolved": exc.unresolved})
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/ct/meta")
def api_ct_meta(case_id: str):
    try:
        meta = build_ct_crop(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    public = {k: v for k, v in meta.items() if k != "inputs"}
    public["inputs"] = {
        name: {"size": info["size"], "mtime_ns": info["mtime_ns"]}
        for name, info in meta.get("inputs", {}).items()
    }
    return public


@app.get("/api/cases/{case_id}/ct")
def api_ct(case_id: str):
    try:
        raw, meta = read_ct_bytes(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"X-Content-SHA256": meta["sha256"]},
    )


@app.get("/api/cases/{case_id}/masks/meta")
def api_masks_meta(case_id: str):
    try:
        meta = build_masks_crop(case_id)
    except (UnknownCase, MasksUnavailable, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    public = {k: v for k, v in meta.items() if k != "inputs"}
    public["inputs"] = meta.get("inputs", {})
    cleaned = {}
    for name, info in public["inputs"].items():
        if isinstance(info, dict) and "path" in info:
            cleaned[name] = {"size": info["size"], "mtime_ns": info["mtime_ns"]}
        else:
            cleaned[name] = info
    public["inputs"] = cleaned
    return public


@app.get("/api/cases/{case_id}/masks")
def api_masks(case_id: str):
    try:
        raw, meta = read_masks_bytes(case_id)
    except (UnknownCase, MasksUnavailable, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"X-Content-SHA256": meta["sha256"]},
    )


@app.get("/api/cases/{case_id}/nodule-contours")
def api_nodule_contours(case_id: str):
    """Axial contour artifact written by the nodule stage (#71). Read-only."""
    try:
        ref = CaseRef.parse(case_id)
    except (UnknownCase, ValueError) as exc:
        raise _http_for_case(exc) from exc
    path = ref.ts_out / "nodule_contours.json"
    if not path.is_file():
        # Optional overlay: an empty artifact keeps the panes unchanged and
        # avoids a 404 console error in every Case without a nodule stage.
        return JSONResponse({"shape": [], "nodules": []})
    artifact = json.loads(path.read_text())
    # The stage traces in the CT array frame; the browser shows a canonical
    # crop. Send the CT affine so the viewer can bridge the two grids.
    try:
        artifact["affine"] = nib.load(ref.ct).affine.tolist()
    except Exception:  # noqa: BLE001 - optional bridge; the viewer falls back
        pass
    # A rebuild rewrites the artifact under the same path.
    return JSONResponse(artifact, headers={"Cache-Control": "no-cache"})


@app.put("/api/cases/{case_id}/lesion")
async def api_put_lesion(case_id: str, request: Request):
    """Parse body manually so NaN/inf yield a plain 422 (JSON-safe detail)."""
    clinician = _require_clinician()
    try:
        require_prior_steps(case_id, int(WorkflowStep.LESION))
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc
    body = await _json_body(request)
    try:
        parsed = LesionBody.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    try:
        return set_lesion(
            case_id, parsed.ras_mm, clinician["clinician"], parsed.diameter_mm
        )
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.delete("/api/cases/{case_id}/lesion")
def api_delete_lesion(case_id: str):
    clinician = _require_clinician()
    try:
        return clear_lesion(case_id, clinician["clinician"])
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.put("/api/cases/{case_id}/candidates")
async def api_put_candidates(case_id: str, request: Request):
    """Store detector candidates. Suggestions only until one is confirmed."""
    clinician = _require_clinician()
    body = await _json_body(request)
    try:
        parsed = CandidatesBody.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_detail(exc)) from exc
    try:
        return set_candidates(
            case_id,
            [item.model_dump() for item in parsed.candidates],
            parsed.source,
            clinician["clinician"],
        )
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.delete("/api/cases/{case_id}/candidates")
def api_delete_candidates(case_id: str):
    clinician = _require_clinician()
    try:
        return clear_candidates(case_id, clinician["clinician"])
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/candidates/{index}/confirm")
def api_confirm_candidate(case_id: str, index: int):
    clinician = _require_clinician()
    try:
        return confirm_candidate(case_id, index, clinician["clinician"])
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/settings")
def api_get_settings():
    return get_settings()


@app.put("/api/settings")
async def api_put_settings(request: Request):
    try:
        return put_settings(await request.json())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/clinician")
def api_get_clinician():
    return _clinician_response(get_clinician())


@app.put("/api/clinician")
def api_put_clinician(body: ClinicianBody):
    try:
        if body.role is not None and body.initials is not None:
            saved = put_clinician(body.role, body.initials, ct_view=body.ct_view)
        else:
            if body.ct_view is None:
                raise ValueError("provide role+initials and/or ct_view")
            validate_ct_view(body.ct_view)
            saved = put_ct_view(body.ct_view)
    except ClinicianNotSet as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _clinician_response(saved)


@app.post("/api/cases/{case_id}/records")
def api_post_record(case_id: str, body: RecordBody):
    try:
        CaseRef.parse(case_id)
    except UnknownCase as exc:
        raise _http_for_case(exc) from exc
    if body.kind in WORKFLOW_COMPLETION_KINDS:
        raise HTTPException(status_code=403, detail="use the step endpoint")
    clinician = _require_clinician()
    try:
        return append_record(
            case_id, body.step, body.kind, body.data, clinician["clinician"]
        )
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/versions")
def api_versions(case_id: str):
    try:
        return load_versions(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.put("/api/cases/{case_id}/walkthrough/{fork_index}")
def api_walkthrough(case_id: str, fork_index: int, body: WalkthroughBody):
    clinician = _require_clinician()
    try:
        return set_walkthrough_decision(
            case_id,
            fork_index,
            clinician["clinician"],
            confirmed=body.confirmed,
            note=body.note,
            return_to=body.return_to,
        )
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/tracing")
def api_tracing(case_id: str):
    try:
        return tracing_sequence(case_id)
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/uncertainty")
def api_uncertainty(case_id: str):
    try:
        require_prior_steps(case_id, int(WorkflowStep.SIGNOFF))
        return {"uncertainty": compute_uncertainty(case_id)}
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.post("/api/cases/{case_id}/signoff")
def api_signoff(case_id: str, body: SignoffBody):
    clinician = _require_clinician()
    try:
        return apply_signoff(case_id, clinician["clinician"], body.statement)
    except WorkflowBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (VersionAlreadyFrozen, FreezeDirExists, SignoffArtifactError) as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.get("/api/cases/{case_id}/versions/{version_n}/report")
def api_version_report(case_id: str, version_n: int):
    try:
        html_body = version_report_html(case_id, version_n)
        return HTMLResponse(
            html_body,
            headers={"Content-Security-Policy": REPORT_CSP},
        )
    except ReportNotFrozen as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


def _project_version() -> str:
    """Single version source: pipeline/pyproject.toml."""
    pyproject = REPO_ROOT / "pipeline" / "pyproject.toml"
    with pyproject.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


@app.get("/api/about")
def api_about():
    return {
        "name": PRODUCT_NAME,
        "version": _project_version(),
        "licence": PRODUCT_LICENCE,
    }


@app.get("/LICENSE")
def licence_file():
    return FileResponse(
        REPO_ROOT / "LICENSE",
        media_type="text/plain; charset=utf-8",
        content_disposition_type="inline",
    )


@app.get("/THIRD_PARTY_NOTICES.md")
def third_party_notices():
    return FileResponse(
        REPO_ROOT / "THIRD_PARTY_NOTICES.md",
        media_type="text/plain; charset=utf-8",
        content_disposition_type="inline",
    )


@app.get("/sponsors.json")
def sponsors_file():
    path = REPO_ROOT / "sponsors.json"
    if not path.is_file():
        return JSONResponse([])
    return FileResponse(path, media_type="application/json")


@app.get("/")
def viewer_page():
    """Main page: left-rail renderer in read-only viewer mode."""
    return FileResponse(PROTOTYPE / "renderer.template.html")


@app.get("/case-web/{case_id}/{name}")
def case_web(case_id: str, name: str):
    """Surface mesh assets for the 3D endoluminal view."""
    try:
        ref = CaseRef.parse(case_id)
    except UnknownCase as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    base = (ref.case_json.parent / "web").resolve()
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        return JSONResponse({"error": "invalid path"}, status_code=404)
    if not target.is_file():
        return JSONResponse({"error": "not built"}, status_code=404)
    media = "application/octet-stream"
    if name.endswith(".json"):
        media = "application/json"
    # A rebuild rewrites these files under the same paths; without this the
    # browser's heuristic cache keeps serving the previous scene.
    return FileResponse(target, media_type=media,
                        headers={"Cache-Control": "no-cache"})


app.mount(
    "/",
    StaticFiles(directory=str(PROTOTYPE), html=False),
    name="prototype",
)


def create_app() -> FastAPI:
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="BronchoRoute")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--exit-on-stdin-close", action="store_true")
    args = parser.parse_args(argv)
    env_host = os.environ.get("LUNGNAV_HOST", "").strip()
    env_port = os.environ.get("LUNGNAV_PORT", "").strip()
    host = env_host or args.host
    port = int(env_port) if env_port else args.port
    if not env_host and host not in _LOCAL_HOSTS:
        raise SystemExit("refusing to bind a non-local host")
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)

    def watch_stdin() -> None:
        while os.read(sys.stdin.fileno(), 4096):
            pass
        terminate_all_jobs()
        server.should_exit = True

    sock = None
    try:
        if port == 0:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            print(f"BRONCHOROUTE_PORT={sock.getsockname()[1]}", flush=True)
        if args.exit_on_stdin_close:
            threading.Thread(target=watch_stdin, daemon=True).start()
        server.run(sockets=[sock] if sock is not None else None)
    finally:
        if sock is not None:
            sock.close()


if __name__ == "__main__":
    main()
