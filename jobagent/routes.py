"""
routes.py — Flask blueprint mounting the job agent onto the GeniusOwl app.

  GET  /jobagent/health          liveness
  POST /jobagent/resume          resume upload from the website -> profile
  POST /jobagent/run             trigger the daily run (cron / Cloud Scheduler)
  GET  /whatsapp/webhook         Meta webhook verification handshake
  POST /whatsapp/webhook         inbound WhatsApp messages
"""

import logging
import os

from flask import Blueprint, request, jsonify

from . import store, resume as resume_mod, daily, conversation, whatsapp

log = logging.getLogger(__name__)

bp = Blueprint("jobagent", __name__)

_wa = None
_llm = None
MODEL = os.environ.get("JOBAGENT_MODEL", "gpt-4o-mini")


def _wa_client():
    global _wa
    if _wa is None:
        _wa = whatsapp.WhatsAppClient()
    return _wa


def _llm_client():
    global _llm
    if _llm is None:
        from openai import OpenAI
        _llm = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _llm


def _admin_ok(req) -> bool:
    expected = os.environ.get("JOBAGENT_ADMIN_TOKEN", "")
    if not expected:
        return False
    header = req.headers.get("Authorization", "")
    return header.startswith("Bearer ") and header[7:].strip() == expected


@bp.route("/jobagent/health", methods=["GET"])
def health():
    return jsonify({"service": "geniusowl-jobagent", "status": "ok"})


@bp.route("/jobagent/resume", methods=["POST"])
def upload_resume():
    """Multipart: file=<resume>, wa_number=<E.164 no +>, city=<optional>."""
    if not _admin_ok(request):
        return jsonify({"error": "unauthorized"}), 401

    wa_number = (request.form.get("wa_number") or "").strip().lstrip("+")
    if not wa_number.isdigit() or len(wa_number) < 10:
        return jsonify({"error": "wa_number must be digits in E.164 form, e.g. 919876543210"}), 400

    upload = request.files.get("file")
    if not upload:
        return jsonify({"error": "file is required"}), 400

    try:
        profile = resume_mod.build_profile(
            wa_number=wa_number,
            filename=upload.filename or "resume.txt",
            data=upload.read(),
            client=_llm_client(),
            city=(request.form.get("city") or "").strip(),
            model=MODEL,
        )
    except Exception as e:
        log.exception("resume parse failed")
        return jsonify({"error": str(e)}), 400

    existing = store.get_user_by_wa(wa_number)
    if existing:
        profile.user_id = existing.user_id      # keep history on re-upload
    store.upsert_user(profile)

    try:
        _wa_client().send_text(
            wa_number,
            f"Hi {profile.name or 'there'} — resume received. I will look for "
            f"{', '.join(profile.target_titles[:4])} roles in {profile.city or 'India'} "
            "and send you 10 matches here every morning. Reply HELP any time.",
        )
    except Exception:
        log.exception("welcome message failed")

    return jsonify({
        "user_id": profile.user_id,
        "name": profile.name,
        "current_title": profile.current_title,
        "role_families": profile.role_families,
        "target_titles": profile.target_titles,
    })


@bp.route("/jobagent/run", methods=["POST"])
def run_daily():
    if not _admin_ok(request):
        return jsonify({"error": "unauthorized"}), 401
    force = request.args.get("force") == "1"
    result = daily.run_all(_wa_client(), _llm_client(), MODEL, force=force)
    return jsonify(result)


@bp.route("/whatsapp/webhook", methods=["GET"])
def verify_webhook():
    """Meta's one-time subscription handshake."""
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == expected:
        return request.args.get("hub.challenge", ""), 200
    return "forbidden", 403


@bp.route("/whatsapp/webhook", methods=["POST"])
def inbound_webhook():
    """Always 200 fast: Meta retries aggressively on anything else."""
    raw = request.get_data()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        log.warning("rejected webhook with bad signature")
        return "", 403

    try:
        messages = whatsapp.parse_inbound(request.get_json(silent=True) or {})
    except Exception:
        log.exception("could not parse webhook body")
        return "", 200

    wa = _wa_client()
    for msg in messages:
        if store.is_duplicate_inbound(msg["message_id"]):
            continue
        try:
            wa.mark_read(msg["message_id"])
            conversation.handle_message(msg, wa, _llm_client(), MODEL)
        except Exception:
            log.exception("message handler crashed for %s", msg.get("from"))
            try:
                wa.send_text(msg["from"], "Something broke on my side. Reply JOBS to try again.")
            except Exception:
                pass

    return "", 200
