"""Local Lung Navigator app server (127.0.0.1 only)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from termcolor import cprint

from app.cases import (
    RECORD_KINDS,
    ReviewBlocked,
    UnknownCase,
    append_record,
    case_payload,
    list_cases,
    load_versions,
    validate_record_data,
)
from app.ct_crop import build_ct_crop, read_ct_bytes
from app.settings import get_clinician, put_clinician

REPO_ROOT = Path(__file__).resolve().parent.parent
PROTOTYPE = REPO_ROOT / "prototype"

app = FastAPI(title="Lung Navigator", docs_url=None, redoc_url=None)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost"],
    www_redirect=False,
)


class ClinicianBody(BaseModel):
    role: str = Field(min_length=1)
    initials: str = Field(min_length=1)

    @field_validator("role")
    @classmethod
    def role_chars(cls, value: str) -> str:
        if any(ch in value for ch in "<>'\""):
            raise ValueError("role must not contain <>'\"")
        return value


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


def _http_for_case(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownCase):
        return HTTPException(status_code=404, detail="case not found")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


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


@app.get("/api/clinician")
def api_get_clinician():
    clinician = get_clinician()
    if clinician is None:
        return {"role": None, "initials": None}
    return {"role": clinician["role"], "initials": clinician["initials"]}


@app.put("/api/clinician")
def api_put_clinician(body: ClinicianBody):
    try:
        saved = put_clinician(body.role, body.initials)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"role": saved["role"], "initials": saved["initials"]}


@app.post("/api/cases/{case_id}/records")
def api_post_record(case_id: str, body: RecordBody):
    from app.caseref import CaseRef

    try:
        CaseRef.parse(case_id)
    except UnknownCase as exc:
        raise _http_for_case(exc) from exc
    clinician = get_clinician()
    if clinician is None:
        raise HTTPException(status_code=412, detail="clinician not set")
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
