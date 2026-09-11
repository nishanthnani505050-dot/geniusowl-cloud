"""Job source adapters. Importing this package registers every adapter."""

from .base import JobSource, register, build_sources, registry  # noqa: F401
from . import ats       # noqa: F401  greenhouse, lever
from . import aggregator  # noqa: F401  adzuna, generic feeds
