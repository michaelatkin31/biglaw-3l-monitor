"""Map raw ATS payloads onto the normalized `Posting` shape.

Each function is pure (raw dict -> Posting) so it can be unit-tested against a
captured API fixture without any network. Fetchers own the network; normalize
owns the field mapping.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Posting

_WS = re.compile(r"\s+")
_HTML_TAG = re.compile(r"<[^>]+>")
_DESC_MAX = 6000  # plenty to catch an experience requirement; keeps memory bounded


def _unwrap_rendered(value: Any) -> Any:
    """Unwrap WordPress WP-JSON `{'rendered': '...'}` wrappers.

    Firms on a WP-JSON board (e.g. Dinsmore) return title/content as
    `{'rendered': 'Public Finance Associate', 'protected': false}`; without this
    the whole dict would be str()'d into the field.
    """
    if isinstance(value, dict) and "rendered" in value:
        return value["rendered"]
    return value


def clean_text(value: Any) -> str:
    """Collapse whitespace and unescape HTML entities from ATS strings."""
    value = _unwrap_rendered(value)
    if value is None:
        return ""
    text = html.unescape(str(value))
    return _WS.sub(" ", text).strip()


# Titles occasionally arrive prefixed with a req/order number ("1029 - Corporate
# Associate"); strip a leading "<digits> - " so the digest reads cleanly.
_REQ_PREFIX = re.compile(r"^\d{2,}\s*[-–—:]\s*(?=\D)")


def clean_title(value: Any) -> str:
    return _REQ_PREFIX.sub("", clean_text(value))


def clean_description(value: Any) -> str:
    """Cleaned plain text of a job description: unwrap, unescape, strip HTML,
    collapse whitespace, and truncate. Empty string when unavailable.

    Unescape happens BEFORE tag-stripping so entity-encoded markup (Greenhouse
    returns `&lt;p&gt;...&lt;/p&gt;`) is reduced to text, not left as literal
    angle-bracket tags.
    """
    value = _unwrap_rendered(value)
    if value is None:
        return ""
    text = _HTML_TAG.sub(" ", html.unescape(str(value)))
    return _WS.sub(" ", text).strip()[:_DESC_MAX]


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
        description=clean_description(job.get("content")),
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
        description=clean_description(
            posting.get("descriptionPlain") or posting.get("description")
        ),
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


# --- career.page (Jibe/Phenom SEO front-end; e.g. Morrison & Foerster) ------
# API: https://{sub}.career.page/api/jobs?limit=N&offset=M
# Returns {jobs: [{data: {title, req_id/slug, apply_url, full_location, city,
# state, country, posted_date, create_date, categories:[{name}]}}], totalCount}.
# Useful because it exposes an iCIMS-backed board as clean public JSON.


def normalize_careerpage_job(firm: str, job: dict) -> Optional[Posting]:
    data = job.get("data") if isinstance(job.get("data"), dict) else job
    title = clean_text(data.get("title"))
    if not title:
        return None
    location = clean_text(data.get("full_location")) or clean_text(
        ", ".join(
            str(p)
            for p in (data.get("city"), data.get("state"), data.get("country"))
            if p
        )
    )
    job_id = clean_text(data.get("req_id") or data.get("slug")) or clean_text(
        data.get("apply_url")
    )
    return Posting(
        firm=firm,
        job_id=job_id or f"{firm}:{title}",
        title=title,
        location=location,
        url=clean_text(data.get("apply_url")),
        ats="careerpage",
        posted_date=clean_text(data.get("posted_date") or data.get("create_date"))
        or None,
        description=clean_description(
            data.get("description") or data.get("job_description") or data.get("body")
        ),
    )


# --- Ashby -----------------------------------------------------------------
# API: https://api.ashbyhq.com/posting-api/job-board/{org}
# jobs: [{id, title, location, jobUrl, applyUrl, publishedAt, isListed, address}]


def normalize_ashby_job(firm: str, job: dict) -> Optional[Posting]:
    if job.get("isListed") is False:
        return None
    title = clean_text(job.get("title"))
    if not title:
        return None
    location = clean_text(job.get("location"))
    addr = (job.get("address") or {}).get("postalAddress") or {}
    region = clean_text(addr.get("addressRegion"))
    if region and region.lower() not in location.lower():
        location = f"{location}, {region}" if location else region
    return Posting(
        firm=firm,
        job_id=clean_text(job.get("id")) or clean_text(job.get("jobUrl")),
        title=title,
        location=location,
        url=clean_text(job.get("jobUrl") or job.get("applyUrl")),
        ats="ashby",
        posted_date=clean_text(job.get("publishedAt")) or None,
        description=clean_description(
            job.get("descriptionPlain") or job.get("description")
        ),
    )


# --- SmartRecruiters -------------------------------------------------------
# API: https://api.smartrecruiters.com/v1/companies/{company}/postings
# Each posting: {id, name, refNumber, releasedDate, location:{city, region,
# country, fullLocation}}. Public apply URL: jobs.smartrecruiters.com/{company}/{id}


def normalize_smartrecruiters_posting(firm: str, posting: dict, company: str) -> Posting:
    loc = posting.get("location") or {}
    location = clean_text(loc.get("fullLocation")) or clean_text(
        ", ".join(
            str(p)
            for p in (loc.get("city"), loc.get("region"), loc.get("country"))
            if p
        )
    )
    job_id = clean_text(posting.get("id")) or clean_text(posting.get("refNumber"))
    url = f"https://jobs.smartrecruiters.com/{company}/{job_id}" if job_id else ""
    return Posting(
        firm=firm,
        job_id=job_id or url or f"{firm}:{clean_text(posting.get('name'))}",
        title=clean_text(posting.get("name")),
        location=location,
        url=url,
        ats="smartrecruiters",
        posted_date=clean_text(posting.get("releasedDate")) or None,
    )


# --- viRecruit / viGlobal (vi by Aderant) ----------------------------------
# The public "self-apply" listing page (viRecruitSelfApply/RecDefault.aspx) is a
# server-rendered ASP.NET GridView. There is no per-job numeric id and no
# per-job URL (Apply is a __doPostBack), so we synthesize a stable id from the
# firm + title + office + posted date and link to the listing page itself.


def normalize_virecruit_job(firm: str, job: dict, listing_url: str) -> Optional[Posting]:
    title = clean_text(job.get("title"))
    if not title:
        return None
    office = clean_text(job.get("office"))
    posted = clean_text(job.get("posted")) or None
    raw = f"{firm}|{title}|{office}|{posted}".lower()
    job_id = "vr-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return Posting(
        firm=firm,
        job_id=job_id,
        title=title,
        location=office,
        url=listing_url,
        ats="virecruit",
        posted_date=posted,
    )


# --- Radancy / TalentBrew (e.g. A&O Shearman) ------------------------------
# The search-jobs page is server-rendered HTML; each job is an anchor
#   <a href="/en/job/{city}/{slug}/{orgid}/{jobid}">Title</a>
# City (for the geo filter) and a stable numeric job id come straight from the href.

_RADANCY_HREF = re.compile(r"/job/([^/]+)/[^/]+/\d+/(\d+)")


def normalize_radancy_job(firm: str, href: str, title: str, origin: str) -> Optional[Posting]:
    title = clean_text(title)
    if not title:
        return None
    m = _RADANCY_HREF.search(href or "")
    city = m.group(1).replace("-", " ").title() if m else ""
    job_id = m.group(2) if m else clean_text(href)
    url = origin.rstrip("/") + href if href.startswith("/") else href
    return Posting(
        firm=firm,
        job_id=job_id or f"{firm}:{title}",
        title=title,
        location=city,
        url=url,
        ats="radancy",
        posted_date=None,
    )


# --- Generic JSON API (auto-detecting; many custom firm career APIs) --------
# Firms expose wildly different JSON shapes (Ropes /results, Orrick /jobs, Vedder
# Next.js /pageProps/posts, Flo /public-jobs, ClearCompany, Paycom, UltiPro, ...).
# We auto-locate the jobs array and map fields by key name so one fetcher covers all.

_JOB_TITLE_KEYS = ("title", "jobtitle", "job_title", "positiontitle", "postingtitle",
                   "requisitiontitle", "name")
_JOB_LOC_KEYS = ("location", "locations", "city", "office", "offices", "locationstext",
                 "region", "joblocation", "full_location", "primarylocation", "fulllocation")
_JOB_URL_KEYS = ("applyurl", "joburl", "apply_url", "url", "hostedurl", "absolute_url",
                 "jobposturl", "externalpath", "link", "detailurl")
_JOB_ID_KEYS = ("id", "jobid", "job_id", "itemid", "reqid", "requisitionid",
                "postingid", "slug", "ref", "refnumber")
_JOB_DESC_KEYS = ("description", "descriptionplain", "jobdescription", "job_description",
                  "content", "body", "summary", "jobsummary", "text", "details",
                  "descriptionhtml", "public_description")


def _first_key(item: dict, keys) -> Any:
    lower = {k.lower(): k for k in item}
    for want in keys:
        if want in lower:
            return item[lower[want]]
    return None


def _stringify_loc(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return clean_text(v)
    if isinstance(v, dict):
        for k in ("name", "city", "label", "fullLocation", "displayName", "text"):
            if v.get(k):
                return clean_text(v[k])
        return clean_text(", ".join(str(x) for x in v.values() if isinstance(x, (str, int))))
    if isinstance(v, list):
        return ", ".join(filter(None, (_stringify_loc(x) for x in v)))[:120]
    return clean_text(str(v))


def find_job_array(data: Any, depth: int = 0):
    """Walk arbitrary JSON to find a list of job dicts (title + a location/url/id)."""
    if depth > 8:
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        it = data[0]
        if "data" in {k.lower() for k in it} and isinstance(_first_key(it, ("data",)), dict):
            inner = _first_key(it, ("data",))
            if _first_key(inner, _JOB_TITLE_KEYS):
                return [d.get("data", d) for d in data if isinstance(d, dict)]
        if _first_key(it, _JOB_TITLE_KEYS) and (
            _first_key(it, _JOB_LOC_KEYS) is not None
            or _first_key(it, _JOB_URL_KEYS) is not None
            or _first_key(it, _JOB_ID_KEYS) is not None
        ):
            return data
    if isinstance(data, dict):
        for v in data.values():
            r = find_job_array(v, depth + 1)
            if r:
                return r
    if isinstance(data, list):
        for x in data[:4]:
            r = find_job_array(x, depth + 1)
            if r:
                return r
    return None


def normalize_jsonapi_item(firm: str, item: dict, base_url: str) -> Optional[Posting]:
    title = clean_title(_first_key(item, _JOB_TITLE_KEYS))
    if not title:
        return None
    location = _stringify_loc(_unwrap_rendered(_first_key(item, _JOB_LOC_KEYS)))
    raw_url = _unwrap_rendered(_first_key(item, _JOB_URL_KEYS))
    url = ""
    if isinstance(raw_url, str) and raw_url:
        url = raw_url if raw_url.startswith("http") else _join_url(base_url, raw_url)
    jid = _first_key(item, _JOB_ID_KEYS)
    job_id = clean_text(jid) if jid not in (None, "") else (url or f"{firm}:{title}")
    return Posting(
        firm=firm,
        job_id=job_id,
        title=title,
        location=location,
        url=url or base_url,
        ats="jsonapi",
        posted_date=clean_text(_first_key(item, ("posteddate", "posted_date", "datePosted",
                                                 "createdat", "publishedat", "postedon"))) or None,
        description=clean_description(_first_key(item, _JOB_DESC_KEYS)),
    )


def _join_url(base: str, path: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base, path)


def parse_rss_jobs(firm: str, xml_text: str) -> list[Posting]:
    """Parse an RSS/Atom job feed (e.g. eArcu / Reed Smith) into Postings."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    posts: list[Posting] = []
    for item in root.iter():
        if not item.tag.endswith("item") and not item.tag.endswith("entry"):
            continue
        title = link = guid = ""
        for ch in item:
            tag = ch.tag.split("}")[-1].lower()
            if tag == "title":
                title = clean_text(ch.text)
            elif tag == "link":
                link = clean_text(ch.text) or clean_text(ch.get("href"))
            elif tag in ("guid", "id"):
                guid = clean_text(ch.text)
        if not title:
            continue
        posts.append(Posting(firm=firm, job_id=guid or link or f"{firm}:{title}",
                             title=title, location="", url=link or guid,
                             ats="jsonapi", posted_date=None))
    return posts


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
        description=clean_description(obj.get("description")),
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
