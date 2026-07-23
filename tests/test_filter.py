import yaml
from pathlib import Path

from core.filter import PostingFilter
from core.models import Posting

ROOT = Path(__file__).resolve().parent.parent


def _filter(**overrides):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["filters"]
    cfg.update(overrides)
    return PostingFilter(cfg)


def _p(title, location="", description=""):
    return Posting(
        firm="Test Firm", job_id="1", title=title, location=location,
        url="http://x", ats="greenhouse", description=description,
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


def test_recall_first_plain_associate_matches():
    # Recall-first design: a bare US associate/attorney role DOES match (we would
    # rather surface a lateral role than miss a generically-titled entry role).
    f = _filter()
    assert f.decide(_p("Corporate Associate")).matched
    assert f.decide(_p("Litigation Attorney", "New York, NY")).matched


def test_recall_first_seniority_guardrails():
    # ...but roles a graduating 3L plainly cannot take are excluded.
    f = _filter()
    assert not f.decide(_p("Senior Corporate Associate")).matched
    assert not f.decide(_p("Real Estate Associate (Junior to Mid-Level)")).matched
    assert not f.decide(_p("Corporate M&A Associate (Mid-Senior Level)")).matched
    # staff titles that would otherwise slip in via the bare "attorney" include
    assert not f.decide(_p("Conflicts Attorney")).matched
    assert not f.decide(_p("Staff Attorney - Litigation")).matched


def test_us_only_geo_gate():
    f = _filter()  # config default us_only: true
    # Clearly-foreign location + no US marker => dropped.
    d = f.decide(_p("Corporate Associate", "London, United Kingdom"))
    assert not d.matched and "non-US" in d.reason
    d = f.decide(_p("Associate", "Amsterdam, Netherlands"))
    assert not d.matched
    # A US location is kept.
    assert f.decide(_p("Corporate Associate", "New York, NY")).matched
    # Recall-safe: ambiguous / multi-location strings are NOT treated as foreign.
    assert f.decide(_p("Corporate Associate", "3 Locations")).matched
    assert f.decide(_p("Corporate Associate", "")).matched
    # A role naming BOTH a foreign and a US office is kept (US-inclusive).
    assert f.decide(_p("Corporate Associate", "New York or London")).matched


def test_us_only_can_be_disabled():
    f = _filter(us_only=False)
    assert f.decide(_p("Corporate Associate", "London, United Kingdom")).matched


def test_experience_stated_roles_excluded():
    # A stated years-of-experience requirement is definitionally not entry-level.
    f = _filter()
    assert not f.decide(_p("Associate (3 - 5 years)")).matched
    assert not f.decide(_p("Lateral Associate (3-4 years)")).matched
    assert not f.decide(_p("Disputes Associate (3-5 PQE)")).matched
    assert not f.decide(_p("Litigation Associate, 5+ years")).matched
    assert not f.decide(_p("Associate with at least 3 years of experience")).matched
    assert not f.decide(_p("Associate (Litigation 4th-5th Year)")).matched


def test_experience_exclusion_is_recall_safe():
    f = _filter()
    # ambiguous (no experience stated) -> still kept
    assert f.decide(_p("Corporate Associate")).matched
    # low ranges / entry signals win over the experience exclude
    assert f.decide(_p("Entry-Level Associate (0-2 years)")).matched
    assert f.decide(_p("Associate (0-2 years)")).matched      # range starts at 0
    assert f.decide(_p("First-Year Associate")).matched
    assert f.decide(_p("2026 Associate")).matched


def test_description_experience_gate_excludes_laterals():
    # A title silent about seniority, but the DESCRIPTION states an experience
    # floor -> lateral, dropped. Covers digit, "at least", range, spelled-out,
    # and "N years of experience" phrasings (all seen in live descriptions).
    f = _filter()
    assert not f.decide(_p("Corporate Associate", description="We seek an associate with 3+ years of experience.")).matched
    assert not f.decide(_p("Litigation Associate", description="Candidates must have at least 4 years in litigation.")).matched
    assert not f.decide(_p("Trademark Associate", description="Requires 3-5 years of trademark practice.")).matched
    # Orrick's real wording that slipped the digit-only patterns:
    assert not f.decide(_p("Technology Transactions Associate", description="Seeking an associate with two to four years of experience.")).matched
    assert not f.decide(_p("Real Estate Associate", description="The ideal candidate has at least six years of experience.")).matched
    d = f.decide(_p("Corporate Associate", description="Minimum of 5 years required."))
    assert not d.matched and "description regex" in d.reason


def test_description_gate_is_recall_safe():
    f = _filter()
    # No description -> gate can't fire, ambiguous title still kept.
    assert f.decide(_p("Corporate Associate", description="")).matched
    # Low / entry-friendly ranges are kept.
    assert f.decide(_p("Corporate Associate", description="Open to candidates with 0-2 years of experience.")).matched
    assert f.decide(_p("Corporate Associate", description="1-3 years of experience welcome.")).matched
    assert f.decide(_p("Corporate Associate", description="Ideal for someone with one year of experience.")).matched
    # An explicit entry signal in the DESCRIPTION overrides an experience floor
    # mentioned elsewhere in the same body (recall-safe).
    assert f.decide(_p("Corporate Associate", description="This is an entry-level role; you'll work with partners who have 20 years of experience.")).matched
    # "years of experience" with no number must NOT gate (it's in nearly every desc).
    assert f.decide(_p("Corporate Associate", description="You will gain years of experience here.")).matched
    # School references shouldn't gate.
    assert f.decide(_p("Corporate Associate", description="Completed three years of law school.")).matched


def test_description_gate_can_be_disabled():
    f = _filter(experience_gate_description=False)
    assert f.decide(_p("Corporate Associate", description="Requires 5+ years of experience.")).matched


def test_geo_gate_catches_foreign_in_title():
    # Baker McKenzie renders the office in the TITLE and leaves location blank;
    # the geo gate must still drop it.
    f = _filter()
    assert not f.decide(_p("London • 3330 • 23-Jul-2026 • Attorney", location="")).matched
    assert not f.decide(_p("Tax Lawyer (Zurich / Geneva)", location="")).matched
    # But a US city in the title is kept.
    assert f.decide(_p("Houston • 1234 • Attorney", location="")).matched


def test_entry_score_ranks_signals():
    f = _filter()
    # Strongest: explicit first-year / entry-level / class-year.
    assert f.entry_score(_p("2026 First Year Associate")) == 3
    assert f.entry_score(_p("Entry-Level Associate")) == 3
    assert f.entry_score(_p("Associate, Class of 2027")) == 3
    # Medium: junior / clerkship signals.
    assert f.entry_score(_p("Junior Associate")) == 2
    assert f.entry_score(_p("Judicial Clerkship Associate")) == 2
    # Ambiguous bare roles score 0 (still shown, just lower).
    assert f.entry_score(_p("Corporate Associate")) == 0
    # A signal in the description also counts.
    assert f.entry_score(_p("Corporate Associate", description="Open to the entering class of 2027.")) == 3


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
