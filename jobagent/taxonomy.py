"""
taxonomy.py — adjacent-role expansion.

The whole point of the agent: someone in M&A should also see corporate
strategy, management consulting, commercial due diligence and commercial
strategy roles. A sales consultant should also see GTM, solutions, presales
and marketing roles. Searching only their current job title is the single
biggest reason people miss callable jobs.

Two layers:
  1. A hand-built graph (fast, free, deterministic) covering the families we
     see most in India.
  2. An LLM fallback for titles the graph does not know, cached in the store
     so we pay for it once per title.
"""

import json
import logging
import os
import re

log = logging.getLogger(__name__)

# family -> {titles: search terms to query boards with,
#            keywords: ATS keywords that signal this family in a JD,
#            seeds: titles that put a person INTO this family}
ROLE_FAMILIES = {
    "corp_dev_strategy": {
        "seeds": [
            "mergers and acquisitions", "m&a", "investment banking", "corporate development",
            "corporate strategy", "transaction advisory", "valuations", "private equity",
            "strategy consultant", "management consultant", "due diligence",
        ],
        "titles": [
            "Mergers and Acquisitions", "Corporate Development", "Corporate Strategy",
            "Strategy Manager", "Management Consultant", "Strategy Consultant",
            "Commercial Strategy", "Commercial Due Diligence", "Transaction Advisory",
            "Business Strategy", "Strategic Finance", "Investment Banking Associate",
            "Chief of Staff", "Business Planning and Analysis",
        ],
        "keywords": [
            "m&a", "due diligence", "valuation", "dcf", "financial modelling", "financial modeling",
            "synergy", "deal", "market sizing", "go-to-market strategy", "business case",
            "board deck", "target screening", "integration", "p&l", "unit economics",
        ],
    },
    "sales_gtm": {
        "seeds": [
            "sales consultant", "sales executive", "business development", "account executive",
            "inside sales", "presales", "solution consultant", "key account",
        ],
        "titles": [
            "Sales Manager", "Account Executive", "Business Development Manager",
            "Go To Market Manager", "GTM Strategy", "Solutions Consultant", "Presales Consultant",
            "Solution Engineer", "Customer Success Manager", "Partnerships Manager",
            "Inside Sales", "Enterprise Sales", "Revenue Operations", "Marketing Manager",
        ],
        "keywords": [
            "pipeline", "quota", "crm", "salesforce", "hubspot", "arr", "acv", "funnel",
            "lead generation", "b2b", "saas", "account management", "gtm", "cross-sell",
            "demo", "rfp", "negotiation", "churn",
        ],
    },
    "marketing_growth": {
        "seeds": ["marketing", "brand manager", "growth", "digital marketing", "performance marketing"],
        "titles": [
            "Marketing Manager", "Growth Manager", "Product Marketing Manager",
            "Performance Marketing", "Brand Manager", "Digital Marketing Manager",
            "Category Manager", "Demand Generation", "Content Marketing Manager",
        ],
        "keywords": [
            "seo", "sem", "google ads", "meta ads", "cac", "roas", "retention", "positioning",
            "campaign", "gtm", "funnel", "attribution", "ga4", "lifecycle",
        ],
    },
    "product": {
        "seeds": ["product manager", "product owner", "associate product manager", "business analyst"],
        "titles": [
            "Product Manager", "Associate Product Manager", "Product Owner",
            "Technical Program Manager", "Business Analyst", "Product Operations",
            "Growth Product Manager", "Program Manager",
        ],
        "keywords": [
            "roadmap", "prd", "user research", "a/b test", "backlog", "agile", "scrum",
            "stakeholder", "sql", "wireframe", "okr", "discovery", "north star metric",
        ],
    },
    "data_analytics": {
        "seeds": ["data analyst", "business analyst", "data scientist", "analytics", "mis"],
        "titles": [
            "Data Analyst", "Business Analyst", "Analytics Manager", "Data Scientist",
            "Business Intelligence Analyst", "Insights Analyst", "Decision Scientist",
            "Reporting Analyst", "Analytics Consultant",
        ],
        "keywords": [
            "sql", "python", "tableau", "power bi", "looker", "dbt", "etl", "dashboard",
            "a/b testing", "regression", "excel", "statistics", "cohort", "snowflake",
        ],
    },
    "software_engineering": {
        "seeds": ["software engineer", "developer", "sde", "full stack", "backend", "frontend"],
        "titles": [
            "Software Engineer", "Backend Engineer", "Frontend Engineer", "Full Stack Engineer",
            "Platform Engineer", "DevOps Engineer", "Site Reliability Engineer",
            "Solutions Engineer", "Engineering Manager",
        ],
        "keywords": [
            "python", "java", "javascript", "typescript", "react", "node", "aws", "gcp",
            "kubernetes", "docker", "microservices", "rest api", "ci/cd", "postgres", "system design",
        ],
    },
    "finance_accounting": {
        "seeds": ["financial analyst", "fp&a", "accountant", "controller", "audit", "ca "],
        "titles": [
            "Financial Analyst", "FP&A Manager", "Finance Business Partner", "Controller",
            "Internal Audit", "Treasury Analyst", "Revenue Analyst", "Strategic Finance",
        ],
        "keywords": [
            "budgeting", "forecasting", "variance analysis", "ifrs", "gaap", "sap", "oracle",
            "month end close", "reconciliation", "cash flow", "audit", "p&l",
        ],
    },
    "operations_supply": {
        "seeds": ["operations", "supply chain", "logistics", "procurement", "process excellence"],
        "titles": [
            "Operations Manager", "Supply Chain Manager", "Procurement Manager",
            "Business Operations", "Process Excellence", "Logistics Manager",
            "Category Operations", "Program Manager Operations",
        ],
        "keywords": [
            "sop", "lean", "six sigma", "vendor management", "inventory", "cost optimization",
            "sla", "throughput", "erp", "demand planning",
        ],
    },
    "hr_talent": {
        "seeds": ["human resources", "hr", "recruiter", "talent acquisition", "hrbp"],
        "titles": [
            "HR Business Partner", "Talent Acquisition Manager", "Recruiter",
            "HR Operations", "People Partner", "Compensation and Benefits Analyst",
            "Learning and Development Manager",
        ],
        "keywords": [
            "sourcing", "ats", "onboarding", "employee engagement", "performance management",
            "hris", "workday", "stakeholder management", "payroll",
        ],
    },
}

_WORD = re.compile(r"[a-z0-9&+#]+")


def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


def classify(current_title: str, resume_text: str = "") -> list:
    """Return the role families this person belongs to, best first.

    Title carries most of the signal; resume text breaks ties. We allow two
    families because real people straddle them (an M&A analyst who also does
    FP&A should see both).
    """
    title_l = (current_title or "").lower()
    resume_l = (resume_text or "").lower()[:8000]

    scores = {}
    for family, spec in ROLE_FAMILIES.items():
        score = 0.0
        for seed in spec["seeds"]:
            if seed in title_l:
                score += 10.0
            elif seed in resume_l:
                score += 1.5
        for kw in spec["keywords"]:
            if kw in resume_l:
                score += 0.6
        if score:
            scores[family] = score

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [f for f, s in ranked[:2] if s >= 2.0]


def expand_titles(current_title: str, resume_text: str = "", llm_client=None, model: str = "gpt-4o-mini") -> dict:
    """Expand one current title into the full set of adjacent roles to search.

    Returns {"families": [...], "titles": [...], "keywords": [...]}.
    Falls back to the LLM only when the graph recognises nothing.
    """
    families = classify(current_title, resume_text)

    if families:
        titles, keywords = [], []
        for fam in families:
            titles.extend(ROLE_FAMILIES[fam]["titles"])
            keywords.extend(ROLE_FAMILIES[fam]["keywords"])
        if current_title and current_title not in titles:
            titles.insert(0, current_title)
        return {
            "families": families,
            "titles": _dedupe(titles),
            "keywords": _dedupe(keywords),
        }

    if llm_client is None:
        log.warning("no taxonomy match for %r and no LLM client; searching title only", current_title)
        return {"families": [], "titles": [current_title] if current_title else [], "keywords": []}

    return _expand_with_llm(current_title, resume_text, llm_client, model)


def _expand_with_llm(current_title: str, resume_text: str, client, model: str) -> dict:
    prompt = (
        "You map a job seeker's current role to ADJACENT roles they are genuinely "
        "hirable for in the Indian job market. Example: mergers and acquisitions maps to "
        "corporate strategy, management consulting, commercial due diligence, commercial strategy. "
        "Sales consultant maps to GTM, solutions consulting, presales, marketing.\n\n"
        f"Current title: {current_title}\n"
        f"Resume extract: {(resume_text or '')[:3000]}\n\n"
        "Return JSON only: {\"titles\": [12 job titles to search job boards with], "
        "\"keywords\": [20 ATS keywords that appear in those job descriptions]}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "families": ["llm_generated"],
            "titles": _dedupe([current_title] + list(data.get("titles", []))),
            "keywords": _dedupe([k.lower() for k in data.get("keywords", [])]),
        }
    except Exception:
        log.exception("LLM title expansion failed for %r", current_title)
        return {"families": [], "titles": [current_title] if current_title else [], "keywords": []}


def _dedupe(items) -> list:
    seen, out = set(), []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out
