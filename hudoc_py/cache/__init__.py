"""Local on-disk cache helpers (Parquet / JSONL).

The cache root is ``$ECHR_PY_DATA_DIR`` or ``~/.echr-py/data/`` by default.
``$HUDOC_PY_DATA_DIR`` and an existing ``~/.hudoc-py/data/`` remain supported
for backward compatibility
(see :data:`hudoc_py.config.DATA_DIR`).
"""

from .store import cache_dir, read_parquet, write_parquet

__all__ = ["cache_dir", "read_parquet", "write_parquet"]
