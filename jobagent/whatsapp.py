"""
whatsapp.py — WhatsApp Cloud API client (Meta Graph API).

Two rules that shape this whole file:

  1. The 24-hour window. You can only send free-form messages within 24 hours
     of the user's last message. The daily digest almost always falls OUTSIDE
     that window, so it MUST go as an approved template. send_daily_template()
     does that; send_text() is only valid once the user has replied.

  2. Interactive lists cap at 10 rows. That is why the digest is exactly 10
     jobs — it fits one native list with no pagination.

Config:
  WHATSAPP_TOKEN           permanent system-user access token
  WHATSAPP_PHONE_ID        phone number id
  WHATSAPP_VERIFY_TOKEN    webhook verification string you choose
  WHATSAPP_APP_SECRET      app secret, used to verify webhook signatures
  WHATSAPP_DIGEST_TEMPLATE approved template name (default: geniusowl_daily_jobs)
"""

import hashlib
import hmac
import logging
import os

import requests

log = logging.getLogger(__name__)

GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_VERSION", "v21.0")
TIMEOUT = 20


class WhatsAppClient:
    def __init__(self):
        self.token = os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_id = os.environ.get("WHATSAPP_PHONE_ID", "")
        self.template = os.environ.get("WHATSAPP_DIGEST_TEMPLATE", "geniusowl_daily_jobs")
        self.template_lang = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")
        self.base = f"https://graph.facebook.com/{GRAPH_VERSION}/{self.phone_id}"
        if not (self.token and self.phone_id):
            raise RuntimeError("WHATSAPP_TOKEN / WHATSAPP_PHONE_ID not set")

    # ------------------------------------------------------------ sending

    def _post(self, payload: dict) -> dict:
        resp = requests.post(
            f"{self.base}/messages",
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 400:
            log.error("whatsapp send failed %s: %s", resp.status_code, resp.text[:500])
        return resp.json() if resp.content else {}

    def send_text(self, to: str, body: str) -> dict:
        """Free-form text. Only valid inside the 24h customer service window."""
        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"preview_url": True, "body": body[:4096]},
        })

    def send_daily_template(self, to: str, name: str, job_count: int, top_role: str) -> dict:
        """Opens the conversation each morning. Body params must match the
        approved template exactly, in order."""
        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": self.template,
                "language": {"code": self.template_lang},
                "components": [{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name or "there"},
                        {"type": "text", "text": str(job_count)},
                        {"type": "text", "text": top_role[:60] or "your target roles"},
                    ],
                }],
            },
        })

    def send_job_list(self, to: str, items: list, header: str = "Today's top 10") -> dict:
        """Native selectable list. items = [{id, title, description}] max 10."""
        rows = [{
            "id": str(i["id"])[:200],
            "title": i["title"][:24],          # Meta hard limits
            "description": i["description"][:72],
        } for i in items[:10]]

        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header[:60]},
                "body": {"text": "Tap to see a role, or reply with numbers to apply "
                                 "(example: 1 3 5, or ALL)."},
                "footer": {"text": "GeniusOwl"},
                "action": {"button": "View jobs", "sections": [{"title": "Matches", "rows": rows}]},
            },
        })

    def send_buttons(self, to: str, body: str, buttons: list) -> dict:
        """buttons = [{id, title}], max 3, titles max 20 chars."""
        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body[:1024]},
                "action": {"buttons": [
                    {"type": "reply", "reply": {"id": str(b["id"])[:256], "title": b["title"][:20]}}
                    for b in buttons[:3]
                ]},
            },
        })

    def send_document(self, to: str, media_id: str, filename: str, caption: str = "") -> dict:
        return self._post({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {"id": media_id, "filename": filename[:100], "caption": caption[:1024]},
        })

    def upload_media(self, content: bytes, filename: str, mime: str = "application/pdf") -> str:
        """Upload a tailored resume, returns a media id usable for 30 days."""
        resp = requests.post(
            f"{self.base}/media",
            headers={"Authorization": f"Bearer {self.token}"},
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, content, mime)},
            timeout=60,
        )
        if resp.status_code >= 400:
            log.error("whatsapp media upload failed: %s", resp.text[:500])
            return ""
        return resp.json().get("id", "")

    def mark_read(self, message_id: str):
        self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})


# ---------------------------------------------------------------- webhook

def verify_signature(raw_body: bytes, header: str) -> bool:
    """Validate X-Hub-Signature-256. Never skip this: the webhook URL is
    public and anyone who finds it could otherwise drive the agent."""
    secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if not secret:
        log.warning("WHATSAPP_APP_SECRET unset — webhook signature not verified")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def parse_inbound(payload: dict) -> list:
    """Flatten a webhook body into [{from, message_id, type, text, reply_id}]."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                item = {
                    "from": msg.get("from", ""),
                    "message_id": msg.get("id", ""),
                    "type": msg.get("type", ""),
                    "text": "",
                    "reply_id": "",
                }
                if msg.get("type") == "text":
                    item["text"] = (msg.get("text") or {}).get("body", "").strip()
                elif msg.get("type") == "interactive":
                    inter = msg.get("interactive") or {}
                    node = inter.get("list_reply") or inter.get("button_reply") or {}
                    item["reply_id"] = node.get("id", "")
                    item["text"] = node.get("title", "")
                out.append(item)
    return out
