"""Compatibility facade.

This module remains import-stable, but the implementation is in `runtime_impl.py`.
"""

from .runtime_impl import SimulatorRuntime, runtime

__all__ = ["SimulatorRuntime", "runtime"]
