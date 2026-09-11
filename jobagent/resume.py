"""
resume.py — turn an uploaded resume into a structured UserProfile.

Accepts PDF, DOCX or plain text. Extraction is one LLM call with a strict
JSON schema; we never invent facts, we only read what the resume says.
"""

import io
import json
import logging
import os
import uuid
import zipfile
import re

from .models import UserProfile
from . import taxonomy

log = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Extract structured data from this resume. Use ONLY what the resume states.
Never invent employers, skills, degrees or numbers. Leave a field empty if the resume does not say.

Return JSON only with these keys:
  name, email, phone, city, current_title, seniority (intern/analyst/associate/manager/senior manager/director/vp),
  years_experience (number), skills (up to 25 concrete skills, tools and domains),
  achievements (up to 8 quantified bullet points copied or lightly condensed from the resume)

Resume:
---
{resume}
---"""


def extract_text(filename: str, data: bytes) -> str:
    """Best-effort text extraction with no heavyweight dependencies."""
    lower = (filename or "").lower()

    if lower.endswith(".pdf"):
        return _pdf_text(data)
    if lower.endswith(".docx"):
        return _docx_text(data)
    return data.decode("utf-8", errors="ignore")


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is in requirements.txt
        raise RuntimeError("pypdf is required to read PDF resumes")
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_text(data: bytes) -> str:
    """Pull paragraph text straight out of the OOXML — avoids python-docx."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def build_profile(
    wa_number: str,
    filename: str,
    data: bytes,
    client,
    city: str = "",
    model: str = "gpt-4o-mini",
    resume_dir: str = "resumes",
) -> UserProfile:
    """Parse a resume upload into a saved, search-ready profile."""
    text = extract_text(filename, data).strip()
    if len(text) < 200:
        raise ValueError("could not read enough text from the resume file")

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(resume=text[:15000])}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    parsed = json.loads(resp.choices[0].message.content or "{}")

    os.makedirs(resume_dir, exist_ok=True)
    user_id = uuid.uuid4().hex[:12]
    ext = os.path.splitext(filename)[1] or ".txt"
    path = os.path.join(resume_dir, f"{user_id}{ext}")
    with open(path, "wb") as fh:
        fh.write(data)

    profile = UserProfile(
        user_id=user_id,
        wa_number=wa_number,
        name=parsed.get("name", ""),
        email=parsed.get("email", ""),
        phone=parsed.get("phone", ""),
        city=city or parsed.get("city", ""),
        current_title=parsed.get("current_title", ""),
        seniority=parsed.get("seniority", ""),
        years_experience=_as_float(parsed.get("years_experience")),
        skills=[s for s in parsed.get("skills", []) if isinstance(s, str)][:25],
        resume_text=text[:20000],
        resume_path=path,
        answers={"achievements": parsed.get("achievements", [])},
    )

    expansion = taxonomy.expand_titles(profile.current_title, profile.resume_text, client, model)
    profile.role_families = expansion["families"]
    profile.target_titles = expansion["titles"]
    profile.answers["family_keywords"] = expansion["keywords"]

    log.info("profile %s built: %s -> %d target titles",
             user_id, profile.current_title, len(profile.target_titles))
    return profile


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
