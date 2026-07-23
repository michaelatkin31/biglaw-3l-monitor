from core.models import Posting
from core.notify import render_digest


def _p(title, job_id, location="", url="http://x/1"):
    return Posting(
        firm="Firm", job_id=job_id, title=title, location=location,
        url=url, ats="jsonapi",
    )


def test_digest_dedups_identical_visible_rows():
    # Same visible role under two job_ids (both "new") -> shown once, counted once.
    postings = [
        _p("Insurance Recovery Associate", "a", "Chicago", "http://x/ir"),
        _p("Insurance Recovery Associate", "b", "Chicago", "http://x/ir"),
    ]
    d = render_digest(postings)
    assert d.match_count == 1
    assert d.text_body.count("Insurance Recovery Associate") == 1
    assert "1 new" in d.subject


def test_digest_keeps_distinct_locations():
    # Same title, different office -> genuinely distinct, both kept.
    postings = [
        _p("Labor & Employment Associate", "a", "Dallas", "http://x/dal"),
        _p("Labor & Employment Associate", "b", "Houston", "http://x/hou"),
    ]
    d = render_digest(postings)
    assert d.match_count == 2


def test_empty_digest_still_renders():
    d = render_digest([])
    assert d.match_count == 0
    assert "No new postings" in d.subject
