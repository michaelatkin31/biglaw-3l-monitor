"""Fetcher wiring tests with a mocked HttpClient (no network)."""

import pytest

from fetchers.base import Firm
from fetchers.careerpage import CareerPageFetcher
from fetchers.greenhouse import GreenhouseFetcher
from fetchers.lever import LeverFetcher
from fetchers.smartrecruiters import SmartRecruitersFetcher
from fetchers.workday import WorkdayFetcher


class FakeClient:
    def __init__(self, json_result=None, post_results=None):
        self._json = json_result
        self._post_results = list(post_results or [])
        self.get_calls = []
        self.post_calls = []

    def get_json(self, url, **kw):
        self.get_calls.append((url, kw))
        return self._json

    def post_json(self, url, body, **kw):
        self.post_calls.append((url, body))
        return self._post_results.pop(0)


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
