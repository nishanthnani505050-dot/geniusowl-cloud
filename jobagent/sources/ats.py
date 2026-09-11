"""
sources/ats.py — Greenhouse and Lever public job board APIs.

Both publish documented, unauthenticated JSON endpoints intended for exactly
this use, so there is no ToS risk and no rate-limit games. The trade-off is
that you must know which companies to watch: set JOBAGENT_GREENHOUSE_BOARDS
and JOBAGENT_LEVER_BOARDS to comma-separated board slugs.

This is the highest-signal source in the stack. Postings appear here before
they reach aggregators, and applying goes straight to the company ATS rather
than through a job board's tracking layer.
"""

import logging
import os
import re

import requests

from ..models import Job
from .base import JobSource, register

log = logging.getLogger(__name__)

TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    import html
    return html.unescape(_TAG.sub(" ", text or "")).replace("\xa0", " ").strip()


@register
class GreenhouseSource(JobSource):
    name = "greenhouse"
    enabled_by_default = True

    def __init__(self):
        raw = os.environ.get("JOBAGENT_GREENHOUSE_BOARDS", "")
        self.boards = [b.strip() for b in raw.split(",") if b.strip()]
        if not self.boards:
            raise RuntimeError("JOBAGENT_GREENHOUSE_BOARDS not set")

    def fetch(self, titles, city, remote_ok=True, limit=100):
        out = []
        for board in self.boards:
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            try:
                data = requests.get(url, timeout=TIMEOUT).json()
            except Exception:
                log.warning("greenhouse board %s failed", board)
                continue
            for j in data.get("jobs", []):
                location = (j.get("location") or {}).get("name", "")
                title = j.get("title", "")
                if not self.matches_location(location, city, remote_ok):
                    continue
                if not self.matches_title(title, titles):
                    continue
                out.append(Job(
                    source=self.name,
                    external_id=str(j.get("id")),
                    title=title,
                    company=board,
                    location=location,
                    url=j.get("absolute_url", ""),
                    description=_strip_html(j.get("content", ""))[:12000],
                    posted_at=j.get("updated_at", ""),
                    apply_mode="assisted",
                ))
                if len(out) >= limit:
                    return out
        return out


@register
class LeverSource(JobSource):
    name = "lever"
    enabled_by_default = True

    def __init__(self):
        raw = os.environ.get("JOBAGENT_LEVER_BOARDS", "")
        self.boards = [b.strip() for b in raw.split(",") if b.strip()]
        if not self.boards:
            raise RuntimeError("JOBAGENT_LEVER_BOARDS not set")

    def fetch(self, titles, city, remote_ok=True, limit=100):
        out = []
        for board in self.boards:
            url = f"https://api.lever.co/v0/postings/{board}?mode=json"
            try:
                data = requests.get(url, timeout=TIMEOUT).json()
            except Exception:
                log.warning("lever board %s failed", board)
                continue
            for j in data:
                cats = j.get("categories") or {}
                location = cats.get("location", "") or ""
                title = j.get("text", "")
                if not self.matches_location(location, city, remote_ok):
                    continue
                if not self.matches_title(title, titles):
                    continue
                out.append(Job(
                    source=self.name,
                    external_id=str(j.get("id")),
                    title=title,
                    company=board,
                    location=location,
                    url=j.get("hostedUrl", ""),
                    description=_strip_html(j.get("descriptionPlain") or j.get("description", ""))[:12000],
                    posted_at=str(j.get("createdAt", "")),
                    apply_mode="assisted",
                ))
                if len(out) >= limit:
                    return out
        return out
