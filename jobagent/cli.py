"""
cli.py — operate the job agent without the web server.

  python -m jobagent.cli initdb
  python -m jobagent.cli add --file resume.pdf --wa 919876543210 --city Bengaluru
  python -m jobagent.cli preview --user <id>     # score today's jobs, print, send nothing
  python -m jobagent.cli daily [--force]         # the cron entrypoint
  python -m jobagent.cli tailor --user <id> --job <job_id>

'preview' is the one to use while tuning: it exercises sources, taxonomy and
matching end to end and costs nothing on WhatsApp.
"""

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _llm():
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def main(argv=None):
    parser = argparse.ArgumentParser(prog="jobagent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("initdb")

    p_add = sub.add_parser("add")
    p_add.add_argument("--file", required=True)
    p_add.add_argument("--wa", required=True)
    p_add.add_argument("--city", default="")

    p_prev = sub.add_parser("preview")
    p_prev.add_argument("--user", required=True)
    p_prev.add_argument("--no-llm", action="store_true")

    p_daily = sub.add_parser("daily")
    p_daily.add_argument("--force", action="store_true")

    p_tailor = sub.add_parser("tailor")
    p_tailor.add_argument("--user", required=True)
    p_tailor.add_argument("--job", required=True)

    args = parser.parse_args(argv)

    from . import store
    store.init_db()

    if args.cmd == "initdb":
        print(f"db ready: {store.DB_PATH}")
        return 0

    if args.cmd == "add":
        from . import resume as resume_mod
        with open(args.file, "rb") as fh:
            data = fh.read()
        profile = resume_mod.build_profile(args.wa.lstrip("+"), os.path.basename(args.file),
                                           data, _llm(), args.city)
        store.upsert_user(profile)
        print(f"user {profile.user_id}: {profile.current_title}")
        print("families:", profile.role_families)
        print("will search:", ", ".join(profile.target_titles))
        return 0

    if args.cmd == "preview":
        from . import daily
        profile = store.get_user(args.user)
        if not profile:
            print("no such user", file=sys.stderr)
            return 1
        matches = daily.build_digest(profile, None if args.no_llm else _llm())
        if not matches:
            print("no new matches")
            return 0
        for i, m in enumerate(matches, 1):
            print(f"{i:2}. [{m.score:5.1f}] {m.fit_label:11} {m.job.title} — {m.job.company} "
                  f"({m.job.location})")
            if m.reason:
                print(f"     why: {m.reason}")
            print(f"     matched: {', '.join(m.matched_keywords[:8])}")
            print(f"     gaps:    {', '.join(m.missing_keywords[:8])}")
            print(f"     {m.job.url}")
        return 0

    if args.cmd == "daily":
        from . import daily, whatsapp
        result = daily.run_all(whatsapp.WhatsAppClient(), _llm(), force=args.force)
        print(result)
        return 0

    if args.cmd == "tailor":
        from .tailor import tailor_resume, save_tailored
        profile, job = store.get_user(args.user), store.get_job(args.job)
        if not (profile and job):
            print("user or job not found", file=sys.stderr)
            return 1
        report = tailor_resume(profile, job, _llm())
        path = save_tailored(profile, job, report["text"])
        print(f"coverage {int(report['coverage']*100)}% -> {path}")
        if report["review_flags"]:
            print("check these numbers:", report["review_flags"])
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
