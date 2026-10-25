"""Local Lung Navigator app server (127.0.0.1 only).

Phase 3 review: LesionBody isfinite; ClinicianNotSet→412; empty role/initials→422.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from termcolor import cprint

from app.cases import (
    RECORD_KINDS,
    ReviewBlocked,
    UnknownCase,
    append_record,
    case_payload,
    clear_lesion,
    list_cases,
    load_versions,
    set_lesion,
    validate_record_data,
)
from app.ct_crop import build_ct_crop, read_ct_bytes
from app.masks import MasksUnavailable, build_masks_crop, read_masks_bytes
from app.settings import (
    DEFAULT_CT_VIEW,
    ClinicianNotSet,
    get_clinician,
    put_clinician,
    put_ct_view,
    validate_ct_view,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE = REPO_ROOT / "prototype"

app = FastAPI(title="Lung Navigator", docs_url=None, redoc_url=None)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost"],
    www_redirect=False,
)


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


@app.get("/api/cases")
def api_cases():
    return list_cases()


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


@app.put("/api/cases/{case_id}/lesion")
async def api_put_lesion(case_id: str, request: Request):
    """Parse body manually so NaN/inf yield a plain 422 (JSON-safe detail)."""
    clinician = _require_clinician()
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="invalid JSON body") from exc
    try:
        parsed = LesionBody.model_validate(body)
    except ValidationError as exc:
        msgs = []
        for err in exc.errors():
            msg = err.get("msg", "validation error")
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            msgs.append(msg)
        raise HTTPException(status_code=422, detail=msgs[0] if len(msgs) == 1 else msgs) from exc
    try:
        return set_lesion(case_id, parsed.ras_mm, clinician["clinician"])
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


@app.delete("/api/cases/{case_id}/lesion")
def api_delete_lesion(case_id: str):
    clinician = _require_clinician()
    try:
        return clear_lesion(case_id, clinician["clinician"])
    except (UnknownCase, FileNotFoundError, ValueError) as exc:
        raise _http_for_case(exc) from exc


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
    from app.caseref import CaseRef

    try:
        CaseRef.parse(case_id)
    except UnknownCase as exc:
        raise _http_for_case(exc) from exc
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


@app.get("/")
def app_shell():
    return FileResponse(PROTOTYPE / "app.html")


app.mount(
    "/",
    StaticFiles(directory=str(PROTOTYPE), html=False),
    name="prototype",
)


def create_app() -> FastAPI:
    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lung Navigator local app server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("refusing to bind a non-local host")
    import uvicorn

    cprint(f"Lung Navigator on http://{args.host}:{args.port}/", "cyan")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
