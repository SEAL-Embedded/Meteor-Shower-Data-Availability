"""Ingest adapters.

Importing this package registers every built-in adapter. A new adapter becomes available by adding
its module here; nothing else in the codebase needs to know it exists.
"""

from . import (  # noqa: F401  (importing these registers the adapters)
    ams,
    catalogues,
    csv_files,
    csv_timeseries,
    file_scan,
    supersid,
)
from .base import (
    Adapter,
    AdapterError,
    CoverageResult,
    EventResult,
    get_adapter,
    register,
    registered_adapters,
)

__all__ = [
    "Adapter",
    "AdapterError",
    "CoverageResult",
    "EventResult",
    "get_adapter",
    "register",
    "registered_adapters",
]
