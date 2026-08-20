"""Twitter-like multi-agent simulation runtime.

The runtime structure is adapted from MiroFish's simulation workflow and is
distributed under AGPL-3.0.  EventSimulation keeps its own evidence boundary:
Persona initialization is grounded, while generated interactions are labeled
as simulation output.
"""

from .config import SimulationConfig, build_simulation_config
from .engine import SimulationEngine

__all__ = ["SimulationConfig", "SimulationEngine", "build_simulation_config"]
