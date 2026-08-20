"""Terminal subprocess entry point for one simulation run."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from .engine import SimulationEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one EventSimulation world")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    asyncio.run(SimulationEngine(args.run_dir).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
