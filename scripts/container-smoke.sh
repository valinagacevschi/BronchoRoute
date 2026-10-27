#!/usr/bin/env bash
# Usage: container-smoke.sh <image> <data-dir>
# data-dir must already contain the staged pipeline layout (see stage-case-data.sh).
set -euo pipefail

IMAGE="${1:?usage: container-smoke.sh <image> <data-dir>}"
DATA_DIR="${2:?usage: container-smoke.sh <image> <data-dir>}"
DATA_DIR="$(cd "$DATA_DIR" && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not available" >&2
  exit 1
fi

NAME="lung-nav-smoke-$$"
PORT=18765
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "starting $IMAGE (read-only root, data→/data RO, cache tmpfs)"
docker run -d --name "$NAME" \
  --platform linux/amd64 \
  --read-only \
  --tmpfs /tmp \
  --tmpfs /cache \
  -v "$DATA_DIR:/data:ro" \
  -e LUNGNAV_HOST=0.0.0.0 \
  -e LUNGNAV_PORT=8765 \
  -e LUNGNAV_DATA_ROOT=/data \
  -e LUNGNAV_CACHE_DIR=/cache \
  -e LUNGNAV_VIEWER_ONLY=1 \
  -e LUNGNAV_CASES=local-001 \
  -e LUNGNAV_ALLOWED_HOSTS=lung-nav.appcenter.ro,127.0.0.1,localhost \
  -p "127.0.0.1:${PORT}:8765" \
  "$IMAGE" >/dev/null

BASE="http://127.0.0.1:${PORT}"
for _ in $(seq 1 60); do
  if curl -fsS "$BASE/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "GET /healthz"
test "$(curl -fsS "$BASE/healthz")" = "ok"

echo "GET /"
curl -fsS -o /dev/null -w "%{http_code}\n" "$BASE/" | grep -qx 200

echo "GET /api/cases (only local-001)"
CASES="$(curl -fsS "$BASE/api/cases")"
echo "$CASES" | grep -q 'local-001'
if echo "$CASES" | grep -Eq '"id"[[:space:]]*:[[:space:]]*"(3|[0-9]+)"'; then
  echo "unexpected AeroPath/other case in /api/cases: $CASES" >&2
  exit 1
fi

echo "GET payload / ct meta"
curl -fsS -o /dev/null "$BASE/api/cases/local-001/payload?allow_review=true"
curl -fsS -o /dev/null "$BASE/api/cases/local-001/ct/meta"

echo "POST → 403"
CODE="$(curl -sS -o /dev/null -w "%{http_code}" -X POST "$BASE/api/imports/scan" \
  -H 'Content-Type: application/json' -d '{"src_dir":"/tmp"}')"
test "$CODE" = "403"

echo "GET /workflow → 404"
CODE="$(curl -sS -o /dev/null -w "%{http_code}" "$BASE/workflow")"
test "$CODE" = "404"

echo "smoke ok"
