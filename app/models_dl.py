# Changes: manifest-only model downloads with per-user bearer keys and ranges (#58).
"""Read-only model distribution; disabled until download keys are configured."""

import hmac
import json
import logging
import os
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import paths

router = APIRouter()
logger = logging.getLogger("uvicorn.error")
NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


@router.api_route("/models/{name}", methods=["GET", "HEAD"])
def download_model(name: str, request: Request):
    raw_keys = os.environ.get("LUNGNAV_MODEL_KEYS", "").strip()
    keys = json.loads(raw_keys) if raw_keys else {}
    if not keys or not NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise HTTPException(404, "Not Found")

    models = paths.PIPELINE / "models"
    try:
        manifest = json.loads((models / "manifest.json").read_text())
    except FileNotFoundError:
        raise HTTPException(404, "Not Found") from None
    entry = next((item for item in manifest["files"] if item["name"] == name), None)
    target = (models / name).resolve()
    if entry is None or target.parent != models.resolve() or not target.is_file():
        raise HTTPException(404, "Not Found")

    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    candidate = supplied if scheme.lower() == "bearer" else ""
    key_id = None
    for identity, key in keys.items():
        matches = hmac.compare_digest(candidate.encode(), key.encode())
        if matches and key:
            key_id = identity
    if key_id is None:
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    ip = forwarded or (request.client.host if request.client else "-")
    logger.info(
        "model download key=%s ip=%s file=%s range=%s",
        key_id, ip, name, request.headers.get("range", "-"),
    )
    return FileResponse(
        target,
        media_type="application/octet-stream",
        headers={"X-Model-SHA256": entry["sha256"], "Cache-Control": "no-store"},
    )
