"""Deterministic extraction primitives for the current ICU article format."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date
import re
from typing import TypeVar
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


MONTHS = {name.lower(): number for number, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
MONTH_PATTERN = r"January|February|March|April|May|June|July|August|September|October|November|December"
DATE_PATTERN = re.compile(
    rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?|(?:{MONTH_PATTERN})\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?(?:\s+\d{{4}})?|\d{{1,2}}/\d{{1,2}}/\d{{4}})\b",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)]+|mailto:[^)]+)\)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Evidence:
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class RequirementCandidate(Evidence):
    requirement_type: str
    mandatory: bool | None
    applies_to: str | None


@dataclass(frozen=True, slots=True)
class TimingCandidate(Evidence):
    deadline_type: str
    absolute_date: date | None = None
    notice_period_value: int | None = None
    notice_period_unit: str | None = None
    working_days: bool | None = None
    recurrence_rule: str | None = None
    relative_to_event_type: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessStepCandidate(Evidence):
    step_number: int
    title: str | None
    instruction: str


@dataclass(frozen=True, slots=True)
class ProcessCandidate(Evidence):
    name: str
    description: str | None
    steps: tuple[ProcessStepCandidate, ...]


@dataclass(frozen=True, slots=True)
class ResourceCandidate(Evidence):
    name: str
    resource_type: str
    url: str | None
    email: str | None
    system_name: str | None
    anchor_text: str | None


@dataclass(frozen=True, slots=True)
class ContactCandidate(Evidence):
    email: str
    name: str | None


def article_text(markdown: str | None, raw_html: str | None = None) -> str:
    source = markdown or raw_html or ""
    if "<" in source and ">" in source:
        source = BeautifulSoup(source, "html.parser").get_text("\n")
    source = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", source)
    source = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", source)
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _evidence_units(text: str) -> list[Evidence]:
    units: list[Evidence] = []
    for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", text):
        value = " ".join(match.group(0).split())
        if value:
            units.append(Evidence(value, match.start(), match.end()))
    return units


EvidenceT = TypeVar("EvidenceT", bound=Evidence)


def _dedupe(items: list[EvidenceT]) -> list[EvidenceT]:
    seen: set[tuple[object, ...]] = set()
    result: list[T] = []
    for item in items:
        key = (type(item).__name__, item.text.casefold()) + tuple(
            getattr(item, field.name)
            for field in fields(item)
            if field.name not in {"text", "start", "end"}
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _requirement_type(text: str) -> str:
    value = text.casefold()
    if re.search(r"\b(form|application|submit)\b", value):
        return "required_form"
    if any(token in value for token in ("finance", "budget", "claim", "reimburse", "purchase", "receipt", "invoice")):
        return "financial_rule"
    if any(token in value for token in ("risk", "insurance", "safety", "incident", "welfare", "under 18")):
        return "compliance_obligation"
    if "train" in value or "course" in value:
        return "required_training"
    if any(token in value for token in ("eligible", "eligibility", "qualif")):
        return "eligibility_rule"
    return "documentation_requirement" if any(token in value for token in ("document", "record", "evidence", "receipt")) else "operational_requirement"


def _applies_to(text: str) -> str | None:
    value = text.casefold()
    for phrase, target in (("driver", "driver"), ("coach", "coach or instructor"), ("committee member", "committee member"), ("treasurer", "treasurer"), ("chair", "chair"), ("club or society", "club or society"), ("under 18", "activities involving under-18s")):
        if phrase in value:
            return target
    return None


def extract_requirements(text: str) -> list[RequirementCandidate]:
    results: list[RequirementCandidate] = []
    for evidence in _evidence_units(text):
        value = evidence.text
        lower = value.casefold()
        if not re.search(r"\b(must|shall|required|need to|needs to|have to|you are expected to)\b", lower):
            continue
        mandatory = bool(re.search(r"\b(must|shall|required|have to)\b", lower))
        results.append(RequirementCandidate(evidence.text, evidence.start, evidence.end, _requirement_type(value), mandatory, _applies_to(value)))
    return _dedupe(results)


def _parse_date(value: str) -> date | None:
    clean = re.sub(r"(st|nd|rd|th)", "", value, flags=re.IGNORECASE)
    parts = clean.replace(",", "").split()
    try:
        if len(parts) == 3 and parts[0].isdigit():
            return date(int(parts[2]), MONTHS[parts[1].casefold()], int(parts[0]))
        if len(parts) == 3 and parts[1].isdigit():
            return date(int(parts[2]), MONTHS[parts[0].casefold()], int(parts[1]))
        if len(parts) == 1 and "/" in parts[0]:
            day, month, year = (int(piece) for piece in parts[0].split("/"))
            return date(year, month, day)
    except (KeyError, ValueError):
        return None
    return None


def extract_timing_rules(text: str) -> list[TimingCandidate]:
    results: list[TimingCandidate] = []
    for evidence in _evidence_units(text):
        value = evidence.text
        lower = value.casefold()
        relative = re.search(r"(?:at least\s+)?(\d+)\s+(working|business|calendar)?\s*days?\s+(?:in advance|before|prior to)", lower)
        if relative:
            results.append(TimingCandidate(value, evidence.start, evidence.end, "relative_notice", notice_period_value=int(relative.group(1)), notice_period_unit="days", working_days=bool(relative.group(2) in {"working", "business"}), relative_to_event_type="event"))
        duration = re.search(r"(?:allow|within|takes?|processing time of)\s+(\d+)\s+(working|business|calendar)?\s*days?", lower)
        if duration:
            results.append(TimingCandidate(value, evidence.start, evidence.end, "duration", notice_period_value=int(duration.group(1)), notice_period_unit="days", working_days=bool(duration.group(2) in {"working", "business"})))
        dates = DATE_PATTERN.findall(value)
        if dates and re.search(r"\b(close|closing|deadline|due|open|opens|submit|application|from|to|between)\w*\b", lower):
            parsed = _parse_date(dates[0])
            results.append(TimingCandidate(value, evidence.start, evidence.end, "absolute_date" if parsed else "date_window", absolute_date=parsed, recurrence_rule=None if parsed else value))
        if re.search(r"\b(every|each|annual|annually|per year|every year)\b", lower) and (dates or "year" in lower or "september" in lower or "term" in lower):
            results.append(TimingCandidate(value, evidence.start, evidence.end, "recurring_window", recurrence_rule=value))
        if re.search(r"\b(before|prior to)\s+(the\s+)?(start|beginning) of term\b", lower):
            results.append(TimingCandidate(value, evidence.start, evidence.end, "seasonal_term", recurrence_rule=value))
    return _dedupe(results)


def extract_process(title: str, text: str) -> ProcessCandidate | None:
    lines = text.splitlines()
    steps: list[ProcessStepCandidate] = []
    for index, line in enumerate(lines):
        match = re.match(r"\s*(?:Step\s*)?(\d+)[.)\-:]\s*(.+?)\s*$", line, re.IGNORECASE)
        if not match:
            continue
        instruction = " ".join(match.group(2).split())
        if not instruction:
            continue
        steps.append(ProcessStepCandidate(instruction, 0, len(line), int(match.group(1)), None, instruction))
    unique_steps: dict[int, ProcessStepCandidate] = {}
    for step in steps:
        unique_steps.setdefault(step.step_number, step)
    steps = list(unique_steps.values())
    if len(steps) < 2:
        return None
    process_name = title.strip() or "ICU operational process"
    description = next((unit.text for unit in _evidence_units(text) if "step" not in unit.text.casefold() and len(unit.text) > 30), None)
    evidence = Evidence(" ".join(step.instruction for step in steps), 0, len(text))
    return ProcessCandidate(evidence.text, evidence.start, evidence.end, process_name, description, tuple(steps))


def _resource_type(name: str, url: str) -> tuple[str, str | None]:
    lower = f"{name} {url}".casefold()
    domain = urlsplit(url).netloc.casefold()
    if "form" in lower or "application" in lower or "request" in lower or "cognitoforms" in domain:
        return "form", None
    if any(token in domain for token in ("eactivities", "sums.su", "imperial.ac.uk", "celcat")):
        system = "SUMS" if "sums" in domain else "eActivities" if "eactivities" in domain else "Imperial College system"
        return "system", system
    if "template" in lower:
        return "template", None
    if re.search(r"\.(?:pdf|docx?|xlsx?|pptx?)(?:$|[?#])", lower) or "/attachments/" in lower:
        return "download", None
    return "link", None


def extract_resources(text: str) -> list[ResourceCandidate]:
    results: list[ResourceCandidate] = []
    seen: set[str] = set()
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        name, url = match.group(1).strip(), match.group(2).strip()
        if url.casefold().startswith("mailto:"):
            continue
        url = url.rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        resource_type, system_name = _resource_type(name, url)
        results.append(ResourceCandidate(match.group(0), match.start(), match.end(), name or url, resource_type, url, None, system_name, name or None))
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url in seen or url.casefold().startswith("mailto:"):
            continue
        seen.add(url)
        resource_type, system_name = _resource_type("", url)
        results.append(ResourceCandidate(url, match.start(), match.end(), url, resource_type, url, None, system_name, None))
    return _dedupe(results)


def extract_contacts(text: str) -> list[ContactCandidate]:
    results: list[ContactCandidate] = []
    for match in EMAIL_PATTERN.finditer(text):
        email = match.group(0).rstrip(".,;:)").casefold()
        evidence_start = max(0, text.rfind("\n", 0, match.start()) + 1)
        evidence_end = text.find("\n", match.end())
        evidence_end = len(text) if evidence_end == -1 else evidence_end
        evidence_text = " ".join(text[evidence_start:evidence_end].split())
        results.append(ContactCandidate(evidence_text or email, evidence_start, evidence_end, email, None))
    return _dedupe(results)
