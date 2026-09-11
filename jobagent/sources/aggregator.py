"""
sources/aggregator.py — keyword-searchable aggregators.

Adzuna has an official API with solid India coverage and a free tier: it is
the closest compliant substitute for a Naukri/LinkedIn keyword search, and
it indexes both of those in many markets. Get keys at
https://developer.adzuna.com and set JOBAGENT_ADZUNA_APP_ID / _APP_KEY.

FeedSource handles any board that publishes RSS or JSON the publisher
intends to be consumed (company career feeds, Remotive, etc.). Point
JOBAGENT_FEEDS at a comma-separated list of URLs.
"""

import logging
import os
import re
import xml.etree.ElementTree as ET

import requests

from ..models import Job
from .base import JobSource, register

log = logging.getLogger(__name__)

TIMEOUT = 25
_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    import html
    return html.unescape(_TAG.sub(" ", text or "")).strip()


@register
class AdzunaSource(JobSource):
    name = "adzuna"
    enabled_by_default = True

    def __init__(self):
        self.app_id = os.environ.get("JOBAGENT_ADZUNA_APP_ID", "")
        self.app_key = os.environ.get("JOBAGENT_ADZUNA_APP_KEY", "")
        self.country = os.environ.get("JOBAGENT_ADZUNA_COUNTRY", "in")
        self.max_days_old = int(os.environ.get("JOBAGENT_MAX_DAYS_OLD", "3"))
        if not (self.app_id and self.app_key):
            raise RuntimeError("JOBAGENT_ADZUNA_APP_ID / _APP_KEY not set")

    def fetch(self, titles, city, remote_ok=True, limit=100):
        """One query per target title — that is how adjacent roles surface."""
        out, seen = [], set()
        for title in titles[:8]:
            params = {
                "app_id": self.app_id,
                "app_key": self.app_key,
                "what": title,
                "where": city or "India",
                "results_per_page": 20,
                "max_days_old": self.max_days_old,
                "content-type": "application/json",
            }
            url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"
            try:
                data = requests.get(url, params=params, timeout=TIMEOUT).json()
            except Exception:
                log.warning("adzuna query failed for %r", title)
                continue
            for j in data.get("results", []):
                ext = str(j.get("id"))
                if ext in seen:
                    continue
                seen.add(ext)
                salary = ""
                if j.get("salary_min"):
                    salary = f"{int(j['salary_min'])}-{int(j.get('salary_max') or j['salary_min'])}"
                out.append(Job(
                    source=self.name,
                    external_id=ext,
                    title=j.get("title", ""),
                    company=(j.get("company") or {}).get("display_name", ""),
                    location=(j.get("location") or {}).get("display_name", ""),
                    url=j.get("redirect_url", ""),
                    description=_strip_html(j.get("description", ""))[:12000],
                    posted_at=j.get("created", ""),
                    salary=salary,
                    apply_mode="assisted",
                ))
                if len(out) >= limit:
                    return out
        return out


@register
class FeedSource(JobSource):
    name = "feed"
    enabled_by_default = True

    def __init__(self):
        raw = os.environ.get("JOBAGENT_FEEDS", "")
        self.feeds = [u.strip() for u in raw.split(",") if u.strip()]
        if not self.feeds:
            raise RuntimeError("JOBAGENT_FEEDS not set")

    def fetch(self, titles, city, remote_ok=True, limit=100):
        out = []
        for feed_url in self.feeds:
            try:
                resp = requests.get(feed_url, timeout=TIMEOUT,
                                    headers={"User-Agent": "GeniusOwlJobAgent/1.0"})
                items = self._parse(resp)
            except Exception:
                log.warning("feed %s failed", feed_url)
                continue
            for it in items:
                if not self.matches_title(it["title"], titles):
                    continue
                if not self.matches_location(it["location"], city, remote_ok):
                    continue
                out.append(Job(
                    source=self.name,
                    external_id=it["url"][-64:],
                    title=it["title"],
                    company=it["company"],
                    location=it["location"],
                    url=it["url"],
                    description=it["description"][:12000],
                    posted_at=it["posted_at"],
                    apply_mode="assisted",
                ))
                if len(out) >= limit:
                    return out
        return out

    def _parse(self, resp) -> list:
        ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype:
            data = resp.json()
            rows = data.get("jobs") or data.get("results") or data if isinstance(data, list) else []
            return [{
                "title": r.get("title", ""),
                "company": r.get("company_name") or r.get("company", ""),
                "location": r.get("candidate_required_location") or r.get("location", ""),
                "url": r.get("url", ""),
                "description": _strip_html(r.get("description", "")),
                "posted_at": r.get("publication_date") or r.get("created", ""),
            } for r in rows]

        root = ET.fromstring(resp.content)
        out = []
        for item in root.iter("item"):
            def txt(tag):
                el = item.find(tag)
                return (el.text or "").strip() if el is not None and el.text else ""
            title = txt("title")
            company, _, rest = title.partition(" at ")
            out.append({
                "title": rest or title,
                "company": company if rest else "",
                "location": txt("location") or txt("region"),
                "url": txt("link"),
                "description": _strip_html(txt("description")),
                "posted_at": txt("pubDate"),
            })
        return out
