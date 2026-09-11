"""
tailor.py — rewrite the resume against one job description.

This is the module that moves the shortlist rate. Most ATS screens are
keyword-driven: the same M&A resume rejected for a "Commercial Strategy"
role gets through once the resume says "commercial due diligence" and
"market sizing" in the candidate's own real experience.

Hard rule enforced in the prompt and checked after generation: we re-word
and re-emphasise what the resume already contains. We never add an employer,
a tool, a certification or a number that is not in the source resume. A
fabricated resume gets the candidate fired in round two, which is a far worse
outcome than a missed interview.
"""

import logging
import os
import re

log = logging.getLogger(__name__)

TAILOR_PROMPT = """You are an expert ATS resume writer for the Indian job market.

Rewrite the candidate's resume so it ranks for THIS job description, using ONLY facts already
present in the original resume. You may:
  - re-word an existing responsibility in the job description's vocabulary
    (e.g. "acquisition target evaluation" -> "commercial due diligence")
  - re-order and re-emphasise experience so the most relevant work comes first
  - add a 2-line summary at the top written in the job description's language
  - add a SKILLS line listing only skills the original resume evidences

You may NOT:
  - invent an employer, title, date, degree, certification, tool or metric
  - claim experience with anything the original resume does not show
  - change any number

Job title: {job_title} at {company}
Job description:
---
{jd}
---

Original resume:
---
{resume}
---

Return the complete tailored resume as plain text, ATS-safe: no tables, no columns,
no graphics, standard section headings (SUMMARY, SKILLS, EXPERIENCE, EDUCATION),
bullets starting with '-'. Keep it to the original length or shorter."""

_WORD = re.compile(r"[a-z0-9&+#]+")


def tailor_resume(profile, job, client, model: str = "gpt-4o-mini") -> dict:
    """Return {'text': tailored resume, 'added_keywords': [...], 'coverage': 0-1}."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": TAILOR_PROMPT.format(
            job_title=job.title,
            company=job.company,
            jd=job.description[:8000],
            resume=profile.resume_text[:12000],
        )}],
        temperature=0.3,
        max_tokens=2000,
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("tailoring produced empty output")

    report = keyword_coverage(job.description, text)
    report["text"] = text
    report["added_keywords"] = sorted(
        set(_WORD.findall(text.lower())) - set(_WORD.findall(profile.resume_text.lower()))
    )[:25]

    flags = fabrication_flags(profile.resume_text, text)
    if flags:
        log.warning("tailored resume for %s introduced unverified numbers: %s", job.job_id, flags)
    report["review_flags"] = flags
    return report


def keyword_coverage(jd: str, resume_text: str) -> dict:
    """How many of the JD's distinctive terms now appear in the resume."""
    jd_terms = _key_terms(jd)
    resume_low = resume_text.lower()
    hit = [t for t in jd_terms if t in resume_low]
    miss = [t for t in jd_terms if t not in resume_low]
    coverage = len(hit) / len(jd_terms) if jd_terms else 0.0
    return {"coverage": round(coverage, 2), "hit": hit[:25], "missing": miss[:25]}


_JD_STOP = {
    "the", "and", "for", "with", "you", "our", "will", "this", "that", "from", "have",
    "your", "are", "work", "role", "team", "job", "company", "about", "who", "what",
    "years", "experience", "candidate", "ability", "strong", "good", "join", "we",
    "should", "must", "plus", "etc", "including", "across", "within", "other",
}


def _key_terms(jd: str, limit: int = 40) -> list:
    """Distinctive JD terms by frequency — cheap proxy for what the ATS indexes."""
    words = [w for w in _WORD.findall((jd or "").lower()) if len(w) > 3 and w not in _JD_STOP]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:limit]]


_NUM = re.compile(r"\b\d[\d,.]*%?\b")


def fabrication_flags(original: str, tailored: str) -> list:
    """Numbers in the tailored resume that do not appear in the original.

    Cheap guard against the model inventing metrics. Anything listed here is
    surfaced to the user before the application is sent.
    """
    orig_nums = set(_NUM.findall(original))
    return [n for n in set(_NUM.findall(tailored)) if n not in orig_nums and len(n) > 1][:10]


def save_tailored(profile, job, text: str, out_dir: str = "tailored") -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", f"{job.company}-{job.title}".lower())[:60].strip("-")
    path = os.path.join(out_dir, f"{profile.user_id}_{safe}_{job.job_id}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
