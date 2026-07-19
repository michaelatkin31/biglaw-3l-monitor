import yaml
from pathlib import Path

from core.filter import PostingFilter
from core.models import Posting

ROOT = Path(__file__).resolve().parent.parent


def _filter(**overrides):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["filters"]
    cfg.update(overrides)
    return PostingFilter(cfg)


def _p(title, location=""):
    return Posting(
        firm="Test Firm", job_id="1", title=title, location=location,
        url="http://x", ats="greenhouse",
    )


def test_includes_first_year_associate():
    f = _filter()
    assert f.decide(_p("First-Year Associate, Corporate")).matched
    assert f.decide(_p("First Year Associate (Litigation)")).matched
    assert f.decide(_p("Entry-Level Associate")).matched


def test_class_year_regex():
    f = _filter()
    assert f.decide(_p("2027 Associate - Class of 2027")).matched
    assert f.decide(_p("Associate (Entering Class 2028)")).matched
    assert f.decide(_p("Class of 2026 Corporate Associate")).matched


def test_exclude_beats_include():
    f = _filter()
    # "lateral" excludes even though "associate" class-year-ish is present
    assert not f.decide(_p("Lateral Associate 2027")).matched
    assert not f.decide(_p("Of Counsel, Corporate")).matched
    assert not f.decide(_p("Paralegal - Litigation")).matched
    assert not f.decide(_p("Law Clerk")).matched


def test_summer_toggle():
    off = _filter(include_summer_associate=False)
    on = _filter(include_summer_associate=True)
    # Summer role that would otherwise match on class year
    summer = _p("2027 Summer Associate")
    assert not off.decide(summer).matched
    assert on.decide(summer).matched


def test_short_token_word_boundary():
    f = _filter()
    # "3l" should match as a standalone token...
    assert f.decide(_p("3L Associate Opportunities")).matched
    # ...but not inside an unrelated word/slug.
    assert not f.decide(_p("Global Mobility Coordinator")).matched


def test_plain_associate_not_matched():
    # A bare "Associate" (likely lateral) should not match.
    f = _filter()
    assert not f.decide(_p("Corporate Associate")).matched


def test_apply_returns_only_matches():
    f = _filter()
    postings = [
        _p("First-Year Associate"),
        _p("Lateral Partner"),
        _p("Paralegal"),
        _p("Entry-Level Associate"),
    ]
    matched = f.apply(postings)
    assert len(matched) == 2
    assert {m.title for m in matched} == {"First-Year Associate", "Entry-Level Associate"}
