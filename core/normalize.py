"""Map raw ATS payloads onto the normalized `Posting` shape.

Each function is pure (raw dict -> Posting) so it can be unit-tested against a
captured API fixture without any network. Fetchers own the network; normalize
owns the field mapping.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Posting

_WS = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """Collapse whitespace and unescape HTML entities from ATS strings."""
    if value is None:
        return ""
    text = html.unescape(str(value))
    return _WS.sub(" ", text).strip()


def _epoch_ms_to_iso(ms: Any) -> Optional[str]:
    try:
        return (
            datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
            .date()
            .isoformat()
        )
    except (TypeError, ValueError, OSError):
        return None


# --- Greenhouse ------------------------------------------------------------
# API: https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
# Each job: {id, title, location:{name}, absolute_url, updated_at, first_published}


def normalize_greenhouse_job(firm: str, job: dict) -> Posting:
    return Posting(
        firm=firm,
        job_id=str(job.get("id")),
        title=clean_text(job.get("title")),
        location=clean_text((job.get("location") or {}).get("name")),
        url=clean_text(job.get("absolute_url")),
        ats="greenhouse",
        posted_date=clean_text(job.get("first_published") or job.get("updated_at"))
        or None,
    )


# --- Lever -----------------------------------------------------------------
# API: https://api.lever.co/v0/postings/{company}?mode=json
# Each posting: {id, text, categories:{location, team, commitment}, hostedUrl, createdAt}


def normalize_lever_posting(firm: str, posting: dict) -> Posting:
    categories = posting.get("categories") or {}
    return Posting(
        firm=firm,
        job_id=str(posting.get("id")),
        title=clean_text(posting.get("text")),
        location=clean_text(categories.get("location")),
        url=clean_text(posting.get("hostedUrl") or posting.get("applyUrl")),
        ats="lever",
        posted_date=_epoch_ms_to_iso(posting.get("createdAt")),
    )


# --- Workday ---------------------------------------------------------------
# CXS jobs endpoint returns jobPostings: [{title, externalPath, locationsText,
# postedOn, bulletFields:[req id]}]. `base_url` is the public site root used to
# build the absolute apply URL from externalPath.


def normalize_workday_job(firm: str, job: dict, base_url: str) -> Posting:
    external_path = clean_text(job.get("externalPath"))
    bullet = job.get("bulletFields") or []
    job_id = clean_text(bullet[0]) if bullet else external_path
    url = ""
    if external_path:
        url = base_url.rstrip("/") + external_path
    return Posting(
        firm=firm,
        job_id=job_id or url,
        title=clean_text(job.get("title")),
        location=clean_text(job.get("locationsText")),
        url=url,
        ats="workday",
        posted_date=clean_text(job.get("postedOn")) or None,
    )


# --- Generic (schema.org JobPosting JSON-LD) -------------------------------


def normalize_jsonld_job(firm: str, obj: dict, page_url: str) -> Optional[Posting]:
    """Map a schema.org JobPosting JSON-LD object onto a Posting."""
    title = clean_text(obj.get("title"))
    if not title:
        return None
    url = clean_text(obj.get("url")) or page_url
    location = _jsonld_location(obj.get("jobLocation"))
    # Prefer an explicit identifier; fall back to the apply URL/title hash.
    identifier = obj.get("identifier")
    if isinstance(identifier, dict):
        job_id = clean_text(identifier.get("value"))
    else:
        job_id = clean_text(identifier)
    if not job_id:
        job_id = url or f"{firm}:{title}"
    return Posting(
        firm=firm,
        job_id=job_id,
        title=title,
        location=location,
        url=url,
        ats="generic",
        posted_date=clean_text(obj.get("datePosted")) or None,
    )


def _jsonld_location(loc: Any) -> str:
    if isinstance(loc, list):
        return ", ".join(filter(None, (_jsonld_location(x) for x in loc)))
    if isinstance(loc, dict):
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ]
            return clean_text(", ".join(str(p) for p in parts if p))
        return clean_text(loc.get("name") or addr)
    return clean_text(loc)
