"""Application facade for creating and managing simulation runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..runtime.manager import SimulationManager


class SimulationService:
    def __init__(self, artifacts_root: Path) -> None:
        self.manager = SimulationManager(artifacts_root)

    def create(self, **kwargs: Any) -> Path:
        return self.manager.create(**kwargs)

    def start(self, run_dir: Path, *, foreground: bool = False) -> dict[str, Any]:
        return self.manager.start(run_dir, foreground=foreground)

    def status(self, run_dir: Path) -> dict[str, Any]:
        return self.manager.status(run_dir)

    def stop(self, run_dir: Path) -> dict[str, Any]:
        return self.manager.stop(run_dir)
