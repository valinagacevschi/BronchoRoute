#!/bin/bash
# Changes: checksum-pinned LGPL arm64 ffmpeg with h264_videotoolbox only.
set -euo pipefail
cd "$(dirname "$0")/.."
build="$PWD/build"
out="$build/ffmpeg"
version="8.1.3"
archive="$build/ffmpeg-${version}.tar.xz"
# SHA-256 of the ffmpeg.org release tarball (no published SHA256SUMS; PGP .asc exists).
sha256="7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3"
url="https://ffmpeg.org/releases/$(basename "$archive")"
src="$build/ffmpeg-${version}"
jobs="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "Build requires an Apple Silicon Mac." >&2
  exit 1
fi

for tool in curl shasum tar make clang otool; do
  if ! command -v "$tool" >/dev/null; then
    echo "Missing required tool: $tool" >&2
    exit 1
  fi
done
if ! command -v pkg-config >/dev/null; then
  echo "Missing required tool: pkg-config (stop; do not Homebrew-install from this script)." >&2
  exit 1
fi

mkdir -p "$build"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --retry 3 "$url" -o "$archive.partial"
  mv "$archive.partial" "$archive"
fi
echo "$sha256  $archive" | shasum -a 256 --check

rm -rf "$src" "$out"
tar -xJf "$archive" -C "$build"
mkdir -p "$out"

configure_args=(
  --disable-everything
  --disable-network
  --disable-autodetect
  --disable-doc
  --disable-ffplay
  --disable-ffprobe
  --disable-htmlpages
  --disable-manpages
  --disable-podpages
  --disable-txtpages
  --disable-gpl
  --disable-nonfree
  --disable-shared
  --enable-static
  --enable-ffmpeg
  --enable-avutil
  --enable-avcodec
  --enable-avformat
  --enable-avfilter
  --enable-swscale
  --enable-videotoolbox
  --enable-decoder=mjpeg
  --enable-parser=mjpeg
  --enable-demuxer=image2pipe
  --enable-demuxer=mov
  --enable-muxer=mp4
  --enable-encoder=h264_videotoolbox
  --enable-filter=scale
  --enable-filter=format
  --enable-protocol=file
  --enable-protocol=pipe
  --enable-protocol=fd
  --extra-cflags=-mmacosx-version-min=14.0
  --extra-ldflags=-mmacosx-version-min=14.0
)

(
  cd "$src"
  ./configure "${configure_args[@]}"
  make -j"$jobs"
  cp -f ffmpeg "$out/ffmpeg"
)

chmod 755 "$out/ffmpeg"

buildconf="$("$out/ffmpeg" -hide_banner -buildconf 2>&1 || true)"
if printf '%s\n' "$buildconf" | grep -E -- '--enable-gpl|--enable-nonfree' >/dev/null; then
  echo "ffmpeg build must not enable GPL or nonfree." >&2
  printf '%s\n' "$buildconf" >&2
  exit 1
fi
encoders="$("$out/ffmpeg" -hide_banner -encoders 2>&1 || true)"
if ! printf '%s\n' "$encoders" | grep -F 'h264_videotoolbox' >/dev/null; then
  echo "ffmpeg build is missing h264_videotoolbox." >&2
  printf '%s\n' "$encoders" >&2
  exit 1
fi

otool_out="$(otool -L "$out/ffmpeg")"
if printf '%s\n' "$otool_out" | grep -E '/opt/homebrew|/usr/local/Cellar|/usr/local/opt' >/dev/null; then
  echo "ffmpeg links Homebrew libraries:" >&2
  printf '%s\n' "$otool_out" >&2
  exit 1
fi

out_sha="$(shasum -a 256 "$out/ffmpeg" | awk '{print $1}')"
configure_line="./configure ${configure_args[*]}"
{
  echo "version: ${version}"
  echo "source_url: ${url}"
  echo "source_sha256: ${sha256}"
  echo "configure: ${configure_line}"
  echo "output_sha256: ${out_sha}"
} > "$out/BUILD_INFO.txt"

"$out/ffmpeg" -hide_banner -version | head -n 1
du -h "$out/ffmpeg" "$archive"
cat "$out/BUILD_INFO.txt"
