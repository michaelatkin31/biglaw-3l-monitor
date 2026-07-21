"""Fetcher wiring tests with a mocked HttpClient (no network)."""

import pytest

from fetchers.ashby import AshbyFetcher
from fetchers.base import Firm
from fetchers.careerpage import CareerPageFetcher
from fetchers.greenhouse import GreenhouseFetcher
from fetchers.jsonapi import JsonApiFetcher
from fetchers.lever import LeverFetcher
from fetchers.radancy import RadancyFetcher
from fetchers.smartrecruiters import SmartRecruitersFetcher
from fetchers.virecruit import ViRecruitFetcher, parse_virecruit_html
from fetchers.workday import WorkdayFetcher


class FakeClient:
    def __init__(self, json_result=None, post_results=None, text_result=None):
        self._json = json_result
        self._post_results = list(post_results or [])
        self._text = text_result
        self.get_calls = []
        self.post_calls = []

    def get_json(self, url, **kw):
        self.get_calls.append((url, kw))
        return self._json

    def post_json(self, url, body, **kw):
        self.post_calls.append((url, body))
        return self._post_results.pop(0)

    def get_text(self, url, **kw):
        self.get_calls.append((url, kw))
        return self._text


_VIRECRUIT_HTML = """
<html><body>
<table id="ctl00_contentPlaceHolder_gridviewList">
  <tr class="even-row"><td>
    <h4>Corporate Associate</h4>
    <section class="sub-title">
      <h5>Office <span>Silicon Valley</span></h5>
      <h5>Practice Area <span>Mergers &amp; Acquisitions</span></h5>
      <h5>Date Posted <span>Jun 02, 2026</span></h5>
      <h5>Application Deadline <span>Jun 02, 2027</span></h5>
    </section>
  </td></tr>
  <tr class="odd-row"><td>
    <h4>Dallas Office - Litigation Associate</h4>
    <section class="sub-title">
      <h5>Office <span>Dallas</span></h5>
      <h5>Practice Area <span>Litigation</span></h5>
      <h5>Date Posted <span>May 11, 2026</span></h5>
    </section>
  </td></tr>
</table>
</body></html>
"""


def test_greenhouse_fetch():
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "First-Year Associate",
                "location": {"name": "NYC"},
                "absolute_url": "https://boards.greenhouse.io/f/jobs/1",
                "updated_at": "2026-07-01",
            }
        ]
    }
    client = FakeClient(json_result=payload)
    firm = Firm(name="F", ats_type="greenhouse", ats_identifier="ftoken")
    posts = GreenhouseFetcher(client).fetch(firm)
    assert len(posts) == 1
    assert posts[0].job_id == "1"
    assert posts[0].ats == "greenhouse"
    assert "ftoken" in client.get_calls[0][0]


def test_greenhouse_requires_identifier():
    firm = Firm(name="F", ats_type="greenhouse", ats_identifier=None)
    with pytest.raises(ValueError):
        GreenhouseFetcher(FakeClient({})).fetch(firm)


def test_lever_fetch():
    payload = [
        {
            "id": "x1",
            "text": "Entry-Level Associate",
            "categories": {"location": "Chicago"},
            "hostedUrl": "https://jobs.lever.co/f/x1",
            "createdAt": 1_700_000_000_000,
        }
    ]
    client = FakeClient(json_result=payload)
    firm = Firm(name="F", ats_type="lever", ats_identifier="fslug")
    posts = LeverFetcher(client).fetch(firm)
    assert len(posts) == 1
    assert posts[0].job_id == "x1"
    assert posts[0].ats == "lever"


def test_workday_pagination_and_pinned_host():
    # Two pages: total=3 with page size 20 would normally be one page, so use a
    # total larger than one page to force a second POST, then an empty page.
    page1 = {"total": 25, "jobPostings": [
        {"title": f"Job {i}", "externalPath": f"/job/{i}", "bulletFields": [f"R{i}"]}
        for i in range(20)
    ]}
    page2 = {"total": 25, "jobPostings": [
        {"title": f"Job {i}", "externalPath": f"/job/{i}", "bulletFields": [f"R{i}"]}
        for i in range(20, 25)
    ]}
    client = FakeClient(post_results=[page1, page2])
    firm = Firm(
        name="F", ats_type="workday", ats_identifier="tenant/site",
        options={"workday_host": "tenant.wd1.myworkdayjobs.com"},
    )
    posts = WorkdayFetcher(client).fetch(firm)
    assert len(posts) == 25
    # Host was pinned -> exactly one host tried, two POSTs (offsets 0 and 20).
    assert len(client.post_calls) == 2
    assert client.post_calls[0][1]["offset"] == 0
    assert client.post_calls[1][1]["offset"] == 20
    assert "wday/cxs/tenant/site/jobs" in client.post_calls[0][0]
    assert posts[0].url == "https://tenant.wd1.myworkdayjobs.com/en-US/site/job/0"


def test_workday_bad_identifier():
    firm = Firm(name="F", ats_type="workday", ats_identifier="no-slash")
    with pytest.raises(ValueError):
        WorkdayFetcher(FakeClient()).fetch(firm)


def test_careerpage_fetch():
    payload = {
        "totalCount": 1,
        "jobs": [
            {"data": {
                "title": "2026 Post-Clerkship Associate Attorney",
                "req_id": "5793",
                "apply_url": "https://x-mofo.icims.com/jobs/5793/login",
                "full_location": "New York, New York, United States",
            }}
        ],
    }
    client = FakeClient(json_result=payload)
    firm = Firm(name="MoFo", ats_type="careerpage", ats_identifier="mofo")
    posts = CareerPageFetcher(client).fetch(firm)
    assert len(posts) == 1
    assert posts[0].job_id == "5793"
    assert posts[0].ats == "careerpage"
    assert "mofo.career.page" in client.get_calls[0][0]


def test_careerpage_requires_identifier():
    firm = Firm(name="F", ats_type="careerpage", ats_identifier=None)
    with pytest.raises(ValueError):
        CareerPageFetcher(FakeClient({})).fetch(firm)


def test_smartrecruiters_fetch():
    payload = {
        "totalFound": 1,
        "content": [
            {
                "id": "744",
                "name": "Litigation Associate",
                "releasedDate": "2026-07-20T00:00:00.000Z",
                "location": {"fullLocation": "Washington, DC, USA"},
            }
        ],
    }
    client = FakeClient(json_result=payload)
    firm = Firm(name="Crowell", ats_type="smartrecruiters", ats_identifier="CrowellMoring")
    posts = SmartRecruitersFetcher(client).fetch(firm)
    assert len(posts) == 1
    assert posts[0].job_id == "744"
    assert posts[0].url == "https://jobs.smartrecruiters.com/CrowellMoring/744"
    assert posts[0].ats == "smartrecruiters"
    assert "companies/CrowellMoring/postings" in client.get_calls[0][0]


def test_smartrecruiters_empty_is_ok():
    # SmartRecruiters returns 200 + empty content when a board has no live jobs.
    client = FakeClient(json_result={"totalFound": 0, "content": []})
    firm = Firm(name="F", ats_type="smartrecruiters", ats_identifier="X")
    assert SmartRecruitersFetcher(client).fetch(firm) == []


def test_smartrecruiters_requires_identifier():
    firm = Firm(name="F", ats_type="smartrecruiters", ats_identifier=None)
    with pytest.raises(ValueError):
        SmartRecruitersFetcher(FakeClient({})).fetch(firm)


def test_virecruit_parse_html():
    jobs = parse_virecruit_html(_VIRECRUIT_HTML)
    assert len(jobs) == 2
    assert jobs[0] == {
        "title": "Corporate Associate",
        "office": "Silicon Valley",
        "practice": "Mergers & Acquisitions",
        "posted": "Jun 02, 2026",
    }
    # Title with an office prefix still parses via <h4>, office via <span>.
    assert jobs[1]["title"] == "Dallas Office - Litigation Associate"
    assert jobs[1]["office"] == "Dallas"


def test_virecruit_fetch():
    url = "https://ommcareers.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx"
    client = FakeClient(text_result=_VIRECRUIT_HTML)
    firm = Firm(name="O'Melveny", ats_type="virecruit", ats_identifier=url)
    posts = ViRecruitFetcher(client).fetch(firm)
    assert len(posts) == 2
    assert posts[0].title == "Corporate Associate"
    assert posts[0].location == "Silicon Valley"
    assert posts[0].url == url
    assert posts[0].ats == "virecruit"
    assert posts[0].job_id.startswith("vr-")
    # id is stable and deterministic across runs
    assert ViRecruitFetcher(FakeClient(text_result=_VIRECRUIT_HTML)).fetch(firm)[0].job_id == posts[0].job_id


def test_virecruit_requires_identifier():
    firm = Firm(name="F", ats_type="virecruit", ats_identifier=None)
    with pytest.raises(ValueError):
        ViRecruitFetcher(FakeClient(text_result="")).fetch(firm)


def test_virecruit_blocked_page_raises():
    # A blocked/login page has no gridviewList table -> surfaced as a failure,
    # not silently treated as "zero jobs".
    firm = Firm(name="F", ats_type="virecruit", ats_identifier="http://x")
    with pytest.raises(RuntimeError):
        ViRecruitFetcher(FakeClient(text_result="<html>login</html>")).fetch(firm)


_RADANCY_HTML = """
<ul class="jobs-list">
  <li><a href="/en/job/new-york/associate-m-and-a/3392/111">Associate, M&amp;A</a></li>
  <li><a href="/en/job/belfast/associate-corporate/3392/222"><span>Associate - Corporate</span></a></li>
</ul>
"""


def test_radancy_fetch_and_paginate_stop():
    client = FakeClient(text_result=_RADANCY_HTML)
    firm = Firm(name="A&O Shearman", ats_type="radancy",
                ats_identifier="https://careers.aoshearman.com/en/search-jobs/Associate")
    posts = RadancyFetcher(client).fetch(firm)
    assert len(posts) == 2  # deduped; page 2 repeats page 1 -> loop stops
    assert posts[0].job_id == "111"
    assert posts[0].location == "New York"
    assert posts[0].url.endswith("/en/job/new-york/associate-m-and-a/3392/111")
    assert posts[0].ats == "radancy"
    # ?p=N pagination param is appended
    assert "p=1" in client.get_calls[0][0]


def test_radancy_requires_identifier():
    firm = Firm(name="F", ats_type="radancy", ats_identifier=None)
    with pytest.raises(ValueError):
        RadancyFetcher(FakeClient(text_result="")).fetch(firm)


def test_ashby_fetch_skips_unlisted():
    payload = {"jobs": [
        {"id": "a1", "title": "Healthcare Associate", "location": "Columbus",
         "jobUrl": "https://jobs.ashbyhq.com/barnes/a1", "isListed": True,
         "address": {"postalAddress": {"addressRegion": "OH"}}},
        {"id": "a2", "title": "Hidden Role", "location": "NY", "isListed": False},
    ]}
    client = FakeClient(json_result=payload)
    firm = Firm(name="Barnes", ats_type="ashby", ats_identifier="barnes")
    posts = AshbyFetcher(client).fetch(firm)
    assert len(posts) == 1  # unlisted dropped
    assert posts[0].job_id == "a1"
    assert posts[0].location == "Columbus, OH"
    assert posts[0].ats == "ashby"
    assert "job-board/barnes" in client.get_calls[0][0]


def test_ashby_requires_identifier():
    firm = Firm(name="F", ats_type="ashby", ats_identifier=None)
    with pytest.raises(ValueError):
        AshbyFetcher(FakeClient({})).fetch(firm)


def test_jsonapi_autodetects_nested_array_and_keys():
    # nested jobs array + mixed key names (Ropes-style)
    payload = {"total": 2, "results": [
        {"title": "Lateral Associate (3-4 years)", "location": "New York",
         "url": "/positions/lateral-associate-ny", "id": "r1"},
        {"title": "Funds Attorney", "location": "Boston", "url": "/positions/funds-boston", "id": "r2"},
    ]}
    client = FakeClient(json_result=payload)
    firm = Firm(name="Ropes", ats_type="jsonapi",
                ats_identifier="https://www.ropesgrayrecruiting.com/sitecore/api/jobsearch")
    posts = JsonApiFetcher(client).fetch(firm)
    assert len(posts) == 2
    assert posts[0].title == "Lateral Associate (3-4 years)"
    assert posts[0].location == "New York"
    assert posts[0].url == "https://www.ropesgrayrecruiting.com/positions/lateral-associate-ny"
    assert posts[0].ats == "jsonapi"


def test_jsonapi_ignores_non_job_json():
    # a content/analytics payload with no title-bearing list -> error, not garbage
    client = FakeClient(json_result={"articles": [{"headline": "News", "date": "2026"}], "settings": {}})
    firm = Firm(name="X", ats_type="jsonapi", ats_identifier="http://x/api")
    with pytest.raises(RuntimeError):
        JsonApiFetcher(client).fetch(firm)


def test_jsonapi_requires_identifier():
    firm = Firm(name="F", ats_type="jsonapi", ats_identifier=None)
    with pytest.raises(ValueError):
        JsonApiFetcher(FakeClient({})).fetch(firm)


def test_careerpage_accepts_full_url():
    # Jibe self-hosted front-end (e.g. Ogletree) passes a full /api/jobs URL.
    payload = {"totalCount": 1, "jobs": [
        {"data": {"title": "2027 2L Summer Associate", "req_id": "6728",
                  "apply_url": "https://x.icims.com/jobs/6728/login",
                  "full_location": "Seattle, Washington"}}]}
    client = FakeClient(json_result=payload)
    firm = Firm(name="Ogletree", ats_type="careerpage",
                ats_identifier="https://careers.ogletree.com/api/jobs")
    posts = CareerPageFetcher(client).fetch(firm)
    assert len(posts) == 1
    assert client.get_calls[0][0] == "https://careers.ogletree.com/api/jobs"
