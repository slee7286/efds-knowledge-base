from datetime import date

from efds.knowledge.deterministic import (
    extract_contacts,
    extract_process,
    extract_requirements,
    extract_resources,
    extract_timing_rules,
)
from efds.knowledge.taxonomy import classify_article, relevance_at_least
from efds.knowledge.semantic import NullSemanticExtractor


def test_relative_notice_period_is_not_forced_into_absolute_date() -> None:
    results = extract_timing_rules("Ad-hoc requests must be submitted at least 5 working days in advance.")
    assert len(results) == 1
    assert results[0].deadline_type == "relative_notice"
    assert results[0].notice_period_value == 5
    assert results[0].working_days is True
    assert results[0].absolute_date is None


def test_absolute_date_and_recurring_window_are_distinct() -> None:
    absolute = extract_timing_rules("Applications close 15 September 2026.")
    no_year = extract_timing_rules("Applications close 15 September.")
    recurring = extract_timing_rules("Applications open every September before the start of term.")
    assert absolute[0].absolute_date == date(2026, 9, 15)
    assert absolute[0].deadline_type == "absolute_date"
    assert no_year[0].deadline_type == "date_window"
    assert no_year[0].absolute_date is None
    assert any(item.deadline_type == "recurring_window" for item in recurring)
    assert any(item.deadline_type == "seasonal_term" for item in recurring)


def test_links_forms_systems_and_emails_are_deterministic() -> None:
    text = "Submit the [Committee Admin Request Form](https://www.cognitoforms.com/ICUActivities/form) or email union@imperial.ac.uk. Log in at https://eactivities.union.ic.ac.uk/."
    resources = extract_resources(text)
    contacts = extract_contacts(text)
    assert any(item.resource_type == "form" for item in resources)
    assert any(item.resource_type == "system" and item.system_name == "eActivities" for item in resources)
    assert contacts[0].email == "union@imperial.ac.uk"


def test_requirement_candidates_preserve_obligation_evidence() -> None:
    results = extract_requirements("Before booking, you must complete the Activity Proposal Form. You are required to keep the confirmation email as your booking record.")
    assert len(results) == 2
    assert results[0].mandatory is True
    assert results[0].requirement_type == "required_form"
    assert "must complete" in results[0].text
    assert results[1].requirement_type == "documentation_requirement"


def test_numbered_process_steps_are_ordered() -> None:
    process = extract_process("Room booking", "Step 1: Check availability\nStep 2: Complete the form\nStep 3: Wait for confirmation")
    assert process is not None
    assert [step.step_number for step in process.steps] == [1, 2, 3]
    assert process.steps[1].instruction == "Complete the form"


def test_relevance_and_role_mapping_are_conservative_and_queryable() -> None:
    result = classify_article(
        title="Key Financial Processes",
        category="Committee Member Resources",
        folder="FINANCES",
        text="All financial activity must use the approved systems. Treasurers should retain receipts.",
    )
    assert result.relevance in {"high", "critical"}
    assert "finance" in result.topics
    assert "treasurer" in result.roles
    assert relevance_at_least(result.relevance, "high")


def test_empty_or_malformed_article_is_safe_and_no_llm_is_required() -> None:
    assert extract_requirements("") == []
    assert extract_timing_rules("<not valid article") == []
    assert extract_resources("not a link") == []
    assert NullSemanticExtractor().extract(title="Broken", text="").requirements == ()
