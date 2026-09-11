"""
store.py — SQLite persistence for the job agent.

Single file DB so the prototype runs anywhere (Fly volume, Cloud Run + a
mounted disk, or a laptop). Every function opens its own connection: cheap
for this traffic and safe across Flask threads.

Swap DB_PATH for Postgres when you outgrow one box — the function signatures
here are the only thing the rest of the code depends on.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

from .models import UserProfile, Job, Application

log = logging.getLogger(__name__)

DB_PATH = os.environ.get("JOBAGENT_DB", "jobagent.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    wa_number   TEXT UNIQUE NOT NULL,
    profile     TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT,
    url         TEXT,
    payload     TEXT NOT NULL,
    seen_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS digests (
    digest_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    run_date    TEXT NOT NULL,
    items       TEXT NOT NULL,
    sent_at     TEXT,
    UNIQUE(user_id, run_date)
);

CREATE TABLE IF NOT EXISTS applications (
    app_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    status      TEXT NOT NULL,
    mode        TEXT NOT NULL,
    resume_path TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(user_id, job_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    wa_number   TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    context     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbound_seen (
    message_id  TEXT PRIMARY KEY,
    seen_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_apps_user ON applications(user_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_seen ON jobs(seen_at);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _conn() as conn:
        conn.executescript(SCHEMA)
    log.info("jobagent db ready at %s", DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- users

def upsert_user(profile: UserProfile):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO users (user_id, wa_number, profile, active, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 wa_number=excluded.wa_number, profile=excluded.profile,
                 active=excluded.active, updated_at=excluded.updated_at""",
            (profile.user_id, profile.wa_number, profile.to_json(),
             1 if profile.active else 0, _now()),
        )


def get_user(user_id: str) -> Optional[UserProfile]:
    with _conn() as conn:
        row = conn.execute("SELECT profile FROM users WHERE user_id=?", (user_id,)).fetchone()
    return UserProfile.from_json(row["profile"]) if row else None


def get_user_by_wa(wa_number: str) -> Optional[UserProfile]:
    with _conn() as conn:
        row = conn.execute("SELECT profile FROM users WHERE wa_number=?", (wa_number,)).fetchone()
    return UserProfile.from_json(row["profile"]) if row else None


def active_users() -> list:
    with _conn() as conn:
        rows = conn.execute("SELECT profile FROM users WHERE active=1").fetchall()
    return [UserProfile.from_json(r["profile"]) for r in rows]


# ---------------------------------------------------------------- jobs

def save_jobs(jobs: list):
    now = _now()
    with _conn() as conn:
        conn.executemany(
            """INSERT INTO jobs (job_id, source, title, company, location, url, payload, seen_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(job_id) DO NOTHING""",
            [(j.job_id, j.source, j.title, j.company, j.location, j.url,
              json.dumps(j.__dict__, ensure_ascii=False, default=str), now) for j in jobs],
        )


def get_job(job_id: str) -> Optional[Job]:
    with _conn() as conn:
        row = conn.execute("SELECT payload FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row["payload"])
    data.pop("raw", None)
    return Job(raw={}, **data)


def prune_jobs(days: int = 45):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as conn:
        conn.execute("DELETE FROM jobs WHERE seen_at < ?", (cutoff,))


# ---------------------------------------------------------------- digests

def save_digest(user_id: str, run_date: str, items: list):
    """items = [{job_id, title, company, score, reason, url}, ...] in display order."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO digests (user_id, run_date, items, sent_at) VALUES (?,?,?,?)
               ON CONFLICT(user_id, run_date) DO UPDATE SET items=excluded.items""",
            (user_id, run_date, json.dumps(items, ensure_ascii=False), _now()),
        )


def latest_digest(user_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT run_date, items FROM digests WHERE user_id=? ORDER BY digest_id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {"run_date": row["run_date"], "items": json.loads(row["items"])}


def already_seen_job_ids(user_id: str, days: int = 30) -> set:
    """Jobs we already showed or applied to — never repeat them in a digest."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    seen = set()
    with _conn() as conn:
        for row in conn.execute(
            "SELECT items FROM digests WHERE user_id=? AND run_date >= ?", (user_id, cutoff)
        ):
            seen.update(i["job_id"] for i in json.loads(row["items"]))
        for row in conn.execute("SELECT job_id FROM applications WHERE user_id=?", (user_id,)):
            seen.add(row["job_id"])
    return seen


# ---------------------------------------------------------------- applications

def upsert_application(app: Application):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO applications
                 (user_id, job_id, status, mode, resume_path, note, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, job_id) DO UPDATE SET
                 status=excluded.status, mode=excluded.mode,
                 resume_path=excluded.resume_path, note=excluded.note,
                 updated_at=excluded.updated_at""",
            (app.user_id, app.job_id, app.status, app.mode, app.tailored_resume_path,
             app.note, app.created_at, _now()),
        )


def applications_for(user_id: str, status: Optional[str] = None) -> list:
    sql = "SELECT * FROM applications WHERE user_id=?"
    args = [user_id]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY app_id DESC"
    with _conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- conversation state

def get_state(wa_number: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT state, context FROM conversations WHERE wa_number=?", (wa_number,)
        ).fetchone()
    if not row:
        return {"state": "idle", "context": {}}
    return {"state": row["state"], "context": json.loads(row["context"])}


def set_state(wa_number: str, state: str, context: dict):
    with _conn() as conn:
        conn.execute(
            """INSERT INTO conversations (wa_number, state, context, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(wa_number) DO UPDATE SET
                 state=excluded.state, context=excluded.context, updated_at=excluded.updated_at""",
            (wa_number, state, json.dumps(context, ensure_ascii=False), _now()),
        )


def is_duplicate_inbound(message_id: str) -> bool:
    """WhatsApp retries webhooks. Process each message id exactly once."""
    if not message_id:
        return False
    try:
        with _conn() as conn:
            conn.execute("INSERT INTO inbound_seen (message_id, seen_at) VALUES (?,?)",
                         (message_id, _now()))
        return False
    except sqlite3.IntegrityError:
        return True
