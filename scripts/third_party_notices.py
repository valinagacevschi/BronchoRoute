# Changes: credit the bundled LGPL ffmpeg 8.1.3 binary and source offer.
# Changes: generate THIRD_PARTY_NOTICES.md from the uv lock and installed metadata.
"""Runtime third-party notices for BronchoRoute.

The dev dependency group is not walked. Environment markers are evaluated for
this interpreter: the desktop app ships on macOS, so Linux-only CUDA packages
are not runtime dependencies here.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import re
import tomllib
from pathlib import Path

from packaging.markers import Marker
from termcolor import cprint

REPO = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = REPO / "pipeline" / "uv.lock"
DEFAULT_OUTPUT = REPO / "THIRD_PARTY_NOTICES.md"

# GPL-3.0-compatible SPDX ids (FSF licence list, GPL-3.0 side).
_COMPATIBLE = frozenset({
    "MIT", "MIT-0", "MIT-CMU", "X11",
    "BSD-2-Clause", "BSD-3-Clause",
    "0BSD", "Apache-2.0", "ISC", "PSF-2.0", "Python-2.0",
    "HPND", "Zlib", "BSL-1.0", "Unlicense", "CC0-1.0",
    "LGPL-2.1", "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "GPL-2.0-or-later",
    "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later", "GPL-3.0+",
    "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "MPL-2.0", "OFL-1.1",
    "MIT-Modern-Variant", "BSD-3-Clause-Open-MPI",
})
_INCOMPATIBLE = frozenset({
    "GPL-2.0", "GPL-2.0-only",
    "BSD-4-Clause", "BSD-4-Clause-UC",
    "Apache-1.0", "Apache-1.1",
    "MPL-1.0", "MPL-1.1",
    "EPL-1.0", "EPL-2.0",
    "OSL-1.0", "OSL-2.0", "OSL-3.0",
})
# Extra permissions, not extra restrictions. GCC's runtime exception is one.
_EXCEPTIONS = frozenset({"LLVM-exception", "GCC-exception-3.1"})
_AMBIGUOUS = frozenset({
    "bsd", "bsd license", "apache", "apache license", "dual license", "unknown",
})
_CLASSIFIER_MAP = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)":
        "GPL-3.0-or-later",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)":
        "LGPL-3.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)":
        "LGPL-3.0-only",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)":
        "LGPL-2.1-or-later",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: zlib/libpng License": "Zlib",
    "License :: OSI Approved :: Boost Software License 1.0 (BSL-1.0)": "BSL-1.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
}
_ALIASES = {
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license (mit)": "MIT",
    "apache-2.0": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-3 clause": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "isc": "ISC",
    "isc license": "ISC",
    "mpl-2.0": "MPL-2.0",
    "psf-2.0": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "lgpl-3.0": "LGPL-3.0",
    "lgpl-3.0-only": "LGPL-3.0-only",
    "lgpl-3.0-or-later": "LGPL-3.0-or-later",
    "gpl-3.0": "GPL-3.0",
    "gpl-3.0-only": "GPL-3.0-only",
    "gpl-3.0-or-later": "GPL-3.0-or-later",
    "gpl-3.0+": "GPL-3.0+",
    "ofl-1.1": "OFL-1.1",
    "mit-cmu": "MIT-CMU",
}
# Not Python packages: credited by hand, still emitted by this script.
_MATERIALS = (
    (
        "TotalSegmentator model weights",
        "Apache-2.0",
        "tasks lung_vessels and total — https://github.com/wasserth/TotalSegmentator",
    ),
    (
        "BBT 3D Slicer workflow (T. Toma)",
        "Apache-2.0",
        "https://doi.org/10.5281/zenodo.23190857",
    ),
    (
        "IBM Plex",
        "OFL-1.1",
        "https://github.com/IBM/plex",
    ),
    (
        "FFmpeg 8.1.3 (LGPL build)",
        "LGPL-2.1-or-later",
        "Bundled at Contents/Resources/ffmpeg/ffmpeg. Source tarball "
        "https://ffmpeg.org/releases/ffmpeg-8.1.3.tar.xz "
        "(sha256 7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3). "
        "Written offer: that tarball is the complete corresponding source for "
        "this binary. Configure: --disable-everything --disable-network "
        "--disable-autodetect --disable-doc --disable-ffplay --disable-ffprobe "
        "--disable-gpl --disable-nonfree --disable-shared --enable-static "
        "--enable-videotoolbox --enable-decoder=mjpeg --enable-parser=mjpeg "
        "--enable-demuxer=image2pipe --enable-demuxer=mov --enable-muxer=mp4 "
        "--enable-encoder=h264_videotoolbox --enable-filter=scale "
        "--enable-filter=format --enable-protocol=file --enable-protocol=pipe "
        "--enable-protocol=fd (see desktop/scripts/build-ffmpeg.sh).",
    ),
)
_TOKEN = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9.+-]*|\(|\))\s*")
_BULLET = re.compile(r"^(?:\d+\.|[a-z]\.|[*•-])\s+\S")


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def _normalize_label(text: str) -> str:
    stripped = _strip_quotes(text)
    if any(op in f" {stripped} " for op in (" AND ", " OR ", " WITH ")):
        return stripped
    key = re.sub(r"\s+", " ", stripped).strip()
    if key.lower().startswith("https://opensource.org/licenses/"):
        return _normalize_label(key.rsplit("/", 1)[-1])
    return _ALIASES.get(key.lower(), key)


def _tokenize(expr: str) -> list[str]:
    pos = 0
    tokens: list[str] = []
    while pos < len(expr):
        if expr[pos].isspace():
            pos += 1
            continue
        match = _TOKEN.match(expr, pos)
        if match is None:
            raise ValueError(expr[pos:pos + 24])
        tokens.append(match.group(1))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._index = 0

    def _peek(self) -> str | None:
        if self._index >= len(self._tokens):
            return None
        return self._tokens[self._index]

    def _pop(self) -> str | None:
        token = self._peek()
        self._index += 1
        return token

    def parse(self) -> tuple:
        node = self._parse_or()
        if self._peek() is not None:
            raise ValueError("trailing")
        return node

    def _parse_or(self) -> tuple:
        parts = [self._parse_and()]
        while self._peek() == "OR":
            self._pop()
            parts.append(self._parse_and())
        if len(parts) == 1:
            return parts[0]
        return ("OR", parts)

    def _parse_and(self) -> tuple:
        parts = [self._parse_with()]
        while self._peek() == "AND":
            self._pop()
            parts.append(self._parse_with())
        if len(parts) == 1:
            return parts[0]
        return ("AND", parts)

    def _parse_with(self) -> tuple:
        node = self._parse_primary()
        exceptions: list[str] = []
        while self._peek() == "WITH":
            self._pop()
            exc = self._pop()
            if exc in {None, "AND", "OR", "WITH", "(", ")"}:
                raise ValueError("WITH")
            exceptions.append(exc)
        if not exceptions:
            return node
        return ("WITH", node, exceptions)

    def _parse_primary(self) -> tuple:
        token = self._pop()
        if token == "(":
            node = self._parse_or()
            if self._pop() != ")":
                raise ValueError("paren")
            return node
        if token in {None, "AND", "OR", "WITH", ")"}:
            raise ValueError("id")
        return ("ID", token)


def _evaluate(node: tuple) -> str:
    kind = node[0]
    if kind == "ID":
        ident = node[1]
        if ident in _COMPATIBLE:
            return "yes"
        if ident in _INCOMPATIBLE:
            return "no"
        return "unknown"
    if kind == "OR":
        results = [_evaluate(part) for part in node[1]]
        if "yes" in results:
            return "yes"
        if "unknown" in results:
            return "unknown"
        return "no"
    if kind == "AND":
        results = [_evaluate(part) for part in node[1]]
        if "no" in results:
            return "no"
        if "unknown" in results:
            return "unknown"
        return "yes"
    base = _evaluate(node[1])
    if base == "no":
        return "no"
    if any(exc not in _EXCEPTIONS for exc in node[2]):
        return "unknown"
    return base


def _unknown_ids(node: tuple) -> set[str]:
    kind = node[0]
    if kind == "ID":
        if node[1] in _COMPATIBLE or node[1] in _INCOMPATIBLE:
            return set()
        return {node[1]}
    if kind == "WITH":
        found = _unknown_ids(node[1])
        found.update(exc for exc in node[2] if exc not in _EXCEPTIONS)
        return found
    found: set[str] = set()
    for part in node[1]:
        found.update(_unknown_ids(part))
    return found


def compatibility(expr: str) -> tuple[str, str]:
    """Return ('ok', '') or ('review', reason) for a SPDX expression."""
    try:
        node = _Parser(_tokenize(expr)).parse()
    except ValueError:
        return ("review", "could not parse licence expression")
    result = _evaluate(node)
    if result == "yes":
        return ("ok", "")
    if result == "no":
        return ("review", "not GPL-3.0-compatible")
    names = ", ".join(sorted(_unknown_ids(node)))
    return ("review", f"unknown licence: {names}")


def _recognize_bsd(text: str) -> str | None:
    low = text.lower()
    looks_bsd = (
        "redistribution and use in source and binary forms" in low
        or "bsd" in low
    )
    if not looks_bsd:
        return None
    # A title ("BSD 3-Clause") outranks a quoted advertising sentence.
    if any(phrase in low for phrase in (
        "bsd 3-clause", "bsd-3-clause", "modified bsd", "revised bsd", "new bsd",
    )):
        return "BSD-3-Clause"
    if any(phrase in low for phrase in ("bsd 2-clause", "bsd-2-clause", "simplified bsd")):
        return "BSD-2-Clause"
    if "all advertising materials" in low:
        return "BSD-4-Clause"
    conditions = []
    for line in text.splitlines():
        stripped = line.strip()
        if not _BULLET.match(stripped):
            continue
        folded = stripped.lower()
        if any(word in folded for word in (
            "redistribut", "neither", "endors", "copyright notice", "contributors",
        )):
            conditions.append(folded)
    if len(conditions) == 3:
        return "BSD-3-Clause"
    if len(conditions) == 2:
        return "BSD-2-Clause"
    if len(conditions) >= 4:
        return "BSD-4-Clause"
    return None


def _is_spdx_expr(text: str) -> bool:
    try:
        _Parser(_tokenize(text)).parse()
    except ValueError:
        return False
    return True


def _is_debian_copyright(text: str) -> bool:
    """A single embedded `License:` line is not a machine-readable copyright."""
    licence_lines = 0
    files_lines = 0
    for line in text.splitlines():
        if re.match(r"(?i)^license:", line):
            licence_lines += 1
        elif re.match(r"(?i)^files:", line):
            files_lines += 1
    return licence_lines >= 2 or (licence_lines >= 1 and files_lines >= 1)


def _licence_chunks(text: str) -> list[str]:
    parts = re.split(r"(?m)^-{10,}\s*$", text)
    chunks = [part.strip() for part in parts if part.strip()]
    return chunks or [text]


def _debian_expressions(text: str) -> list[str] | None:
    """Full `License:` values. A bare first token would drop `OR` alternatives."""
    lines = text.splitlines()
    exprs: list[str] = []
    saw = False
    for index, line in enumerate(lines):
        match = re.match(r"(?i)^license:[ \t]*(.*)$", line)
        if match is None:
            continue
        saw = True
        expr = match.group(1).strip()
        if not expr and index + 1 < len(lines):
            nxt = lines[index + 1].strip()
            # A following sentence is a description, not an SPDX id.
            if (
                nxt
                and len(nxt) < 80
                and not nxt.endswith(".")
                and not re.match(r"(?i)^(files|copyright|name|description|license):", nxt)
            ):
                expr = nxt
        if expr and expr not in exprs:
            exprs.append(expr)
    if not saw or not exprs:
        return None
    return exprs


def _combine_labels(labels: list[str]) -> tuple[str, str]:
    display: list[str] = []
    reasons: list[str] = []
    for label in labels:
        if _is_spdx_expr(label):
            normalized, reason = _finish(label)
        else:
            normalized = label
            reason = f"unknown licence: {label}"
        if normalized not in display:
            display.append(normalized)
        if reason and reason not in reasons:
            reasons.append(reason)
    shown = " AND ".join(sorted(display))
    return shown, "; ".join(reasons)


def _recognize_one(text: str) -> str | None:
    head = text[:800].lower()
    if "apache license" in head and "version 2.0" in head:
        return "Apache-2.0"
    if "mit-cmu" in head:
        return "MIT-CMU"
    if "permission is hereby granted, free of charge" in head or "the mit license" in head:
        return "MIT"
    bsd = _recognize_bsd(text)
    if bsd:
        return bsd
    if "gnu lesser general public license" in head and "version 3" in head:
        return "LGPL-3.0-only"
    if "gnu general public license" in head and "version 3" in head:
        return "GPL-3.0-only"
    if "python software foundation license" in head:
        return "PSF-2.0"
    return None


def _primary_label(text: str) -> str | None:
    """First recognised licence in a file. Appendices are not the package licence."""
    for chunk in _licence_chunks(text):
        label = _recognize_one(chunk)
        if label:
            return label
    return None


def _recognize_text(text: str) -> tuple[str, str] | None:
    """Return (label, review reason). None if the text is not a licence."""
    if not text.strip():
        return None
    if _is_debian_copyright(text):
        debian = _debian_expressions(text)
        if debian:
            return _combine_labels(debian)
    label = _primary_label(text)
    if label is None:
        return None
    return _finish(label)


def _licence_files(dist: importlib.metadata.Distribution) -> list[Path]:
    """Package-level licence files. Nested vendored copies are not the package licence."""
    found: list[Path] = []
    for entry in dist.files or []:
        text = str(entry)
        if ".dist-info/" not in text:
            continue
        name = Path(text).name.lower()
        if "license" not in name and not name.startswith(("copying", "licence")):
            continue
        relative = text.split(".dist-info/", 1)[1]
        parts = Path(relative).parts
        package_level = len(parts) == 1 or (len(parts) == 2 and parts[0].lower() == "licenses")
        if package_level:
            found.append(Path(dist.locate_file(entry)))
    found.sort(key=lambda path: path.name.lower())
    return found


def _licence_from_files(dist: importlib.metadata.Distribution) -> tuple[str, str] | None:
    # A machine-readable copyright is the inventory. Otherwise the first
    # recognised block is the package licence; later blocks are vendored copies.
    for path in _licence_files(dist):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        recognized = _recognize_text(body)
        if recognized is not None:
            return recognized
    return None


def _classifiers(meta: importlib.metadata.PackageMetadata) -> tuple[list[str], bool]:
    mapped: list[str] = []
    ambiguous_bsd = False
    for classifier in meta.get_all("Classifier") or []:
        if classifier == "License :: OSI Approved :: BSD License":
            ambiguous_bsd = True
            continue
        spdx = _CLASSIFIER_MAP.get(classifier)
        if spdx and spdx not in mapped:
            mapped.append(spdx)
    return mapped, ambiguous_bsd


def _finish(label: str) -> tuple[str, str]:
    normalized = _normalize_label(label)
    _status, reason = compatibility(normalized)
    if reason:
        return normalized, reason
    return normalized, ""


def resolve_licence(
    meta: importlib.metadata.PackageMetadata,
    dist: importlib.metadata.Distribution,
) -> tuple[str, str]:
    """Return (licence label, review reason). An empty reason means compatible."""
    expr = _strip_quotes(meta.get("License-Expression") or "")
    if expr:
        return _finish(expr)
    raw = (meta.get("License") or "").strip()
    long_text = raw if ("\n" in raw or len(raw) > 160) else ""
    short = ""
    if raw and not long_text and raw.upper() != "UNKNOWN":
        short = _strip_quotes(raw)
    mapped, ambiguous_bsd = _classifiers(meta)
    if short and short in _CLASSIFIER_MAP:
        return _finish(_CLASSIFIER_MAP[short])
    if short:
        normalized = _normalize_label(short)
        if normalized.lower() not in _AMBIGUOUS:
            return _finish(normalized)
    recognized = _licence_from_files(dist)
    if recognized is None and long_text:
        recognized = _recognize_text(long_text)
    if recognized:
        return recognized
    if short and short.lower() == "dual license" and (mapped or ambiguous_bsd):
        parts = list(mapped)
        if ambiguous_bsd:
            parts.append("BSD")
        return _finish(" OR ".join(parts))
    if mapped and ambiguous_bsd:
        return _finish(" OR ".join([*mapped, "BSD"]))
    if mapped:
        return _finish(" OR ".join(mapped))
    if ambiguous_bsd:
        return ("BSD", "BSD clause count is not in the installed metadata")
    return ("unknown", "no licence in the installed metadata")


def _project_url(meta: importlib.metadata.PackageMetadata) -> str:
    labelled: list[tuple[str, str]] = []
    for item in meta.get_all("Project-URL") or []:
        if "," not in item:
            labelled.append(("", item.strip()))
            continue
        label, url = item.split(",", 1)
        labelled.append((label.strip().lower(), url.strip()))
    for prefer in ("homepage", "home", "source", "repository", "source code"):
        for label, url in labelled:
            if label == prefer and url:
                return url
    for _label, url in labelled:
        if url:
            return url
    home = (meta.get("Home-page") or "").strip()
    return home or "unknown"


def _load_packages(lock_path: Path) -> dict[str, dict]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages: dict[str, dict] = {}
    for package in data["package"]:
        name = package["name"]
        if name in packages:
            raise SystemExit(f"uv.lock has two versions of {name}")
        packages[name] = package
    return packages


def _root_package(packages: dict[str, dict]) -> dict:
    roots = [
        package for package in packages.values()
        if isinstance(package.get("source"), dict) and "virtual" in package["source"]
    ]
    if len(roots) != 1:
        raise SystemExit(f"expected one virtual root in the lock, found {len(roots)}")
    return roots[0]


def _marker_matches(marker: str | None) -> bool:
    if not marker:
        return True
    return Marker(marker).evaluate()


def runtime_names(packages: dict[str, dict], root: dict) -> list[str]:
    """Transitive runtime deps. Extras are followed; the dev group is not."""
    queue: list[tuple[str, tuple[str, ...]]] = []
    for dep in root.get("dependencies") or []:
        if _marker_matches(dep.get("marker")):
            extras = tuple(sorted(dep.get("extra") or []))
            queue.append((dep["name"], extras))
    seen: set[tuple[str, tuple[str, ...]]] = set()
    names: set[str] = set()
    while queue:
        name, extras = queue.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        names.add(name)
        package = packages.get(name)
        if package is None:
            continue
        deps = list(package.get("dependencies") or [])
        optional = package.get("optional-dependencies") or {}
        for extra in extras:
            deps.extend(optional.get(extra) or [])
        for dep in deps:
            if not _marker_matches(dep.get("marker")):
                continue
            dep_extras = tuple(sorted(dep.get("extra") or []))
            queue.append((dep["name"], dep_extras))
    return sorted(names, key=str.lower)


def _package_line(name: str, version: str, licence: str, url: str) -> str:
    return f"- {name} {version} — {licence} — {url}"


def generate(lock_path: Path) -> str:
    packages = _load_packages(lock_path)
    root = _root_package(packages)
    package_lines: list[str] = []
    review: list[str] = []
    for name in runtime_names(packages, root):
        package = packages.get(name)
        version = package.get("version", "unknown") if package else "unknown"
        try:
            dist = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            dist = None
        if dist is None:
            licence = "unknown"
            url = "unknown"
            reason = "not installed"
        else:
            meta = dist.metadata
            licence, reason = resolve_licence(meta, dist)
            url = _project_url(meta)
            installed = (meta.get("Version") or "").strip()
            if installed and installed != version:
                mismatch = f"installed {installed} != lock {version}"
                reason = f"{reason}; {mismatch}" if reason else mismatch
        line = _package_line(name, version, licence, url)
        package_lines.append(line)
        if reason:
            review.append(f"{line} — {reason}")
    material_lines: list[str] = []
    for name, licence, detail in _MATERIALS:
        line = f"- {name} — {licence} — {detail}"
        material_lines.append(line)
        _status, reason = compatibility(licence)
        if reason:
            review.append(f"{line} — {reason}")
    review_body = "\n".join(review) if review else "None."
    parts = [
        "# Third-party notices",
        "",
        "Generated by `scripts/third_party_notices.py` from `pipeline/uv.lock`",
        "and installed package metadata. Runtime dependencies only (the dev",
        "group is excluded). Markers follow the interpreter that runs the",
        "script. Do not edit by hand.",
        "",
        "## Python packages",
        "",
        *package_lines,
        "",
        "## Other materials",
        "",
        *material_lines,
        "",
        "## Needs human review",
        "",
        review_body,
        "",
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write THIRD_PARTY_NOTICES.md")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    text = generate(args.lock)
    args.output.write_text(text, encoding="utf-8")
    review = text.split("## Needs human review", 1)[1].strip()
    count = text.count("\n- ")
    cprint(
        f"third-party notices: {count} entries, review {len(review.splitlines())} lines → {args.output}",
        "cyan",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
