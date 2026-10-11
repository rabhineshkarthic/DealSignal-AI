"""IC review parser tests. Pure Python; no LLM."""

import json

import pytest

from agents.registry import AGENT_NAMES
from ic_review import ICReview, ICReviewParseError, RevisionRequest, parse_ic_review


def review(**overrides):
    base = {
        "status": "approved",
        "summary": "All analyses meet the review criteria.",
        "revision_requests": [],
        "cross_workstream_issues": [],
        "unresolved_information_gaps": ["Customer data is not supplied."],
    }
    base.update(overrides)
    return json.dumps(base)


def request(agent="Risk Analyst", reason="Gap presented as risk.",
            required_change="Relabel it as an information gap."):
    return {"agent": agent, "reason": reason, "required_change": required_change}


def test_parses_valid_approved_review():
    parsed = parse_ic_review(review())
    assert isinstance(parsed, ICReview)
    assert parsed.approved
    assert parsed.revision_requests == ()
    assert parsed.unresolved_information_gaps == ("Customer data is not supplied.",)


def test_parses_valid_revision_required_review():
    parsed = parse_ic_review(review(
        status="revision_required",
        revision_requests=[request(), request(agent="Financial Due Diligence")],
        cross_workstream_issues=["Risk and Financial DD disagree on a gap."],
    ))
    assert not parsed.approved
    assert parsed.revision_requests[0] == RevisionRequest(
        "Risk Analyst", "Gap presented as risk.", "Relabel it as an information gap.")
    assert parsed.flagged_agents == ("Risk Analyst", "Financial Due Diligence")


def test_surrounding_whitespace_is_allowed():
    assert parse_ic_review("\n  " + review() + "\n").approved


@pytest.mark.parametrize("text", [
    "not json", "", '{"status": "approved"', "```json\n" + review() + "\n```",
    "Here is my review: " + review(),
])
def test_rejects_malformed_json_without_repair(text):
    with pytest.raises(ICReviewParseError, match="not valid JSON"):
        parse_ic_review(text)


def test_rejects_non_object():
    with pytest.raises(ICReviewParseError, match="JSON object"):
        parse_ic_review("[]")


@pytest.mark.parametrize("key", ["status", "summary", "revision_requests",
                                 "cross_workstream_issues", "unresolved_information_gaps"])
def test_rejects_missing_keys(key):
    data = json.loads(review())
    del data[key]
    with pytest.raises(ICReviewParseError, match="missing keys"):
        parse_ic_review(json.dumps(data))


@pytest.mark.parametrize("extra", ["score", "confidence", "rating", "recommendation"])
def test_rejects_extra_keys(extra):
    data = json.loads(review())
    data[extra] = "x"
    with pytest.raises(ICReviewParseError, match="unexpected keys"):
        parse_ic_review(json.dumps(data))


@pytest.mark.parametrize("status", ["Approved", "proceed", "pass", "rejected", "", None])
def test_rejects_invalid_status(status):
    with pytest.raises(ICReviewParseError, match="status must be"):
        parse_ic_review(review(status=status))


def test_rejects_unknown_agent_target():
    with pytest.raises(ICReviewParseError, match="unknown agent"):
        parse_ic_review(review(status="revision_required",
                               revision_requests=[request(agent="Market Analyst")]))


def test_rejects_ic_partner_as_target():
    with pytest.raises(ICReviewParseError, match="unknown agent 'IC Partner'"):
        parse_ic_review(review(status="revision_required",
                               revision_requests=[request(agent="IC Partner")]))


def test_agent_names_are_case_sensitive_exact():
    with pytest.raises(ICReviewParseError, match="unknown agent"):
        parse_ic_review(review(status="revision_required",
                               revision_requests=[request(agent="risk analyst")]))


def test_rejects_duplicate_targets():
    with pytest.raises(ICReviewParseError, match="duplicate revision requests"):
        parse_ic_review(review(status="revision_required",
                               revision_requests=[request(), request(reason="Another.")]))


@pytest.mark.parametrize("field", ["reason", "required_change", "agent"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_rejects_empty_request_fields(field, bad):
    with pytest.raises(ICReviewParseError, match="must not be empty"):
        parse_ic_review(review(status="revision_required",
                               revision_requests=[{**request(), field: bad}]))


def test_rejects_request_with_missing_or_extra_keys():
    r = request()
    del r["reason"]
    with pytest.raises(ICReviewParseError, match="missing keys"):
        parse_ic_review(review(status="revision_required", revision_requests=[r]))
    with pytest.raises(ICReviewParseError, match="unexpected keys"):
        parse_ic_review(review(status="revision_required",
                               revision_requests=[{**request(), "severity": "high"}]))


def test_rejects_approved_with_revisions():
    with pytest.raises(ICReviewParseError, match="approved review must have no"):
        parse_ic_review(review(revision_requests=[request()]))


def test_rejects_revision_required_without_revisions():
    with pytest.raises(ICReviewParseError, match="at least one revision request"):
        parse_ic_review(review(status="revision_required"))


@pytest.mark.parametrize("overrides, message", [
    ({"summary": ""}, "summary must not be empty"),
    ({"summary": 5}, "summary must be a string"),
    ({"revision_requests": {}}, "revision_requests must be a list"),
    ({"cross_workstream_issues": "issue"}, "cross_workstream_issues must be a list"),
    ({"unresolved_information_gaps": [3]}, "must be a string"),
    ({"cross_workstream_issues": [""]}, "must not be empty"),
    ({"status": "revision_required", "revision_requests": ["Risk Analyst"]}, "must be an object"),
])
def test_rejects_wrong_types(overrides, message):
    with pytest.raises(ICReviewParseError, match=message):
        parse_ic_review(review(**overrides))


def test_every_canonical_agent_is_a_valid_target():
    for name in AGENT_NAMES:
        parsed = parse_ic_review(review(status="revision_required",
                                        revision_requests=[request(agent=name)]))
        assert parsed.flagged_agents == (name,)
