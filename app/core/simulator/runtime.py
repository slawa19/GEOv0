"""Public entrypoint for the in-process simulator runtime.

The implementation lives in `runtime_impl.py`.
This module stays intentionally small to keep imports stable.
"""

from .runtime_impl import SimulatorRuntime, runtime

__all__ = ["SimulatorRuntime", "runtime"]
