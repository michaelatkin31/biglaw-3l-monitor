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


def _score(p):
    # Tiny stand-in scorer: entry signal in the title -> 3, else 0.
    t = p.title.lower()
    return 3 if ("first year" in t or "entry-level" in t) else 0


def test_digest_surfaces_likely_entry_level_on_top():
    postings = [
        _p("Corporate Associate", "a", "NY", "http://x/ca"),
        _p("2026 First Year Associate", "b", "NY", "http://x/fy"),
        _p("Litigation Associate", "c", "TX", "http://x/lit"),
        _p("Entry-Level Associate", "d", "MA", "http://x/el"),
    ]
    d = render_digest(postings, score_fn=_score)
    # Subject advertises the likely count.
    assert "2 likely entry-level" in d.subject
    # The likely section appears before the "other" section, and the two
    # entry-level roles appear before the ambiguous ones in the body.
    body = d.text_body
    assert body.index("LIKELY ENTRY-LEVEL") < body.index("OTHER ASSOCIATE ROLES")
    assert body.index("First Year") < body.index("Corporate Associate")
    assert body.index("Entry-Level Associate") < body.index("Litigation Associate")


def test_digest_no_tiers_when_nothing_scores():
    # With no positive scores the digest degrades to the plain firm-grouped form
    # (no tier headers), so ordinary days look unchanged.
    postings = [_p("Corporate Associate", "a", "NY", "http://x/ca")]
    d = render_digest(postings, score_fn=_score)
    assert "LIKELY ENTRY-LEVEL" not in d.text_body
    assert "OTHER ASSOCIATE ROLES" not in d.text_body
    assert "Corporate Associate" in d.text_body
