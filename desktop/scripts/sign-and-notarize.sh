#!/bin/bash
# Changes: sign bundled ffmpeg with hardened runtime and no entitlements.
# Changes: sign the packaged app inside out, then notarize when NOTARY_PROFILE is set.
set -euo pipefail
export LANG=C LC_ALL=C

caller_pwd=$PWD
desktop=$(cd "$(dirname "$0")/.." && pwd)
cd "$desktop"

TEAM_ID="${TEAM_ID:-488B4W32U2}"
if [[ ! "$TEAM_ID" =~ ^[A-Z0-9]{10}$ ]]; then
  echo "TEAM_ID must be a 10-character Apple team id." >&2
  exit 1
fi

if [[ -n "${APP:-}" && "$APP" != /* ]]; then
  APP="$caller_pwd/$APP"
fi
APP="${APP:-$desktop/dist/mac-arm64/BronchoRoute.app}"
PYTHON_ENTS="$desktop/build-resources/python.entitlements.plist"
APP_ENTS="$desktop/build-resources/app.entitlements.plist"

adhoc=0
if [[ "${IDENTITY-}" == "-" ]]; then
  adhoc=1
  IDENTITY="-"
elif [[ -z "${IDENTITY:-}" ]]; then
  IDENTITY=""
fi

detect_identity() {
  local team="$1"
  local names count
  names=$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | grep -F "(${team})" || true)
  if [[ -z "$names" ]]; then
    echo "No Developer ID Application identity for team ${team} is in the keychain." >&2
    echo "Install that certificate, or run: npm run sign:adhoc" >&2
    exit 1
  fi
  count=$(printf '%s\n' "$names" | wc -l | tr -d ' ')
  if [[ "$count" -ne 1 ]]; then
    echo "Expected one Developer ID Application identity for team ${team}, found ${count}." >&2
    echo "Set IDENTITY to the certificate name to use." >&2
    printf '%s\n' "$names" >&2
    exit 1
  fi
  printf '%s\n' "$names"
}

if [[ "$adhoc" -eq 0 && -z "$IDENTITY" ]]; then
  IDENTITY=$(detect_identity "$TEAM_ID")
fi

if [[ ! -d "$APP" ]]; then
  echo "App not found: $APP" >&2
  echo "Run npm run pack first." >&2
  exit 1
fi

plutil -lint "$PYTHON_ENTS" >/dev/null
plutil -lint "$APP_ENTS" >/dev/null

work=$(mktemp -d "${TMPDIR:-/tmp}/bronchoroute-sign.XXXXXX")
trap 'rm -rf "$work"' EXIT

sign_file() {
  local target="$1"
  local mode="$2"
  local args
  args=(--sign "$IDENTITY" --force)
  if [[ "$adhoc" -eq 0 ]]; then
    args+=(--timestamp)
  fi
  case "$mode" in
    lib) ;;
    python-exec)
      args+=(--options runtime --entitlements "$PYTHON_ENTS")
      ;;
    app-exec)
      args+=(--options runtime --entitlements "$APP_ENTS")
      ;;
    framework)
      args+=(--options runtime)
      ;;
    runtime-exec)
      # Bundled helpers (ffmpeg): hardened runtime, no entitlements unless a run needs one.
      args+=(--options runtime)
      ;;
    *)
      echo "Unknown sign mode: $mode" >&2
      exit 1
      ;;
  esac
  codesign "${args[@]}" "$target"
}

sort_deepest() {
  local src="$1" dest="$2"
  if [[ ! -s "$src" ]]; then
    : > "$dest"
    return 0
  fi
  awk '{ n = gsub(/\//, "/"); printf "%04d\t%s\n", n, $0 }' "$src" | sort -nr -k1,1 | cut -f2- > "$dest"
}

count_lines() {
  if [[ ! -s "$1" ]]; then
    echo 0
    return 0
  fi
  wc -l < "$1" | tr -d ' '
}

echo "Enumerating Mach-O files"
set +e
find "$APP" -type f -exec file {} + | grep 'Mach-O' > "$work/file_out"
pipe_status=("${PIPESTATUS[@]}")
set -e
find_status=${pipe_status[0]}
grep_status=${pipe_status[1]:-1}
if [[ "$find_status" -ne 0 ]]; then
  echo "find failed while enumerating $APP" >&2
  exit 1
fi
if [[ "$grep_status" -gt 1 ]]; then
  echo "Could not list Mach-O files under $APP" >&2
  exit 1
fi
if [[ ! -s "$work/file_out" ]]; then
  echo "No Mach-O files under $APP" >&2
  exit 1
fi

: > "$work/all_macho"
: > "$work/python_libs"
: > "$work/python_execs"
: > "$work/ffmpeg_execs"
: > "$work/other_libs"
: > "$work/other_execs"
: > "$work/bundle_mains"
python_root="$APP/Contents/Resources/python/"
ffmpeg_bin="$APP/Contents/Resources/ffmpeg/ffmpeg"

while IFS= read -r line; do
  [[ -n "$line" ]] || continue
  # Universal binaries add a second line per slice: "path (for architecture arm64):<tab>Mach-O …".
  case "$line" in
    *" (for architecture "*) continue ;;
  esac
  desc=${line#*: }
  path=${line%": $desc"}
  if [[ "$path" == "$line" || -z "$path" ]]; then
    echo "Could not parse file(1) output: $line" >&2
    exit 1
  fi
  [[ "$desc" == *Mach-O* ]] || continue
  if [[ ! -f "$path" ]]; then
    echo "Mach-O path does not exist: $path" >&2
    exit 1
  fi
  printf '%s\n' "$path" >> "$work/all_macho"
  is_exec=0
  if [[ "$desc" == *executable* && "$desc" != *shared\ library* ]]; then
    is_exec=1
  fi
  is_bundle_main=0
  case "$path" in
    *.app/Contents/MacOS/*)
      parent=${path%/*}
      name=${path##*/}
      if [[ "$parent" == *.app/Contents/MacOS && -f "${parent%/MacOS}/Info.plist" ]]; then
        exe=$(plutil -extract CFBundleExecutable raw -o - "${parent%/MacOS}/Info.plist" 2>/dev/null || true)
        if [[ -n "$exe" && "$name" == "$exe" ]]; then
          is_bundle_main=1
        elif [[ -z "$exe" ]]; then
          # Electron helpers omit CFBundleExecutable and keep a single binary here.
          # Signing that binary seals the helper, so it has to wait for the bundle.
          others=0
          for entry in "$parent"/*; do
            if [[ "$entry" != "$path" && -e "$entry" ]]; then
              others=1
              break
            fi
          done
          if [[ "$others" -eq 0 ]]; then
            is_bundle_main=1
          fi
        fi
      fi
      ;;
  esac
  if [[ "$is_bundle_main" -eq 1 ]]; then
    printf '%s\n' "$path" >> "$work/bundle_mains"
    continue
  fi
  if [[ "$path" == "$ffmpeg_bin" ]]; then
    printf '%s\n' "$path" >> "$work/ffmpeg_execs"
    continue
  fi
  if [[ "$path" == "$python_root"* ]]; then
    if [[ "$is_exec" -eq 1 ]]; then
      printf '%s\n' "$path" >> "$work/python_execs"
    else
      printf '%s\n' "$path" >> "$work/python_libs"
    fi
  elif [[ "$is_exec" -eq 1 ]]; then
    printf '%s\n' "$path" >> "$work/other_execs"
  else
    printf '%s\n' "$path" >> "$work/other_libs"
  fi
done < "$work/file_out"

if [[ -d "$python_root" ]]; then
  python_macho=$(grep -c -F "$python_root" "$work/all_macho" || true)
  if [[ "${python_macho:-0}" -eq 0 ]]; then
    echo "No Mach-O files under Contents/Resources/python" >&2
    exit 1
  fi
fi

# Inside out, no --deep. Deeper Mach-O files are signed before a shallower
# binary that might seal a bundle. Bundle main executables are sealed with
# the bundle itself, after nested code is already signed.
: > "$work/tagged"
tag_list() {
  local mode="$1"
  local src="$2"
  local target n
  if [[ ! -s "$src" ]]; then
    return 0
  fi
  while IFS= read -r target; do
    [[ -n "$target" ]] || continue
    n=${target//[^\/]/}
    printf '%04d\t%s\t%s\n' "${#n}" "$mode" "$target" >> "$work/tagged"
  done < "$src"
}
tag_list lib "$work/python_libs"
tag_list lib "$work/other_libs"
tag_list python-exec "$work/python_execs"
tag_list runtime-exec "$work/ffmpeg_execs"
tag_list app-exec "$work/other_execs"
while IFS=$'\t' read -r _depth mode target; do
  [[ -n "$target" ]] || continue
  printf 'sign %s\n' "$target"
  sign_file "$target" "$mode"
done < <(sort -nr -k1,1 "$work/tagged")

: > "$work/nested_bundles"
find "$APP" \( -name '*.app' -o -name '*.framework' -o -name '*.xpc' \) -print > "$work/bundles_raw"
while IFS= read -r bundle; do
  [[ -n "$bundle" && "$bundle" != "$APP" ]] || continue
  printf '%s\n' "$bundle" >> "$work/nested_bundles"
done < "$work/bundles_raw"
sort_deepest "$work/nested_bundles" "$work/nested_bundles.sorted"
while IFS= read -r bundle; do
  [[ -n "$bundle" ]] || continue
  printf 'sign %s\n' "$bundle"
  case "$bundle" in
    *.framework) sign_file "$bundle" framework ;;
    *) sign_file "$bundle" app-exec ;;
  esac
done < "$work/nested_bundles.sorted"
printf 'sign %s\n' "$APP"
sign_file "$APP" app-exec

python_lib_count=$(count_lines "$work/python_libs")
python_exec_count=$(count_lines "$work/python_execs")
ffmpeg_exec_count=$(count_lines "$work/ffmpeg_execs")
other_lib_count=$(count_lines "$work/other_libs")
other_exec_count=$(count_lines "$work/other_execs")
bundle_main_count=$(count_lines "$work/bundle_mains")
macho_total=$(count_lines "$work/all_macho")
classified=$((python_lib_count + python_exec_count + ffmpeg_exec_count + other_lib_count + other_exec_count + bundle_main_count))
if [[ "$classified" -ne "$macho_total" ]]; then
  echo "Internal error: classified ${classified} Mach-O files, found ${macho_total}." >&2
  exit 1
fi
if [[ -e "$ffmpeg_bin" && "$ffmpeg_exec_count" -ne 1 ]]; then
  echo "Expected one bundled ffmpeg Mach-O at $ffmpeg_bin." >&2
  exit 1
fi

echo "Verifying signatures"
codesign --verify --deep --strict -vvv "$APP"
while IFS= read -r target; do
  [[ -n "$target" ]] || continue
  codesign --verify --strict "$target"
done < "$work/all_macho"

assert_no_get_task_allow() {
  local target="$1"
  local ents
  if ! ents=$(codesign -d --entitlements :- "$target" 2>"$work/codesign.err"); then
    cat "$work/codesign.err" >&2
    echo "Could not read entitlements: $target" >&2
    exit 1
  fi
  if printf '%s\n' "$ents" | grep -q 'get-task-allow'; then
    echo "get-task-allow is present on $target" >&2
    exit 1
  fi
  if printf '%s\n' "$ents" | grep -q 'bplist00'; then
    echo "Entitlements are a binary plist on $target" >&2
    exit 1
  fi
}

assert_no_get_task_allow "$APP"
while IFS= read -r target; do
  [[ -n "$target" ]] || continue
  assert_no_get_task_allow "$target"
done < "$work/all_macho"

flags=$(codesign -d --verbose=2 "$APP" 2>&1 | grep 'flags=' || true)
case "$flags" in
  *runtime*) ;;
  *)
    echo "Hardened runtime is missing on $APP" >&2
    exit 1
    ;;
esac

python_bin="$APP/Contents/Resources/python/bin/python3"
if [[ -e "$python_bin" ]]; then
  python_flags=$(codesign -d --verbose=2 "$python_bin" 2>&1 | grep 'flags=' || true)
  case "$python_flags" in
    *runtime*) ;;
    *)
      echo "Hardened runtime is missing on $python_bin" >&2
      exit 1
      ;;
  esac
  python_ents=$(codesign -d --entitlements :- "$python_bin" 2>/dev/null || true)
  if printf '%s\n' "$python_ents" | grep -q 'allow-jit\|disable-library-validation\|get-task-allow'; then
    echo "Python executable has an entitlement it must not have." >&2
    exit 1
  fi
fi

if [[ -e "$ffmpeg_bin" ]]; then
  ffmpeg_flags=$(codesign -d --verbose=2 "$ffmpeg_bin" 2>&1 | grep 'flags=' || true)
  case "$ffmpeg_flags" in
    *runtime*) ;;
    *)
      echo "Hardened runtime is missing on $ffmpeg_bin" >&2
      exit 1
      ;;
  esac
  ffmpeg_ents=$(codesign -d --entitlements :- "$ffmpeg_bin" 2>/dev/null || true)
  if printf '%s\n' "$ffmpeg_ents" | grep -q 'allow-jit\|disable-library-validation\|get-task-allow'; then
    echo "ffmpeg has an entitlement it must not have." >&2
    exit 1
  fi
fi

app_ents=$(codesign -d --entitlements :- "$APP" 2>/dev/null || true)
if ! printf '%s\n' "$app_ents" | grep -q 'com.apple.security.cs.allow-jit'; then
  echo "App is missing com.apple.security.cs.allow-jit." >&2
  exit 1
fi
if printf '%s\n' "$app_ents" | grep -q 'disable-library-validation\|get-task-allow'; then
  echo "App has an entitlement it must not have." >&2
  exit 1
fi

dmg_path=""
notarization="skipped (ad-hoc identity)"
spctl_app=""
spctl_dmg=""
if [[ "$adhoc" -eq 0 ]]; then
  version=$(plutil -extract CFBundleShortVersionString raw -o - "$APP/Contents/Info.plist")
  case "$version" in
    ""|*[!A-Za-z0-9._-]*)
      echo "Unexpected CFBundleShortVersionString: ${version:-<empty>}" >&2
      exit 1
      ;;
  esac
  dmg_path="$(dirname "$APP")/BronchoRoute-${version}-arm64.dmg"
  stage="$work/dmg-root"
  mkdir -p "$stage"
  ditto "$APP" "$stage/$(basename "$APP")"
  ln -s /Applications "$stage/Applications"
  rm -f "$dmg_path"
  hdiutil create -volname "BronchoRoute" -srcfolder "$stage" -ov -format UDZO "$dmg_path"
  hdiutil verify "$dmg_path"
  codesign --sign "$IDENTITY" --timestamp --force "$dmg_path"
  codesign --verify --verbose=2 "$dmg_path"
  if [[ -z "${NOTARY_PROFILE:-}" ]]; then
    notarization="not started (NOTARY_PROFILE is unset)"
  else
    notarize_out=$(xcrun notarytool submit "$dmg_path" --keychain-profile "$NOTARY_PROFILE" --wait 2>&1) || {
      printf '%s\n' "$notarize_out" | grep -vi 'password' >&2
      exit 1
    }
    printf '%s\n' "$notarize_out" | grep -vi 'password' | grep -E 'status:|id:' || true
    if ! printf '%s\n' "$notarize_out" | grep -q 'status: Accepted'; then
      echo "Notarization did not report status: Accepted" >&2
      printf '%s\n' "$notarize_out" | grep -vi 'password' >&2
      exit 1
    fi
    xcrun stapler staple "$APP"
    xcrun stapler staple "$dmg_path"
    xcrun stapler validate "$APP"
    xcrun stapler validate "$dmg_path"
    if ! spctl_app=$(spctl -a -t exec -vv "$APP" 2>&1); then
      printf '%s\n' "$spctl_app" >&2
      exit 1
    fi
    if ! spctl_dmg=$(spctl -a -t open --context context:primary-signature -vv "$dmg_path" 2>&1); then
      printf '%s\n' "$spctl_dmg" >&2
      exit 1
    fi
    notarization="accepted"
  fi
fi

echo "BronchoRoute signing report"
if [[ "$adhoc" -eq 1 ]]; then
  echo "identity: ad-hoc"
else
  echo "identity: ${IDENTITY}"
fi
echo "Mach-O files signed: ${macho_total}"
echo "  python libraries: ${python_lib_count}"
echo "  python executables: ${python_exec_count}"
echo "  ffmpeg executables: ${ffmpeg_exec_count}"
echo "  other libraries: ${other_lib_count}"
echo "  other executables: ${other_exec_count}"
echo "  bundle main executables: ${bundle_main_count}"
echo "codesign --verify --deep --strict: passed"
echo "get-task-allow: absent"
echo "notarization: ${notarization}"
if [[ -n "$dmg_path" ]]; then
  echo "dmg: ${dmg_path}"
fi
if [[ -n "$spctl_app" ]]; then
  echo "spctl app:"
  printf '%s\n' "$spctl_app"
fi
if [[ -n "$spctl_dmg" ]]; then
  echo "spctl dmg:"
  printf '%s\n' "$spctl_dmg"
fi

if [[ "$notarization" == "not started (NOTARY_PROFILE is unset)" ]]; then
  echo "NOTARY_PROFILE is not set. The app and DMG are signed and verified, and notarization was not started." >&2
  echo "Create a keychain profile, then re-run: NOTARY_PROFILE=<profile> npm run sign" >&2
  echo "  xcrun notarytool store-credentials <profile> --apple-id <email> --team-id ${TEAM_ID}" >&2
  exit 1
fi
