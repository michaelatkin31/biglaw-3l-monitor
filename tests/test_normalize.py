from core.normalize import (
    clean_text,
    normalize_careerpage_job,
    normalize_greenhouse_job,
    normalize_lever_posting,
    normalize_smartrecruiters_posting,
    normalize_virecruit_job,
    normalize_workday_job,
)


def test_clean_text():
    assert clean_text("  Hello\n\tWorld ") == "Hello World"
    assert clean_text("Ben &amp; Jerry") == "Ben & Jerry"
    assert clean_text(None) == ""


def test_greenhouse():
    job = {
        "id": 12345,
        "title": "First-Year Associate",
        "location": {"name": "New York, NY"},
        "absolute_url": "https://boards.greenhouse.io/firm/jobs/12345",
        "updated_at": "2026-07-01T00:00:00Z",
        "first_published": "2026-06-15T00:00:00Z",
    }
    p = normalize_greenhouse_job("Firm", job)
    assert p.job_id == "12345"
    assert p.title == "First-Year Associate"
    assert p.location == "New York, NY"
    assert p.ats == "greenhouse"
    assert p.posted_date == "2026-06-15T00:00:00Z"


def test_lever_epoch():
    posting = {
        "id": "abc-123",
        "text": "Entry-Level Associate",
        "categories": {"location": "Chicago"},
        "hostedUrl": "https://jobs.lever.co/firm/abc-123",
        "createdAt": 1_700_000_000_000,
    }
    p = normalize_lever_posting("Firm", posting)
    assert p.job_id == "abc-123"
    assert p.location == "Chicago"
    assert p.posted_date == "2023-11-14"  # ms epoch -> ISO date
    assert p.ats == "lever"


def test_careerpage_job():
    job = {
        "data": {
            "title": "2026 Post-Clerkship Associate Attorney",
            "req_id": "5793",
            "slug": "5793",
            "apply_url": "https://lateralattorney-mofo.icims.com/jobs/5793/login",
            "full_location": "New York, New York, United States",
            "city": "New York",
            "state": "New York",
            "country": "United States",
            "posted_date": "2026-02-24T17:08:00+0000",
            "create_date": "2026-02-20T00:00:00+0000",
        }
    }
    p = normalize_careerpage_job("MoFo", job)
    assert p.job_id == "5793"
    assert p.title == "2026 Post-Clerkship Associate Attorney"
    assert p.location == "New York, New York, United States"
    assert p.url == "https://lateralattorney-mofo.icims.com/jobs/5793/login"
    assert p.ats == "careerpage"
    assert p.posted_date == "2026-02-24T17:08:00+0000"


def test_careerpage_job_no_title_is_dropped():
    assert normalize_careerpage_job("MoFo", {"data": {"req_id": "1"}}) is None


def test_smartrecruiters_posting():
    posting = {
        "id": "744000138709871",
        "name": "Litigation Associate",
        "refNumber": "REF1",
        "releasedDate": "2026-07-20T18:23:13.590Z",
        "location": {
            "city": "Washington",
            "region": "DC",
            "country": "us",
            "fullLocation": "Washington, DC, USA",
        },
    }
    p = normalize_smartrecruiters_posting("Crowell & Moring", posting, "CrowellMoring")
    assert p.job_id == "744000138709871"
    assert p.title == "Litigation Associate"
    assert p.location == "Washington, DC, USA"
    assert p.url == "https://jobs.smartrecruiters.com/CrowellMoring/744000138709871"
    assert p.ats == "smartrecruiters"
    assert p.posted_date == "2026-07-20T18:23:13.590Z"


def test_virecruit_job():
    listing = "https://ommcareers.viglobalcloud.com/viRecruitSelfApply/RecDefault.aspx"
    job = {"title": "Corporate Associate", "office": "Silicon Valley",
           "practice": "M&A", "posted": "Jun 02, 2026"}
    p = normalize_virecruit_job("O'Melveny", job, listing)
    assert p.title == "Corporate Associate"
    assert p.location == "Silicon Valley"
    assert p.url == listing
    assert p.ats == "virecruit"
    assert p.posted_date == "Jun 02, 2026"
    assert p.job_id.startswith("vr-")
    # deterministic id from firm+title+office+posted
    assert normalize_virecruit_job("O'Melveny", dict(job), listing).job_id == p.job_id
    # a different office => a different id (distinct multi-office rows)
    job2 = dict(job, office="New York")
    assert normalize_virecruit_job("O'Melveny", job2, listing).job_id != p.job_id


def test_virecruit_job_no_title_dropped():
    assert normalize_virecruit_job("F", {"office": "NY"}, "http://x") is None


def test_workday_url_and_id():
    job = {
        "title": "2027 Associate",
        "externalPath": "/job/New-York/2027-Associate_R-123",
        "locationsText": "New York",
        "postedOn": "Posted 5 Days Ago",
        "bulletFields": ["R-123"],
    }
    base = "https://firm.wd1.myworkdayjobs.com/en-US/firmcareers"
    p = normalize_workday_job("Firm", job, base)
    assert p.job_id == "R-123"
    assert p.url == base + "/job/New-York/2027-Associate_R-123"
    assert p.posted_date == "Posted 5 Days Ago"


def test_workday_falls_back_to_path_when_no_bullet():
    job = {"title": "X", "externalPath": "/job/abc", "bulletFields": []}
    p = normalize_workday_job("Firm", job, "https://h/en-US/s")
    assert p.job_id == "/job/abc"
