"""Public entrypoint for the in-process simulator runtime.

The implementation lives in `runtime_impl.py`.
This module stays intentionally small to keep imports stable.
"""

from .runtime_impl import SimulatorRuntime, runtime

# NOTE:
# Unit tests (and potentially external callers) rely on importing
# `_map_rejection_code` from this stable module path.
# The implementation currently lives in `real_runner.map_rejection_code`.
from .real_runner import map_rejection_code as _map_rejection_code

__all__ = ["SimulatorRuntime", "runtime", "_map_rejection_code"]
