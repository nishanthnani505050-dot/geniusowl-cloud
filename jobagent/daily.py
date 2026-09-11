"""
daily.py — the scheduled morning run.

    for each active user:
        fetch from every source using their ADJACENT titles, not just their own
        drop anything they have already seen or applied to
        score, re-rank, take the top 10
        store the digest and open the WhatsApp conversation with a template

Run it from cron / Cloud Scheduler once an hour and it will only message the
users whose configured hour matches, so people in different routines get
their batch when they actually check their phone.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from . import store
from .matcher import top_matches
from .sources import build_sources

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
DIGEST_SIZE = int(os.environ.get("JOBAGENT_DIGEST_SIZE", "10"))


def fetch_for_profile(profile, sources=None) -> list:
    """Pull jobs from every source using the expanded adjacent titles."""
    sources = sources if sources is not None else build_sources(
        [s.strip() for s in os.environ.get("JOBAGENT_SOURCES", "").split(",") if s.strip()] or None
    )
    titles = profile.target_titles or ([profile.current_title] if profile.current_title else [])
    all_jobs, seen = [], set()

    for src in sources:
        try:
            jobs = list(src.fetch(titles, profile.city, profile.remote_ok))
        except Exception:
            log.exception("source %s failed for user %s", src.name, profile.user_id)
            continue
        for j in jobs:
            if j.job_id in seen:
                continue
            seen.add(j.job_id)
            all_jobs.append(j)
        log.info("user %s: %s returned %d jobs", profile.user_id, src.name, len(jobs))

    return all_jobs


def build_digest(profile, llm_client=None, model: str = "gpt-4o-mini", sources=None) -> list:
    jobs = fetch_for_profile(profile, sources)
    if not jobs:
        return []

    store.save_jobs(jobs)

    already = store.already_seen_job_ids(profile.user_id)
    fresh = [j for j in jobs if j.job_id not in already]
    log.info("user %s: %d fetched, %d fresh", profile.user_id, len(jobs), len(fresh))
    if not fresh:
        return []

    return top_matches(fresh, profile, limit=DIGEST_SIZE, client=llm_client, model=model)


def run_for_user(profile, wa_client, llm_client=None, model: str = "gpt-4o-mini", sources=None) -> int:
    """Build and send one user's digest. Returns how many jobs were sent."""
    matches = build_digest(profile, llm_client, model, sources)
    if not matches:
        log.info("user %s: nothing new today", profile.user_id)
        return 0

    run_date = datetime.now(IST).date().isoformat()
    items = [{
        "job_id": m.job.job_id,
        "title": m.job.title,
        "company": m.job.company,
        "location": m.job.location,
        "url": m.job.url,
        "score": m.score,
        "fit": m.fit_label,
        "reason": m.reason,
    } for m in matches]

    store.save_digest(profile.user_id, run_date, items)

    # Outside the 24h window we must open with an approved template.
    wa_client.send_daily_template(
        profile.wa_number,
        profile.name.split(" ")[0] if profile.name else "",
        len(items),
        matches[0].job.title,
    )
    store.set_state(profile.wa_number, "digest_sent", {"run_date": run_date})
    log.info("user %s: digest of %d sent", profile.user_id, len(items))
    return len(items)


def run_all(wa_client, llm_client=None, model: str = "gpt-4o-mini", force: bool = False) -> dict:
    """Hourly entrypoint. Only messages users whose daily_hour_ist is now."""
    hour_now = datetime.now(IST).hour
    sources = build_sources(
        [s.strip() for s in os.environ.get("JOBAGENT_SOURCES", "").split(",") if s.strip()] or None
    )
    if not sources:
        log.error("no job sources configured — set JOBAGENT_* keys")
        return {"users": 0, "sent": 0, "error": "no sources"}

    sent, touched = 0, 0
    for profile in store.active_users():
        if not force and profile.daily_hour_ist != hour_now:
            continue
        touched += 1
        try:
            sent += run_for_user(profile, wa_client, llm_client, model, sources)
        except Exception:
            log.exception("daily run failed for user %s", profile.user_id)

    store.prune_jobs()
    return {"users": touched, "sent": sent}
