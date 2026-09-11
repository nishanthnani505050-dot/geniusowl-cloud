"""
sources/base.py — the job source contract.

Every board is an adapter. Adding a board never touches matching, tailoring
or WhatsApp code.

COMPLIANCE NOTE (read before writing a new adapter):
LinkedIn and Naukri both prohibit scraping and automated access in their
terms of service, and LinkedIn actively fingerprints and bans accounts that
do it. Adapters shipped in this repo use official public APIs or feeds the
publisher intends for machine consumption. If you add a scraping adapter,
that is your decision and your account risk — keep it in a separate module
so the compliant path stays deployable.
"""

import logging
from typing import Iterable

from ..models import Job

log = logging.getLogger(__name__)


class JobSource:
    """Base adapter. Implement fetch() and register the class."""

    name = "base"
    enabled_by_default = False

    def fetch(self, titles: list, city: str, remote_ok: bool = True, limit: int = 100) -> Iterable[Job]:
        raise NotImplementedError

    # -- helpers shared by adapters -------------------------------------

    @staticmethod
    def matches_location(job_location: str, city: str, remote_ok: bool) -> bool:
        loc = (job_location or "").lower()
        if not city:
            return True
        if city.lower() in loc:
            return True
        if remote_ok and any(w in loc for w in ("remote", "anywhere", "work from home", "hybrid")):
            return True
        return False

    @staticmethod
    def matches_title(job_title: str, titles: list) -> bool:
        """Loose title gate: any meaningful word overlap with a target title.

        Deliberately permissive — the matcher does the real scoring, and a
        strict gate here would undo the whole adjacent-roles idea.
        """
        jt = (job_title or "").lower()
        if not titles:
            return True
        for t in titles:
            words = [w for w in t.lower().split() if len(w) > 3]
            if not words:
                continue
            hits = sum(1 for w in words if w in jt)
            if hits >= max(1, len(words) // 2):
                return True
        return False


_REGISTRY = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def build_sources(names: list = None) -> list:
    """Instantiate the configured adapters, skipping any that fail to init
    (usually a missing API key) so one bad key never kills the daily run."""
    chosen = names or [n for n, c in _REGISTRY.items() if c.enabled_by_default]
    built = []
    for n in chosen:
        cls = _REGISTRY.get(n)
        if not cls:
            log.warning("unknown job source %r, skipping", n)
            continue
        try:
            built.append(cls())
        except Exception as e:
            log.warning("source %s unavailable: %s", n, e)
    return built


def registry() -> dict:
    return dict(_REGISTRY)
