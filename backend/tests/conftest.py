"""
Stub heavy ML/AI dependencies so pure-logic tests run in CI without
requiring torch, transformers, or GPU drivers.

Only stubs packages that are not already installed; real packages
take priority when present.
"""
import sys
from unittest.mock import MagicMock

_HEAVY_DEPS = [
    "torch",
    "torch.nn",
    "torch.nn.functional",
    "torch.cuda",
    "torch.backends",
    "torch.backends.mps",
    "torchaudio",
    "transformers",
    "transformers.models",
    "mlx",
    "mlx.core",
    "mlx.nn",
    "numba",
    "intel_extension_for_pytorch",
    "torch_directml",
]

for _dep in _HEAVY_DEPS:
    if _dep not in sys.modules:
        try:
            __import__(_dep)
        except (ImportError, ModuleNotFoundError):
            sys.modules[_dep] = MagicMock()
