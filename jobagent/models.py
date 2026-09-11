"""
models.py — core data structures for the GeniusOwl job agent.

Everything that crosses a module boundary is one of these. Keep them dumb:
no I/O, no network, no LLM calls.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional
import hashlib
import json


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UserProfile:
    """One job seeker. Created when they upload a resume on the website."""

    user_id: str
    wa_number: str                      # E.164, no '+', e.g. 919876543210
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""                      # primary search city
    extra_cities: list = field(default_factory=list)
    remote_ok: bool = True
    current_title: str = ""
    seniority: str = ""                 # intern / analyst / manager / director
    years_experience: float = 0.0
    skills: list = field(default_factory=list)
    role_families: list = field(default_factory=list)   # from taxonomy.py
    target_titles: list = field(default_factory=list)   # expanded adjacent titles
    min_salary_lpa: float = 0.0
    notice_period_days: int = 0
    work_auth: str = ""
    resume_text: str = ""
    resume_path: str = ""
    answers: dict = field(default_factory=dict)   # cached answers to application questions
    daily_hour_ist: int = 9
    active: bool = True
    created_at: str = field(default_factory=_now)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(blob: str) -> "UserProfile":
        return UserProfile(**json.loads(blob))


@dataclass
class Job:
    """A single posting, normalized across every source adapter."""

    source: str                 # greenhouse | lever | adzuna | feed | custom
    external_id: str
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    posted_at: str = ""
    salary: str = ""
    apply_email: str = ""
    apply_mode: str = "assisted"    # assisted | email | ats_form
    raw: dict = field(default_factory=dict)

    @property
    def job_id(self) -> str:
        """Stable id used for dedupe across sources.

        Same role reposted on two boards should collapse to one row, so the
        hash is over company+title+location, not the source URL.
        """
        key = f"{self.company.strip().lower()}|{self.title.strip().lower()}|{self.location.strip().lower()}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


@dataclass
class ScoredJob:
    """A job after matching, with the reasoning we show the user."""

    job: Job
    score: float                             # 0..100
    role_family: str = ""
    matched_keywords: list = field(default_factory=list)
    missing_keywords: list = field(default_factory=list)
    reason: str = ""                         # one line, shown on WhatsApp

    @property
    def fit_label(self) -> str:
        if self.score >= 80:
            return "Strong fit"
        if self.score >= 65:
            return "Good fit"
        if self.score >= 50:
            return "Stretch"
        return "Long shot"


@dataclass
class Application:
    user_id: str
    job_id: str
    status: str = "queued"      # queued | needs_info | applied | failed | skipped
    mode: str = "assisted"
    tailored_resume_path: str = ""
    note: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
