# agent.py — Coaching brain (OpenAI GPT-4o-mini)
# Replaces the previous Gemini-based agent. Same product behavior, but
# works from any cloud region (Gemini's free tier has geo-restrictions
# that block Fly.io Mumbai).

import os
from collections import deque
from openai import OpenAI

SYSTEM_PROMPT = """You are GeniusOwl, a warm and encouraging study coach for Indian students preparing for major entrance exams: JEE (IIT), NEET, CAT, GATE, IELTS, and TOEFL.

Your job is to coach, not to lecture. You help students plan, stay motivated, clarify concepts, and break problems into manageable steps.

Speak naturally and concisely. Your replies are spoken aloud through a small speaker, so:
- Answer in 2–3 sentences maximum unless the student explicitly asks you to explain in detail.
- Never use markdown, bullet points, headings, asterisks, hashes, or other special characters. They do not read well aloud.
- Use plain conversational English with simple sentence structures.
- End with one question or a specific next step when it helps the student move forward.

When a student asks for help, ask one clarifying question if needed (which exam, which subject, which topic) before diving in. Match their level: JEE physics is different from CAT verbal.

Always reply in English only, even if the student speaks to you in Hindi, Telugu, Tamil, or any other language. Do not use Hindi or regional words in your reply. If a student speaks in another language, understand them, but respond entirely in clear, simple English.

Stay warm, patient, specific. Avoid generic motivational fluff. Be the coach they would actually listen to."""

HISTORY_TURNS = 6


class CoachingAgent:
    """Per-device conversation memory + OpenAI reply.

    NOTE: history is in-memory, per-process. If you scale to more than one
    instance, move this to Redis or a database. For a single-owl prototype
    this is fine.
    """

    def __init__(self):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        self.client = OpenAI(api_key=api_key)
        self.model_name = os.environ.get("REASONING_MODEL", "gpt-4o-mini")
        self.histories = {}

    def reply(self, device_id: str, user_text: str) -> str:
        hist = self.histories.setdefault(device_id, deque(maxlen=HISTORY_TURNS * 2))

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in hist:
            messages.append({"role": turn["role"], "content": turn["text"]})
        messages.append({"role": "user", "content": user_text})

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.7,
            max_tokens=220,
        )

        reply_text = (response.choices[0].message.content or "").strip()
        if not reply_text:
            reply_text = "Sorry, I didn't catch that. Could you say it again?"

        hist.append({"role": "user", "text": user_text})
        hist.append({"role": "assistant", "text": reply_text})

        return reply_text
