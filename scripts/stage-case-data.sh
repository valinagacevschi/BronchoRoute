#!/usr/bin/env bash
# Refuses non NIfTI/JSON/NPZ outputs; never touches ~/Downloads/dicom.
set -euo pipefail

CASE_ID="${1:?usage: stage-case-data.sh <case-id> <dest>}"
DEST="${2:?usage: stage-case-data.sh <case-id> <dest>}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIPELINE="$ROOT/pipeline"

if [[ "$DEST" == *"/Downloads/dicom"* ]] || [[ "$DEST" == "$HOME/Downloads/dicom"* ]]; then
  echo "refuse: never stage into ~/Downloads/dicom" >&2
  exit 2
fi

# Exact files the viewer GET path reads (no regenerable crop caches).
FILES=(
  "data/extracted/local/${CASE_ID}/case.json"
  "data/extracted/local/${CASE_ID}/${CASE_ID}_CT.nii.gz"
  "ts_out/${CASE_ID}/lobes.nii.gz"
  "ts_out/${CASE_ID}/lobes/trachea.nii.gz"
  "ts_out/${CASE_ID}/airways.nii.gz"
  "ts_out/${CASE_ID}/airway_bridges.nii.gz"
  "ts_out/${CASE_ID}/airway_bridges.json"
  "out/${CASE_ID}/${CASE_ID}_graph.json"
)

ALLOWED_EXT_RE='\.(nii\.gz|json|npz)$'

mkdir -p "$DEST"
for rel in "${FILES[@]}"; do
  src="$PIPELINE/$rel"
  if [[ ! -f "$src" ]]; then
    echo "missing required file: $rel" >&2
    exit 1
  fi
  base="$(basename "$rel")"
  if [[ ! "$base" =~ $ALLOWED_EXT_RE ]] && [[ ! "$rel" =~ \.nii\.gz$ ]]; then
    echo "refuse non NIfTI/JSON/NPZ: $rel" >&2
    exit 2
  fi
  # Extra safety: path must stay under pipeline and not be a directory.
  if [[ -d "$src" ]]; then
    echo "refuse directory: $rel" >&2
    exit 2
  fi
  out="$DEST/$rel"
  mkdir -p "$(dirname "$out")"
  cp -p "$src" "$out"
  echo "staged $rel"
done

echo "done: ${#FILES[@]} files → $DEST"
