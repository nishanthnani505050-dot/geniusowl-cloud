"""
End-to-end smoke test with fake sources, a fake LLM and a fake WhatsApp
client. Runs offline with no API keys:

    python -m tests.test_jobagent
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["JOBAGENT_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from jobagent import store, taxonomy, matcher, daily, conversation  # noqa: E402
from jobagent.models import UserProfile, Job  # noqa: E402


class FakeSource:
    name = "fake"

    def __init__(self, jobs):
        self.jobs = jobs

    def fetch(self, titles, city, remote_ok=True, limit=100):
        return self.jobs


class FakeWhatsApp:
    def __init__(self):
        self.sent = []

    def send_text(self, to, body):
        self.sent.append(("text", to, body))

    def send_job_list(self, to, items, header="x"):
        self.sent.append(("list", to, items))

    def send_buttons(self, to, body, buttons):
        self.sent.append(("buttons", to, body))

    def send_daily_template(self, to, name, count, role):
        self.sent.append(("template", to, f"{name}|{count}|{role}"))

    def mark_read(self, mid):
        pass


def make_jobs():
    return [
        Job(source="fake", external_id="1", title="Corporate Strategy Manager",
            company="Acme", location="Bengaluru, India", url="https://x/1",
            description="Lead market sizing, commercial due diligence, business case "
                        "and synergy analysis. Financial modelling and board deck experience."),
        Job(source="fake", external_id="2", title="Management Consultant",
            company="Beta", location="Bengaluru", url="https://x/2",
            description="Strategy consulting, due diligence, valuation, market sizing, p&l."),
        Job(source="fake", external_id="3", title="Frontend Engineer",
            company="Gamma", location="Bengaluru", url="https://x/3",
            description="React, typescript, css, node, webpack."),
        Job(source="fake", external_id="4", title="Summer Intern Finance",
            company="Delta", location="Pune", url="https://x/4",
            description="Support the finance team with excel work."),
    ]


def test_taxonomy():
    out = taxonomy.expand_titles("M&A Analyst", "Worked on due diligence and valuation")
    assert "corp_dev_strategy" in out["families"], out
    titles = " ".join(out["titles"]).lower()
    for expected in ("corporate strategy", "management consultant", "commercial due diligence"):
        assert expected in titles, f"missing {expected}"

    sales = taxonomy.expand_titles("Sales Consultant", "Managed pipeline in salesforce, b2b saas quota")
    st = " ".join(sales["titles"]).lower()
    for expected in ("go to market", "solutions consultant", "marketing manager"):
        assert expected in st, f"missing {expected}"
    print("PASS taxonomy: M&A -> strategy/consulting, sales -> GTM/solutions/marketing")


def make_profile():
    return UserProfile(
        user_id="u1", wa_number="919999999999", name="Test User",
        email="t@example.com", phone="9999999999", city="Bengaluru",
        current_title="M&A Analyst", years_experience=4,
        skills=["financial modelling", "valuation", "due diligence", "market sizing", "excel"],
        role_families=["corp_dev_strategy"],
        target_titles=taxonomy.ROLE_FAMILIES["corp_dev_strategy"]["titles"],
        resume_text="M&A analyst with 4 years in due diligence, valuation, DCF, "
                    "market sizing and board decks for deals worth 500 crore." * 5,
        notice_period_days=60,
    )


def test_matching():
    profile = make_profile()
    ranked = matcher.top_matches(make_jobs(), profile, limit=10, client=None)
    titles = [r.job.title for r in ranked]
    assert titles[0] in ("Corporate Strategy Manager", "Management Consultant"), titles
    assert titles.index("Frontend Engineer") > 1, titles
    assert ranked[0].matched_keywords, "no keyword evidence surfaced"
    print(f"PASS matching: ranked {titles}")


def test_digest_and_reply_flow():
    store.init_db()
    profile = make_profile()
    store.upsert_user(profile)

    wa = FakeWhatsApp()
    sent = daily.run_for_user(profile, wa, llm_client=None, sources=[FakeSource(make_jobs())])
    assert sent >= 3, sent
    assert wa.sent[0][0] == "template", wa.sent[0]

    digest = store.latest_digest("u1")
    assert digest and len(digest["items"]) == sent

    # user asks for the list
    conversation.handle_message({"from": "919999999999", "text": "jobs", "message_id": "m1"},
                                wa, llm_client=None)
    assert any(kind == "list" for kind, *_ in wa.sent), "no interactive list sent"

    # de-dupe: same jobs must not come back tomorrow
    again = daily.build_digest(profile, llm_client=None, sources=[FakeSource(make_jobs())])
    assert again == [], "already-seen jobs were shown again"

    # pause works
    conversation.handle_message({"from": "919999999999", "text": "pause", "message_id": "m2"},
                                wa, llm_client=None)
    assert store.get_user_by_wa("919999999999").active is False
    print(f"PASS flow: digest of {sent} sent, list rendered, dedupe + pause work")


def test_selection_parsing():
    assert conversation._parse_selection("1 3 5") == [1, 3, 5]
    assert conversation._parse_selection("1,3,5") == [1, 3, 5]
    assert conversation._parse_selection("apply 2") == [2]
    assert conversation._parse_selection("ALL") == "all"
    assert conversation._parse_selection("what is this") is None
    print("PASS selection parsing")


def test_fabrication_guard():
    from jobagent.tailor import fabrication_flags, keyword_coverage
    flags = fabrication_flags("Led deals worth 500 crore", "Led deals worth 500 crore, grew revenue 42%")
    assert "42" in flags, flags
    cov = keyword_coverage("commercial due diligence market sizing valuation",
                           "I did commercial due diligence and market sizing")
    assert cov["coverage"] > 0.5, cov
    print("PASS tailoring guards: invented numbers flagged, coverage computed")


if __name__ == "__main__":
    test_taxonomy()
    test_matching()
    test_digest_and_reply_flow()
    test_selection_parsing()
    test_fabrication_guard()
    print("\nAll job agent tests passed.")
