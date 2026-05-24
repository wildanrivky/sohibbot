"""Compatibility shim.

Beberapa tempat di repo (dokumen, tests, dan caller lama) mengimpor
`el_solver.memory` sebagai module dari file `el_solver/memory.py`.

Sekarang implementasi sebenarnya ada di package `el_solver.memory`
(folder `el_solver/memory/__init__.py`). Agar backward-compatibility dan
tanpa mengubah implementasi, kita re-export semua simbol dari package
di sini.

File ini tidak mengubah fungsi logika, hanya bridge nama modul.
"""
from __future__ import annotations

# Import the package implementation and re-export its public API.
# This keeps old imports working (from el_solver import memory or
# import el_solver.memory) while the real code lives in the package.
try:
    # Import package (this will execute el_solver/memory/__init__.py)
    from . import memory as _memory_pkg
except Exception:  # pragma: no cover - defensive, should not happen
    # If package import fails, raise a clear error so the runtime can surface it.
    raise

# Re-export public names if defined, else fallback to module dict.
try:
    __all__ = list(getattr(_memory_pkg, "__all__"))
except Exception:
    __all__ = [k for k in dir(_memory_pkg) if not k.startswith("_")]

globals().update({name: getattr(_memory_pkg, name) for name in __all__})

# Also keep a reference to the package for callers that inspect module attrs.
_package = _memory_pkg
