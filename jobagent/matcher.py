"""
matcher.py — score jobs against a profile and pick the daily top 10.

Two stages, on purpose:

  1. Cheap deterministic scoring over every job pulled today (keyword overlap,
     title-family fit, location, recency, seniority). Hundreds of jobs, zero
     API cost.
  2. LLM re-rank over only the top ~25 survivors, which is where judgement
     actually pays: "is an M&A analyst credible for this commercial strategy
     role?" is not a keyword question.

If the LLM call fails the stage-1 order stands, so a daily digest always goes
out.
"""

import json
import logging
import re
from datetime import datetime, timezone

from .models import Job, ScoredJob
from .taxonomy import ROLE_FAMILIES

log = logging.getLogger(__name__)

_WORD = re.compile(r"[a-z0-9&+#./]+")

STOPWORDS = {
    "and", "the", "for", "with", "you", "are", "our", "will", "this", "that", "from",
    "have", "your", "all", "job", "role", "work", "team", "years", "experience",
    "including", "should", "must", "able", "strong", "good", "help", "across",
}


def _tokens(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


def _phrases(text: str, vocabulary: list) -> set:
    """Multi-word ATS keywords ('due diligence') need substring matching."""
    low = (text or "").lower()
    return {kw for kw in vocabulary if kw in low}


def profile_vocabulary(profile) -> list:
    """Everything we consider a 'keyword the person can credibly claim'."""
    vocab = [s.lower() for s in profile.skills]
    for fam in profile.role_families:
        vocab.extend(ROLE_FAMILIES.get(fam, {}).get("keywords", []))
    vocab.extend(profile.answers.get("family_keywords", []))
    return sorted({v for v in vocab if v})


def score_job(job: Job, profile, vocab: list, profile_tokens: set) -> ScoredJob:
    jd = f"{job.title}\n{job.description}"
    jd_tokens = _tokens(jd)
    jd_phrases = _phrases(jd, vocab)

    # --- keyword overlap (0-45)
    profile_phrases = set(vocab)
    matched = sorted(jd_phrases | (jd_tokens & profile_tokens & profile_phrases))
    denom = max(len(jd_phrases) + 6, 10)
    keyword_score = min(45.0, 45.0 * len(matched) / denom)

    # --- title / family fit (0-30)
    title_l = job.title.lower()
    family_hit, best_family = 0.0, ""
    for fam in profile.role_families:
        spec = ROLE_FAMILIES.get(fam)
        if not spec:
            continue
        for t in spec["titles"]:
            words = [w for w in t.lower().split() if len(w) > 3]
            if words and all(w in title_l for w in words):
                family_hit, best_family = 30.0, fam
                break
            if words and any(w in title_l for w in words):
                if family_hit < 18.0:
                    family_hit, best_family = 18.0, fam
        if family_hit >= 30.0:
            break
    if not family_hit:
        for t in profile.target_titles:
            if t.lower() in title_l:
                family_hit = 22.0
                break

    # --- location (0-12)
    loc = (job.location or "").lower()
    if profile.city and profile.city.lower() in loc:
        location_score = 12.0
    elif any(w in loc for w in ("remote", "anywhere", "work from home")) and profile.remote_ok:
        location_score = 10.0
    elif any(c.lower() in loc for c in profile.extra_cities):
        location_score = 9.0
    else:
        location_score = 2.0

    # --- recency (0-8) : a 3-day-old posting converts far better than a 30-day one
    recency_score = _recency_score(job.posted_at)

    # --- seniority sanity (0-5, can go negative)
    seniority_score = _seniority_score(job.title, profile)

    total = keyword_score + family_hit + location_score + recency_score + seniority_score
    total = max(0.0, min(100.0, total))

    missing = sorted(jd_phrases - set(matched))[:12]

    return ScoredJob(
        job=job,
        score=round(total, 1),
        role_family=best_family,
        matched_keywords=matched[:15],
        missing_keywords=missing,
        reason="",
    )


def _recency_score(posted_at: str) -> float:
    if not posted_at:
        return 3.0
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(posted_at[:len("2024-01-01T00:00:00+0000")], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt).days
            if age <= 1:
                return 8.0
            if age <= 3:
                return 6.0
            if age <= 7:
                return 4.0
            if age <= 14:
                return 2.0
            return 0.0
        except ValueError:
            continue
    return 3.0


JUNIOR = ("intern", "trainee", "fresher", "graduate")
SENIOR = ("director", "vp", "vice president", "head of", "chief", "principal")


def _seniority_score(title: str, profile) -> float:
    t = title.lower()
    yrs = profile.years_experience
    if any(w in t for w in JUNIOR) and yrs >= 3:
        return -8.0
    if any(w in t for w in SENIOR) and yrs < 8:
        return -6.0
    if "senior" in t and yrs >= 4:
        return 5.0
    if "manager" in t and yrs >= 5:
        return 4.0
    return 2.0


def top_matches(jobs: list, profile, limit: int = 10, client=None, model: str = "gpt-4o-mini") -> list:
    """Full pipeline: score everything, LLM re-rank the shortlist, return top N."""
    vocab = profile_vocabulary(profile)
    profile_tokens = _tokens(profile.resume_text) | _tokens(" ".join(profile.skills))

    scored = [score_job(j, profile, vocab, profile_tokens) for j in jobs]
    scored.sort(key=lambda s: s.score, reverse=True)

    # Cap per company so one aggressive hirer cannot own the whole digest.
    capped, per_company = [], {}
    for s in scored:
        key = s.job.company.lower()
        if per_company.get(key, 0) >= 2:
            continue
        per_company[key] = per_company.get(key, 0) + 1
        capped.append(s)

    shortlist = capped[: max(limit * 2, 20)]
    if client and shortlist:
        shortlist = _llm_rerank(shortlist, profile, client, model)

    return shortlist[:limit]


RERANK_PROMPT = """You screen candidates for recruiters in India. Decide which postings this
person would realistically get a callback for, including ADJACENT roles (an M&A analyst is
credible for corporate strategy, commercial due diligence and consulting; a sales consultant is
credible for GTM, presales and solutions roles).

Candidate: {title}, {yrs} years experience, based in {city}.
Skills: {skills}

Postings:
{postings}

Return JSON only:
{{"ranked": [{{"i": <index>, "score": <0-100 callback likelihood>, "reason": "<max 12 words, why this fits them>"}}]}}
Rank every posting. Be honest: score below 50 when the jump is not credible."""


def _llm_rerank(shortlist: list, profile, client, model: str) -> list:
    postings = "\n".join(
        f"[{i}] {s.job.title} at {s.job.company} ({s.job.location}) :: {s.job.description[:400]}"
        for i, s in enumerate(shortlist)
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": RERANK_PROMPT.format(
                title=profile.current_title or "unspecified",
                yrs=profile.years_experience,
                city=profile.city or "India",
                skills=", ".join(profile.skills[:20]),
                postings=postings[:12000],
            )}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        ranked = json.loads(resp.choices[0].message.content or "{}").get("ranked", [])
    except Exception:
        log.exception("LLM re-rank failed; keeping deterministic order")
        return shortlist

    by_index = {int(r["i"]): r for r in ranked if isinstance(r.get("i"), (int, float))}
    for i, s in enumerate(shortlist):
        r = by_index.get(i)
        if not r:
            continue
        # Blend rather than replace: keyword evidence still counts for something.
        s.score = round(0.65 * float(r.get("score", s.score)) + 0.35 * s.score, 1)
        s.reason = (r.get("reason") or "").strip()

    shortlist.sort(key=lambda s: s.score, reverse=True)
    return shortlist
