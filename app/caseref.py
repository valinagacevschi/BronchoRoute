# Changes: Phase 4b — CaseRef.manual for airway_manual.nii.gz.
"""Typed case id reference: local-NNN or AeroPath n, with resolved artifact paths.

Phase 3: bridges path for airway_bridges.nii.gz. Phase 4b: airway_manual.nii.gz.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import paths

CASE_ID_RE = re.compile(r"^(local-\d{3,}|[1-9]\d*)$")

CaseKind = Literal["local", "aeropath"]


class UnknownCase(LookupError):
    """Case id is syntactically invalid or does not exist on disk."""


@dataclass(frozen=True)
class CaseRef:
    case_id: str
    kind: CaseKind

    @classmethod
    def parse(cls, case_id: str) -> CaseRef:
        if not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id):
            raise UnknownCase(f"invalid case id: {case_id!r}")
        kind: CaseKind = "local" if case_id.startswith("local-") else "aeropath"
        ref = cls(case_id=case_id, kind=kind)
        if not ref.exists():
            raise UnknownCase(f"unknown case: {case_id}")
        return ref

    def exists(self) -> bool:
        if self.kind == "local":
            return (paths.LOCAL_CASES / self.case_id / "case.json").is_file()
        return (paths.AEROPATH / self.case_id).is_dir()

    @property
    def source_label(self) -> str:
        if self.kind == "aeropath":
            return f"AeroPath {self.case_id}"
        return self.case_id

    @property
    def case_json(self) -> Path:
        if self.kind == "local":
            return paths.LOCAL_CASES / self.case_id / "case.json"
        return paths.OUT / "cases" / self.case_id / "case.json"

    @property
    def ct(self) -> Path:
        if self.kind == "local":
            return paths.LOCAL_CASES / self.case_id / f"{self.case_id}_CT.nii.gz"
        return paths.AEROPATH / self.case_id / f"{self.case_id}_CT_HR.nii.gz"

    @property
    def ts_out(self) -> Path:
        if self.kind == "local":
            return paths.TS_OUT / self.case_id
        return paths.TS_OUT / f"fallback_{self.case_id}"

    @property
    def lobes(self) -> Path:
        return self.ts_out / "lobes.nii.gz"

    @property
    def trachea(self) -> Path:
        return self.ts_out / "lobes" / "trachea.nii.gz"

    @property
    def airways(self) -> Path:
        return self.ts_out / "airways.nii.gz"

    @property
    def bridges(self) -> Path:
        return self.ts_out / "airway_bridges.nii.gz"

    @property
    def manual(self) -> Path:
        return self.ts_out / "airway_manual.nii.gz"

    @property
    def crop_cache_dir(self) -> Path:
        return paths.TS_OUT / self.case_id

    def graph_path(self) -> Path | None:
        if self.kind == "local":
            candidates = [
                paths.OUT / self.case_id / f"{self.case_id}_graph.json",
                paths.OUT / f"{self.case_id}_graph.json",
            ]
        else:
            candidates = [
                paths.OUT / "fallback" / f"{self.case_id}_graph.json",
                paths.OUT / f"{self.case_id}_graph.json",
                paths.PIPELINE / "cases" / f"{self.case_id}_graph.json",
            ]
        for path in candidates:
            if path.is_file():
                return path
        return None
