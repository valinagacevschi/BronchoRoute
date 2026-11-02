#!/bin/bash
# Changes: checksum-pinned standalone Python with frozen runtime dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
build="$PWD/build"
python="$build/python/bin/python3"
archive="$build/cpython-3.12.16+20261026-aarch64-apple-darwin-install_only.tar.gz"
sha256="fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e"
url="https://github.com/astral-sh/python-build-standalone/releases/download/20261026/$(basename "$archive")"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Build requires an Apple Silicon Mac." >&2
  exit 1
fi
mkdir -p "$build"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --retry 3 "$url" -o "$archive.partial"
  mv "$archive.partial" "$archive"
fi
echo "$sha256  $archive" | shasum -a 256 --check
# Recreate the prefix so removed lock dependencies cannot survive a rebuild.
rm -rf "$build/python"
tar -xzf "$archive" -C "$build"
(
  cd ../pipeline
  env -u PYTHONPATH uv export --frozen --no-dev --no-hashes --no-emit-project > "$build/runtime-requirements.txt"
)
env -u PYTHONPATH uv pip install --python "$python" -r "$build/runtime-requirements.txt"
env -u PYTHONPATH PYTHONNOUSERSITE=1 "$python" -m compileall -q "$build/python"
"$python" --version
du -sh "$build/python" "$archive"
