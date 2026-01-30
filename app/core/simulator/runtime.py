"""Public entrypoint for the in-process simulator runtime.

The implementation lives in `runtime_facade.py`.
This module stays intentionally small to keep imports stable.
"""

from .runtime_facade import SimulatorRuntime, runtime

__all__ = ["SimulatorRuntime", "runtime"]
