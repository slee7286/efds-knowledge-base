"""Unified PostgreSQL-native retrieval over EFDS canonical sources."""

from .service import make_context_package, search_retrieval
from .types import ContextPackage, RetrievalFilters, RetrievalResult

__all__ = ["ContextPackage", "RetrievalFilters", "RetrievalResult", "make_context_package", "search_retrieval"]
