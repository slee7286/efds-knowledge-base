"""Controlled ICU topics, EFDS roles, and conservative rule-based mapping."""

from __future__ import annotations

from dataclasses import dataclass
import re


TOPICS: tuple[tuple[str, str], ...] = (
    ("governance", "Governance"),
    ("committee_management", "Committee management"),
    ("finance", "Finance"),
    ("funding", "Funding"),
    ("sponsorship", "Sponsorship"),
    ("events", "Events"),
    ("room_booking", "Room booking"),
    ("external_speakers", "External speakers and partners"),
    ("risk_compliance", "Risk and compliance"),
    ("sums", "SUMS and systems"),
    ("membership", "Membership"),
    ("marketing", "Marketing and communications"),
    ("data_privacy", "Data and privacy"),
    ("trips_transport", "Trips and transport"),
    ("training", "Training"),
    ("elections", "Elections"),
    ("volunteering", "Volunteering and recognition"),
    ("wellbeing", "Wellbeing and belonging"),
    ("services", "Union services"),
    ("fundraising", "Fundraising"),
    ("new_activities", "New activities"),
    ("other", "Other"),
)

ROLES: tuple[tuple[str, str, str], ...] = (
    ("chair", "Chair", "efds_committee"),
    ("vice_president", "Vice President", "efds_committee"),
    ("treasurer", "Treasurer", "efds_committee"),
    ("secretary", "Secretary", "efds_committee"),
    ("social_secretary", "Social Secretary", "efds_committee"),
    ("events_officer", "Events Officer", "efds_committee"),
    ("economics_industry_officer", "Economics Industry Officer", "efds_committee"),
    ("finance_industry_officer", "Finance Industry Officer", "efds_committee"),
    ("data_science_industry_officer", "Data Science Industry Officer", "efds_committee"),
    ("competition_officer", "Competition Officer", "efds_committee"),
    ("year_2_general_secretary", "Year 2 General Secretary", "efds_committee"),
    ("year_3_general_secretary", "Year 3 General Secretary", "efds_committee"),
    ("departmental_wellbeing_representative", "Departmental Wellbeing Representative", "department"),
    ("departmental_academic_representative", "Departmental Academic Representative", "department"),
)

RELEVANCE_ORDER = {"irrelevant": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

FOLDER_TOPIC_MAP = {
    "COMMITTEE MANAGEMENT": {"governance", "committee_management"},
    "FINANCES": {"finance"},
    "FUNDING": {"funding"},
    "SPONSORSHIPS": {"sponsorship"},
    "EVENTS & TRIPS": {"events", "trips_transport"},
    "ROOM BOOKINGS": {"room_booking"},
    "EXTERNALS & PARTNERSHIPS": {"external_speakers"},
    "COMPLIANCE & REGULATION": {"risk_compliance"},
    "SUMS": {"sums"},
    "MINIBUSES": {"trips_transport", "risk_compliance"},
    "TRAINING": {"training"},
    "BELONGING & WELLBEING": {"wellbeing"},
    "SERVICES": {"services"},
    "FUNDRAISING": {"fundraising"},
    "NEW ACTIVITIES INCUBATOR": {"new_activities"},
}

TOPIC_KEYWORDS = {
    "finance": ("finance", "budget", "claim", "reimburse", "purchase order", "invoice", "spend", "payment"),
    "funding": ("funding", "grant", "fund application", "funding application"),
    "sponsorship": ("sponsor", "sponsorship", "partner"),
    "events": ("event", "activity", "social", "stall", "ticket", "production"),
    "room_booking": ("room booking", "bookable space", "celcat", "venue"),
    "external_speakers": ("speaker", "supplier", "external partner"),
    "risk_compliance": ("risk", "insurance", "incident", "safety", "under 18", "compliance", "welfare concern"),
    "sums": ("sums", "eactivities", "podio"),
    "membership": ("membership", "membership types"),
    "marketing": ("newsletter", "marketing", "what's on", "communications"),
    "data_privacy": ("data", "privacy", "personal information"),
    "trips_transport": ("trip", "minibus", "transport", "driver", "boat party"),
    "training": ("training", "coach", "instructor"),
    "elections": ("election", "co-opt", "resignation"),
    "wellbeing": ("wellbeing", "belonging", "accessibility", "inclusivity", "content warning"),
    "services": ("helpdesk", "equipment", "refund", "space access"),
    "fundraising": ("fundrais",),
    "new_activities": ("incubator", "new activity"),
}

ROLE_TOPIC_MAP = {
    "chair": {"governance", "committee_management", "risk_compliance", "events", "finance", "funding"},
    "vice_president": set(topic for topic, _ in TOPICS if topic != "other"),
    "treasurer": {"finance", "funding", "sponsorship", "events", "trips_transport", "risk_compliance"},
    "secretary": {"governance", "committee_management", "membership", "room_booking", "training", "elections", "services"},
    "social_secretary": {"events", "room_booking", "risk_compliance", "trips_transport", "wellbeing", "services"},
    "events_officer": {"events", "room_booking", "risk_compliance", "trips_transport", "external_speakers", "services"},
    "economics_industry_officer": {"sponsorship", "external_speakers", "events"},
    "finance_industry_officer": {"finance", "funding", "sponsorship", "events"},
    "data_science_industry_officer": {"data_privacy", "events", "training", "sponsorship"},
    "competition_officer": {"events", "risk_compliance", "trips_transport", "funding"},
    "year_2_general_secretary": {"governance", "committee_management", "events", "training", "membership"},
    "year_3_general_secretary": {"governance", "committee_management", "events", "training", "membership"},
    "departmental_wellbeing_representative": {"wellbeing", "risk_compliance", "events"},
    "departmental_academic_representative": {"governance", "committee_management", "training", "services"},
}


@dataclass(frozen=True, slots=True)
class ArticleClassification:
    relevance: str
    confidence: float
    evidence: str
    topics: tuple[str, ...]
    roles: tuple[str, ...]


def classify_article(*, title: str, category: str | None, folder: str | None, text: str) -> ArticleClassification:
    # The first part of a Freshdesk article is usually its useful summary. A
    # whole-article scan overweights generic words such as “event” and
    # “member”, especially in duplicated HTML-to-Markdown sections.
    haystack = " ".join(part for part in (title, category or "", folder or "", text[:4000]) if part).lower()
    folder_topics = FOLDER_TOPIC_MAP.get((folder or "").upper(), set())
    topics = set(folder_topics)
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            topics.add(topic)
    topics.discard("other")
    if not topics:
        topics.add("other")

    critical_terms = ("insurance", "reporting an incident", "under 18", "welfare concern", "high risk", "frozen account")
    high_terms = ("finance", "funding", "sponsor", "room booking", "minibus", "risk assessment", "event", "training", "sums", "election")
    if any(term in haystack for term in critical_terms):
        relevance, confidence = "critical", 0.90
        evidence = "Contains a safety, welfare, insurance, high-risk, or account-control topic."
    elif topics.intersection({"finance", "funding", "sponsorship", "events", "room_booking", "risk_compliance", "trips_transport", "sums", "training", "governance"}) or any(term in haystack for term in high_terms):
        relevance, confidence = "high", 0.84
        evidence = "Matches a recurring EFDS operational topic or committee process."
    elif topics.intersection({"wellbeing", "services", "membership", "marketing", "data_privacy", "fundraising"}):
        relevance, confidence = "medium", 0.76
        evidence = "May support a committee responsibility or departmental role."
    elif topics == {"other"}:
        relevance, confidence = "low", 0.55
        evidence = "No strong EFDS operational topic signal was found."
    else:
        relevance, confidence = "low", 0.60
        evidence = "Weak or indirect EFDS relevance signal."

    roles = {role for role, role_topics in ROLE_TOPIC_MAP.items() if topics.intersection(role_topics)}
    if relevance in {"critical", "high"} and not roles:
        roles.add("chair")
    return ArticleClassification(relevance, confidence, evidence, tuple(sorted(topics)), tuple(sorted(roles)))


def relevance_at_least(value: str, minimum: str) -> bool:
    return RELEVANCE_ORDER.get(value, -1) >= RELEVANCE_ORDER.get(minimum, -1)
