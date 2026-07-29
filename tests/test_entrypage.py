from fetchers.base import Firm
from fetchers.entrypage import EntryPageFetcher


class FakeClient:
    def __init__(self, html):
        self.html = html

    def get_text(self, _url):
        return self.html


def _firm():
    return Firm(
        name="Test Firm",
        ats_type="unknown",
        careers_url="https://firm.example/careers",
    )


def _fetch(html, *, years=(2027,)):
    fetcher = EntryPageFetcher(FakeClient(html), target_years=years)
    return fetcher.fetch_page(
        _firm(),
        {
            "url": "https://firm.example/careers",
            "label": "Entry-Level Recruiting",
        },
    )


def test_open_entry_level_resume_collect_emits_posting():
    html = """
    <h2>Entry Level Associate Candidates</h2>
    <p>We encourage 3Ls to submit your materials through our resume collect.</p>
    <a href="/apply/3l">First Year Associate Candidates | 3L Candidates</a>
    """
    postings = _fetch(html)
    assert len(postings) == 1
    assert postings[0].title == "First Year Associate Candidates | 3L Candidates"
    assert postings[0].location == "United States"
    assert postings[0].url == "https://firm.example/apply/3l"
    assert postings[0].ats == "entrypage"


def test_closed_entry_level_application_does_not_emit():
    html = """
    <h2>Entry-Level Associate Opportunities</h2>
    <p>Applications are closed. Check back next year.</p>
    """
    assert _fetch(html) == []


def test_target_class_year_opportunity_is_strong_evidence():
    html = "<p>Class of 2027 Associate hiring information and requirements.</p>"
    assert len(_fetch(html)) == 1
    assert _fetch(html, years=(2028,)) == []


def test_target_year_summer_associate_is_not_a_3l_signal():
    html = """
    <h2>2027 Summer Associate Opportunities</h2>
    <p>Applications are open for 2L students. Apply now.</p>
    """
    assert _fetch(html) == []


def test_generic_student_marketing_page_does_not_emit():
    html = """
    <h1>Law Students</h1>
    <p>Learn about our people, culture, mentorship, and summer program.</p>
    <a href="/learn-more">Learn more</a>
    """
    assert _fetch(html) == []


def test_unrelated_apply_link_outside_entry_context_does_not_emit():
    filler = " ".join(["summer program details"] * 100)
    html = (
        "<h2>Entry-Level Recruiting</h2>"
        f"<p>{filler}</p>"
        "<a href='/apply-summer'>Apply now for the 2L summer program</a>"
    )
    assert _fetch(html) == []


def test_fingerprint_is_stable_and_changes_with_application_link():
    first = _fetch(
        "<p>3L hiring: applications are open.</p>"
        "<a href='/apply/a'>Apply now</a>"
    )[0]
    repeat = _fetch(
        "<p>3L hiring: applications are open.</p>"
        "<a href='/apply/a'>Apply now</a>"
    )[0]
    changed = _fetch(
        "<p>3L hiring: applications are open.</p>"
        "<a href='/apply/b'>Apply now</a>"
    )[0]
    assert first.job_id == repeat.job_id
    assert changed.job_id != first.job_id
