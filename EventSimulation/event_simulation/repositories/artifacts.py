"""Filesystem layout and atomic JSON persistence for case artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ArtifactRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def case_root(self, case_id: str) -> Path:
        return self.root / "cases" / case_id

    def read_json(self, path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"expected JSON object: {path}")
        return payload

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

