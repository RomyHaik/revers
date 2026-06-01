"""Probing strategies package -- auto-discovers strategy classes from this folder.

To add a new strategy:
1. Create a new .py file in this folder
2. Define a class that inherits from ProbeStrategy
3. Set the required class attributes: name, description, phases
4. Implement generate_probe()
5. Optionally set forceful=True, param_space, and override canonical_param_key()

The strategy will be automatically discovered and registered.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import pkgutil
from abc import ABC, abstractmethod
from typing import Optional

from reversegent.knowledge import KnowledgeState

log = logging.getLogger(__name__)


class ProbeStrategy(ABC):
    """Base class for all probing strategies.

    Class attributes (set these in your subclass):
        name:        Unique snake_case identifier (e.g. "tool_discovery").
        description: One-line human-readable description shown to the planner.
        phases:      List of phases where this strategy is relevant.
                     Valid values: "early", "middle", "late", "verification".
        forceful:    If True, only included when running in forceful mode.
        param_space: Dict mapping parameter names to lists of valid options.
                     Used by the planner for deterministic fallback probing.
    """

    name: str
    description: str
    phases: list[str]  # e.g. ["early"] or ["middle", "late"]
    forceful: bool = False
    behavioral_observation: bool = False

    # Parameter space for deterministic fallback probing.
    # Override in subclass: {"param_name": ["option1", "option2", ...]}
    param_space: dict[str, list] = {}

    @abstractmethod
    def generate_probe(
        self,
        knowledge: KnowledgeState,
        parameters: Optional[dict] = None,
    ) -> list[dict]:
        """Return a list of chat messages to send to the target."""
        ...

    def canonical_param_key(self, params: dict) -> str:
        """Build a canonical string key from parameters for deduplication.

        Override in subclass for strategy-specific key extraction.
        Default: JSON-serialized params truncated to 120 chars.
        """
        return json.dumps(params, sort_keys=True)[:120]


# ── Auto-discovery ────────────────────────────────────────────────────


def _discover_strategies() -> (
    tuple[dict[str, type[ProbeStrategy]], dict[str, type[ProbeStrategy]]]
):
    """Scan this package for ProbeStrategy subclasses and build registries."""
    standard: dict[str, type[ProbeStrategy]] = {}
    forceful: dict[str, type[ProbeStrategy]] = {}

    # Import all modules in this package
    for _importer, module_name, _is_pkg in pkgutil.iter_modules(__path__):
        if module_name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f".{module_name}", __package__)
        except Exception:
            log.warning("Failed to import strategy module: %s", module_name, exc_info=True)
            continue

        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                inspect.isclass(obj)
                and issubclass(obj, ProbeStrategy)
                and obj is not ProbeStrategy
                and hasattr(obj, "name")
                and isinstance(obj.name, str)
            ):
                if getattr(obj, "forceful", False):
                    forceful[obj.name] = obj
                else:
                    standard[obj.name] = obj

    log.debug(
        "Discovered %d standard + %d forceful strategies",
        len(standard),
        len(forceful),
    )
    return standard, forceful


STRATEGY_REGISTRY, FORCEFUL_STRATEGY_REGISTRY = _discover_strategies()

__all__ = [
    "ProbeStrategy",
    "STRATEGY_REGISTRY",
    "FORCEFUL_STRATEGY_REGISTRY",
]
