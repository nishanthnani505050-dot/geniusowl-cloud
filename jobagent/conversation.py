"""
conversation.py — the WhatsApp state machine.

Everything the user ever does happens here, in WhatsApp, with no website
visit after the first resume upload:

    (9am)  agent -> template: "12 new matches today"
           user  -> "jobs"
           agent -> interactive list of 10 roles
           user  -> "1 3 5"   (or "all", or taps a row)
           agent -> tailors each resume, applies, asks for anything missing
           user  -> answers once; the answer is cached forever
           agent -> "Applied to 3. 2 need your tap: <links>"

States: idle -> digest_sent -> applying -> asking -> idle

Long work (tailoring + applying) runs on a worker thread so the webhook
returns inside Meta's timeout. Meta retries webhooks, so every inbound
message id is de-duplicated in the store before it reaches this module.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from . import store
from .models import Application
from .apply import build_channels, pick_channel
from .tailor import tailor_resume, save_tailored

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=int(__import__("os").environ.get("JOBAGENT_WORKERS", "4")))

HELP = (
    "GeniusOwl job agent commands:\n"
    "JOBS — today's top 10\n"
    "1 3 5 — apply to those numbers\n"
    "ALL — apply to all 10\n"
    "STATUS — what I applied to this week\n"
    "CITY Bengaluru — change your search city\n"
    "PAUSE / RESUME — stop or restart daily messages"
)

# Fields we may need before an application can be submitted, and how we ask.
FIELD_QUESTIONS = {
    "name": "What name should I put on applications?",
    "email": "Which email should recruiters reply to?",
    "phone": "Which phone number should I share with recruiters?",
    "city": "Which city are you targeting?",
    "notice_period_days": "What is your notice period, in days?",
    "min_salary_lpa": "What is your expected CTC, in LPA? (reply SKIP to leave it out)",
}


def handle_message(msg: dict, wa_client, llm_client, model: str = "gpt-4o-mini") -> None:
    """Entry point from the webhook. Never raises: WhatsApp gets a 200 either way."""
    wa_number = msg["from"]
    text = (msg.get("text") or "").strip()
    reply_id = msg.get("reply_id") or ""

    profile = store.get_user_by_wa(wa_number)
    if not profile:
        wa_client.send_text(
            wa_number,
            "I do not have your resume yet. Upload it at geniusowl.in and I will start "
            "sending you 10 matched jobs here every morning.",
        )
        return

    state = store.get_state(wa_number)
    lowered = text.lower()

    # --- global commands work from any state -------------------------------
    if lowered in ("help", "hi", "hello", "menu"):
        wa_client.send_text(wa_number, HELP)
        return

    if lowered in ("pause", "stop", "unsubscribe"):
        profile.active = False
        store.upsert_user(profile)
        wa_client.send_text(wa_number, "Paused. Reply RESUME whenever you want the daily jobs back.")
        return

    if lowered == "resume":
        profile.active = True
        store.upsert_user(profile)
        wa_client.send_text(wa_number, "Back on. Next batch lands tomorrow morning.")
        return

    if lowered == "status":
        wa_client.send_text(wa_number, _status_text(profile))
        return

    if lowered.startswith("city "):
        profile.city = text[5:].strip()
        store.upsert_user(profile)
        wa_client.send_text(wa_number, f"Searching {profile.city} from tomorrow.")
        return

    if state["state"] == "asking":
        _handle_answer(profile, text, state, wa_client, llm_client, model)
        return

    if lowered in ("jobs", "today", "show", "list"):
        _send_digest_list(profile, wa_client)
        return

    # --- a tapped list row: show that one job ------------------------------
    if reply_id.startswith("job:"):
        _send_job_detail(profile, reply_id[4:], wa_client)
        return

    if reply_id.startswith("apply:"):
        _start_applying(profile, [reply_id[6:]], wa_client, llm_client, model)
        return

    # --- a selection like "1 3 5" or "all" ---------------------------------
    picks = _parse_selection(text)
    if picks is not None:
        digest = store.latest_digest(profile.user_id)
        if not digest:
            wa_client.send_text(wa_number, "No jobs queued yet. Reply JOBS after tomorrow's batch.")
            return
        items = digest["items"]
        if picks == "all":
            chosen = [i["job_id"] for i in items]
        else:
            chosen = [items[n - 1]["job_id"] for n in picks if 1 <= n <= len(items)]
        if not chosen:
            wa_client.send_text(wa_number, "Those numbers are not in today's list. Reply JOBS to see it again.")
            return
        _start_applying(profile, chosen, wa_client, llm_client, model)
        return

    wa_client.send_text(wa_number, HELP)


# ------------------------------------------------------------------ digest

def _send_digest_list(profile, wa_client):
    digest = store.latest_digest(profile.user_id)
    if not digest or not digest["items"]:
        wa_client.send_text(profile.wa_number,
                            "Nothing new matched today. I will keep looking and message you tomorrow.")
        return

    items = digest["items"]
    lines = [
        f"{i+1}. *{it['title']}* — {it['company']}\n   {it['fit']} · {it['reason'] or it['location']}"
        for i, it in enumerate(items)
    ]
    wa_client.send_text(
        profile.wa_number,
        "Today's matches:\n\n" + "\n".join(lines) +
        "\n\nReply with numbers to apply (e.g. 1 3 5) or ALL."
    )
    wa_client.send_job_list(
        profile.wa_number,
        [{"id": f"job:{it['job_id']}", "title": f"{i+1}. {it['title']}",
          "description": f"{it['company']} · {it['fit']}"}
         for i, it in enumerate(items)],
    )
    store.set_state(profile.wa_number, "digest_sent", {"run_date": digest["run_date"]})


def _send_job_detail(profile, job_id: str, wa_client):
    job = store.get_job(job_id)
    if not job:
        wa_client.send_text(profile.wa_number, "I lost that posting. Reply JOBS for the current list.")
        return
    body = (
        f"*{job.title}*\n{job.company} · {job.location}\n"
        f"{(job.salary + chr(10)) if job.salary else ''}"
        f"\n{job.description[:700]}...\n\n{job.url}"
    )
    wa_client.send_text(profile.wa_number, body)
    wa_client.send_buttons(profile.wa_number, "Want me to tailor your resume and apply?",
                           [{"id": f"apply:{job_id}", "title": "Apply"},
                            {"id": "skip", "title": "Skip"}])


# ------------------------------------------------------------------ applying

def _start_applying(profile, job_ids: list, wa_client, llm_client, model):
    missing = _missing_fields(profile)
    if missing:
        store.set_state(profile.wa_number, "asking", {
            "queue": job_ids, "pending": missing, "current_field": missing[0],
        })
        wa_client.send_text(
            profile.wa_number,
            f"Before I apply I need {len(missing)} detail"
            f"{'s' if len(missing) > 1 else ''}.\n\n{FIELD_QUESTIONS[missing[0]]}",
        )
        return

    wa_client.send_text(
        profile.wa_number,
        f"On it. Tailoring your resume for {len(job_ids)} role"
        f"{'s' if len(job_ids) > 1 else ''} — I will report back in a couple of minutes.",
    )
    store.set_state(profile.wa_number, "applying", {"queue": job_ids})
    _pool.submit(_run_applications, profile, job_ids, wa_client, llm_client, model)


def _run_applications(profile, job_ids: list, wa_client, llm_client, model):
    """Worker thread: tailor + apply each job, then send one summary."""
    channels = build_channels()
    applied, handoff, failed = [], [], []

    for job_id in job_ids:
        job = store.get_job(job_id)
        if not job:
            continue
        app = Application(user_id=profile.user_id, job_id=job_id, status="queued")
        try:
            tailored = tailor_resume(profile, job, llm_client, model)
            path = save_tailored(profile, job, tailored["text"])
            app.tailored_resume_path = path

            channel = pick_channel(job, channels)
            app.mode = channel.name
            result = channel.apply(profile, job, tailored)

            app.status = result["status"]
            app.note = result.get("message", "")[:500]

            if result.get("hand_off"):
                handoff.append((job, tailored, result["message"]))
            elif result["status"] == "applied":
                applied.append(job)
            else:
                failed.append((job, result.get("message", "")))
        except Exception as e:
            log.exception("apply failed for %s", job_id)
            app.status = "failed"
            app.note = str(e)[:500]
            failed.append((job, str(e)))

        store.upsert_application(app)

    _report(profile, applied, handoff, failed, wa_client)
    store.set_state(profile.wa_number, "idle", {})


def _report(profile, applied, handoff, failed, wa_client):
    parts = []
    if applied:
        parts.append("Submitted:\n" + "\n".join(f"✓ {j.title} — {j.company}" for j in applied))

    for job, tailored, message in handoff:
        wa_client.send_text(profile.wa_number, message)
        flags = tailored.get("review_flags") or []
        if flags:
            wa_client.send_text(
                profile.wa_number,
                f"Heads up: the tailored version mentions {', '.join(flags)}. "
                "Check those numbers are yours before you submit.",
            )

    if failed:
        parts.append("Could not complete:\n" + "\n".join(f"✗ {j.title} — {m[:80]}" for j, m in failed))

    if parts:
        wa_client.send_text(profile.wa_number, "\n\n".join(parts))
    wa_client.send_text(profile.wa_number, "Done for today. Next batch tomorrow morning.")


# ------------------------------------------------------------------ field Q&A

def _missing_fields(profile) -> list:
    missing = []
    for field in ("name", "email", "phone"):
        if not getattr(profile, field, ""):
            missing.append(field)
    if not profile.city:
        missing.append("city")
    if not profile.notice_period_days and "notice_period_days" not in profile.answers:
        missing.append("notice_period_days")
    return missing


def _handle_answer(profile, text, state, wa_client, llm_client, model):
    ctx = state["context"]
    field = ctx.get("current_field")
    pending = list(ctx.get("pending", []))

    if field:
        _set_field(profile, field, text)
        store.upsert_user(profile)
        if field in pending:
            pending.remove(field)

    if pending:
        nxt = pending[0]
        store.set_state(profile.wa_number, "asking",
                        {**ctx, "pending": pending, "current_field": nxt})
        wa_client.send_text(profile.wa_number, FIELD_QUESTIONS[nxt])
        return

    queue = ctx.get("queue", [])
    store.set_state(profile.wa_number, "applying", {"queue": queue})
    wa_client.send_text(profile.wa_number, "Got it — saved for every future application. Applying now.")
    _pool.submit(_run_applications, profile, queue, wa_client, llm_client, model)


def _set_field(profile, field: str, value: str):
    value = value.strip()
    if value.lower() == "skip":
        profile.answers[field] = ""
        return
    if field == "notice_period_days":
        nums = re.findall(r"\d+", value)
        profile.notice_period_days = int(nums[0]) if nums else 0
        profile.answers[field] = value
        return
    if field == "min_salary_lpa":
        nums = re.findall(r"\d+(?:\.\d+)?", value)
        profile.min_salary_lpa = float(nums[0]) if nums else 0.0
        return
    if hasattr(profile, field):
        setattr(profile, field, value)
    profile.answers[field] = value


# ------------------------------------------------------------------ helpers

def _parse_selection(text: str):
    """'1 3 5' / '1,3,5' / 'apply 2' -> [1,3,5]; 'all' -> 'all'; else None."""
    t = text.lower().strip()
    if t in ("all", "apply all"):
        return "all"
    t = t.replace("apply", "").strip()
    if not t or not re.fullmatch(r"[\d\s,]+", t):
        return None
    nums = [int(n) for n in re.findall(r"\d+", t)]
    return nums or None


def _status_text(profile) -> str:
    apps = store.applications_for(profile.user_id)[:15]
    if not apps:
        return "No applications yet. Reply JOBS to see today's matches."
    lines = [f"{a['status'].upper()} · {store.get_job(a['job_id']).title if store.get_job(a['job_id']) else a['job_id']}"
             for a in apps]
    return f"Last {len(lines)} applications:\n" + "\n".join(lines)
