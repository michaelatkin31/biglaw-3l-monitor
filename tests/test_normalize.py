from core.normalize import (
    clean_text,
    normalize_greenhouse_job,
    normalize_lever_posting,
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
