"""
apply.py — application channels.

An ApplyChannel takes (profile, job, tailored_resume) and either completes
the application or tells the user exactly what it still needs.

Three channels ship here:

  assisted  (default, always safe) — tailor the resume, send it to the user
            on WhatsApp as a document plus a one-tap apply link. The user
            taps once. No ToS exposure, no account risk, and it works on
            every board including LinkedIn and Naukri.

  email     — the JD publishes an application email. We send the tailored
            resume and a short cover note. Fully automatic and fully
            legitimate.

  ats_form  — true hands-off submission into an ATS form. Intentionally a
            stub. Automating a login-walled board is against LinkedIn's and
            Naukri's terms and gets accounts banned; automating a company's
            own Greenhouse/Lever form is a grey area but technically
            straightforward. Implement FormApplyChannel yourself if you
            decide to take that on, and read the note in sources/base.py.

Start users on 'assisted'. It converts nearly as well, costs nothing in
account risk, and the daily one-tap flow is the product.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


class ApplyChannel:
    name = "base"

    def can_handle(self, job) -> bool:
        raise NotImplementedError

    def apply(self, profile, job, tailored) -> dict:
        """Return {'status': applied|needs_info|failed, 'message': str, 'needs': [field...]}."""
        raise NotImplementedError


class AssistedApplyChannel(ApplyChannel):
    """One-tap apply: we do the work, the user taps submit."""

    name = "assisted"

    def can_handle(self, job) -> bool:
        return bool(job.url)

    def apply(self, profile, job, tailored) -> dict:
        coverage = int(tailored.get("coverage", 0) * 100)
        missing = tailored.get("missing", [])[:5]
        msg = (
            f"*{job.title}* — {job.company}\n"
            f"Tailored resume ready. ATS keyword match: {coverage}%\n"
        )
        if missing:
            msg += f"Still thin on: {', '.join(missing)}\n"
        msg += f"Apply here: {job.url}"
        return {"status": "applied", "message": msg, "needs": [], "hand_off": True}


class EmailApplyChannel(ApplyChannel):
    """Fully automatic where the posting gives an email address."""

    name = "email"

    def __init__(self):
        self.host = os.environ.get("SMTP_HOST", "")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ.get("SMTP_USER", "")
        self.password = os.environ.get("SMTP_PASSWORD", "")
        self.from_addr = os.environ.get("SMTP_FROM", self.user)

    def can_handle(self, job) -> bool:
        return bool(job.apply_email and self.host and self.user)

    def apply(self, profile, job, tailored) -> dict:
        missing = [f for f in ("name", "email") if not getattr(profile, f, "")]
        if missing:
            return {"status": "needs_info", "message": "", "needs": missing}

        mail = EmailMessage()
        mail["Subject"] = f"Application: {job.title} — {profile.name}"
        mail["From"] = self.from_addr
        mail["To"] = job.apply_email
        mail["Reply-To"] = profile.email
        mail.set_content(
            f"Hello,\n\nI would like to apply for the {job.title} role at {job.company}.\n"
            f"My resume, tailored to the posting, is attached.\n\n"
            f"{profile.name}\n{profile.email}\n{profile.phone}\n"
        )
        mail.add_attachment(
            tailored["text"].encode("utf-8"),
            maintype="text", subtype="plain",
            filename=f"{profile.name or 'resume'}_{job.company}.txt".replace(" ", "_"),
        )
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(self.user, self.password)
                smtp.send_message(mail)
        except Exception as e:
            log.exception("email apply failed for %s", job.job_id)
            return {"status": "failed", "message": str(e), "needs": []}

        return {"status": "applied",
                "message": f"Applied to *{job.title}* at {job.company} by email.",
                "needs": []}


class FormApplyChannel(ApplyChannel):
    """Hands-off ATS form submission. Not implemented — see module docstring.

    To build it: drive Playwright (already available in this image) against
    the company's own ATS form, fill from profile.answers, upload the
    tailored resume, and screenshot the confirmation page as proof. Required
    fields you cannot fill must come back as 'needs_info' so the WhatsApp
    flow can ask the user, and the answer gets cached in profile.answers so
    it is never asked twice.
    """

    name = "ats_form"

    def __init__(self):
        if os.environ.get("JOBAGENT_ENABLE_FORM_APPLY") != "1":
            raise RuntimeError("form apply is disabled")

    def can_handle(self, job) -> bool:
        return False

    def apply(self, profile, job, tailored) -> dict:
        return {"status": "failed", "message": "form apply channel not implemented", "needs": []}


def build_channels() -> list:
    """Ordered by preference: fully automatic first, assisted as the fallback."""
    channels = []
    for cls in (FormApplyChannel, EmailApplyChannel):
        try:
            channels.append(cls())
        except Exception as e:
            log.debug("channel %s unavailable: %s", cls.name, e)
    channels.append(AssistedApplyChannel())
    return channels


def pick_channel(job, channels: list) -> ApplyChannel:
    for ch in channels:
        if ch.can_handle(job):
            return ch
    return AssistedApplyChannel()
