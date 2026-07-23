"""Generic HTML fallback fetcher.

Used only for firms not on Greenhouse/Lever/Workday. Strategy, cheapest first:

  1. Lightweight HTML GET of `careers_url`, then extract schema.org JobPosting
     objects from the page. Two encodings are read, both standard and both common
     on careers CMSs that care about Google-for-Jobs SEO:
       a. <script type="application/ld+json"> blocks (JSON-LD).
       b. Inline microdata (itemtype="https://schema.org/JobPosting" +
          itemprop="title"/"datePosted"/... on the rendered job cards). Some
          WordPress/CMS careers front-ends (e.g. Kilpatrick) render the openings
          as microdata cards with no JSON-LD and no public JSON API, but are still
          plain server-rendered HTML -- so no browser is needed.
  2. Only if the page is truly JS-rendered AND the firm sets `render: playwright`
     in firms.yaml do we fall back to Playwright. Playwright is an optional
     dependency; keep this set as small as possible so the whole thing still runs
     in GitHub Actions.

If nothing is found we return [] and log -- we never crash the run.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.models import Posting
from core.normalize import clean_description, clean_text, normalize_jsonld_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

# Content-region hints for a job detail page, tried in order. Slicing to the main
# content before stripping avoids picking up experience-like noise from nav/footer.
_CONTENT_REGION = re.compile(
    r'<(?:main|article)\b[^>]*>(.*?)</(?:main|article)>'
    r'|<div[^>]*class="[^"]*(?:entry-content|job-description|posting|single-job)[^"]*"[^>]*>(.*)',
    re.IGNORECASE | re.DOTALL,
)


def _detail_text(html: str) -> str:
    """Best-effort plain text of a job detail page for the experience gate."""
    if not html:
        return ""
    m = _CONTENT_REGION.search(html)
    region = next((g for g in m.groups() if g), html) if m else html
    return clean_description(region)

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

# --- schema.org microdata (itemscope/itemprop) ----------------------------
_MICRODATA_JOB_RE = re.compile(
    r'itemtype=["\']https?://schema\.org/JobPosting["\']', re.IGNORECASE
)
# Links that are share/apply-elsewhere chrome, not the job's own canonical URL.
_SOCIAL_HREF_RE = re.compile(
    r'linkedin\.com/share|twitter\.com/share|x\.com/share|facebook\.com/(?:share|sharer)'
    r'|/sharer|mailto:|^javascript:|addthis|whatsapp|t\.me/share|reddit\.com/submit',
    re.IGNORECASE,
)
_TAGS_RE = re.compile(r"<[^>]+>")


def _microdata_prop(scope: str, name: str) -> str:
    """First value of itemprop=`name` within `scope` -- content attr or inner text."""
    m = re.search(
        rf'itemprop=["\']{name}["\'][^>]*\bcontent=["\']([^"\']*)["\']', scope, re.IGNORECASE
    )
    if m:
        return clean_text(m.group(1))
    m = re.search(rf'itemprop=["\']{name}["\'][^>]*>(.*?)</', scope, re.IGNORECASE | re.DOTALL)
    return clean_text(_TAGS_RE.sub(" ", m.group(1))) if m else ""


def _microdata_url(scope: str) -> str:
    """The job's own URL: an explicit itemprop=url, else the first non-share link."""
    m = re.search(
        r'itemprop=["\']url["\'][^>]*\bhref=["\']([^"\']+)["\']', scope, re.IGNORECASE
    )
    if m:
        return clean_text(m.group(1))
    for m in re.finditer(r'<a\b[^>]*\bhref=["\'](https?://[^"\']+)["\']', scope, re.IGNORECASE):
        href = m.group(1)
        if not _SOCIAL_HREF_RE.search(href):
            return clean_text(href)
    return ""


def extract_microdata_jobs(firm_name: str, html: str, page_url: str) -> list[Posting]:
    """Extract schema.org JobPosting *microdata* cards from server-rendered HTML.

    Cards are delimited by successive JobPosting itemtype markers (they render as
    sibling elements); each card's title/datePosted/url are read from the first
    matching itemprop after its marker. Location is only captured when the card
    exposes it as microdata -- otherwise blank, which the US-only geo gate treats
    as ambiguous and keeps (recall-safe).
    """
    markers = [m.start() for m in _MICRODATA_JOB_RE.finditer(html)]
    if not markers:
        return []
    postings: list[Posting] = []
    for i, start in enumerate(markers):
        end = markers[i + 1] if i + 1 < len(markers) else len(html)
        scope = html[start:end]
        title = _microdata_prop(scope, "title") or _microdata_prop(scope, "name")
        if not title:
            continue
        url = _microdata_url(scope) or page_url
        location = _microdata_prop(scope, "jobLocation") or _microdata_prop(
            scope, "addressLocality"
        )
        posted = _microdata_prop(scope, "datePosted")
        description = _microdata_prop(scope, "description")
        # A per-job URL is a stable id; fall back to firm+title when the card
        # has no link of its own (so every card still dedups sensibly).
        job_id = url if url != page_url else f"{firm_name}:{title}"
        postings.append(
            Posting(
                firm=firm_name,
                job_id=job_id,
                title=title,
                location=location,
                url=url,
                ats="generic",
                posted_date=posted or None,
                description=description,
            )
        )
    return postings


def _iter_jobposting_objects(payload: Any):
    """Yield every dict whose @type is (or includes) JobPosting, recursively."""
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_jobposting_objects(item)
    elif isinstance(payload, dict):
        at_type = payload.get("@type")
        types = at_type if isinstance(at_type, list) else [at_type]
        if any(t == "JobPosting" for t in types):
            yield payload
        # Recurse into common containers (@graph, itemListElement, etc.).
        for value in payload.values():
            if isinstance(value, (list, dict)):
                yield from _iter_jobposting_objects(value)


def extract_jsonld_jobs(firm_name: str, html: str, page_url: str) -> list[Posting]:
    postings: list[Posting] = []
    for block in _JSONLD_RE.findall(html):
        block = block.strip()
        if not block:
            continue
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for obj in _iter_jobposting_objects(payload):
            posting = normalize_jsonld_job(firm_name, obj, page_url)
            if posting is not None:
                postings.append(posting)
    return postings


class GenericFetcher(Fetcher):
    ats_type = "generic"

    def fetch(self, firm: Firm) -> list[Posting]:
        if not firm.careers_url:
            raise ValueError(f"{firm.name}: generic fetcher requires careers_url")

        if firm.options.get("render") == "playwright":
            html = self._render_with_playwright(firm.careers_url)
        else:
            html = self.client.get_text(firm.careers_url) or ""

        # JSON-LD first (richest); then microdata cards. Dedup across both by the
        # per-job URL (falling back to the job_id, which is firm+title when a card
        # carries no link), since a page can carry the same job in both encodings.
        postings = extract_jsonld_jobs(firm.name, html, firm.careers_url)
        seen = {p.url for p in postings if p.url and p.url != firm.careers_url}
        for p in extract_microdata_jobs(firm.name, html, firm.careers_url):
            key = p.url if (p.url and p.url != firm.careers_url) else p.job_id
            if key in seen:
                continue
            seen.add(key)
            postings.append(p)
        if not postings:
            log.debug(
                "%s: generic fetch found no JSON-LD or microdata JobPosting at %s",
                firm.name,
                firm.careers_url,
            )

        # Opt-in per firm: microdata/JSON-LD listings often omit the description
        # (e.g. Kilpatrick's cards carry only title + datePosted), but the linked
        # detail page states the experience requirement. When `fetch_description`
        # is set, fetch each distinct detail page and attach its text so the
        # filter's experience gate can act on it. Bounded by `description_limit`
        # (default 40) to cap per-run requests; a failed/slow fetch just leaves
        # the description empty (gate stays inactive for that posting).
        if firm.options.get("fetch_description"):
            postings = self._attach_descriptions(firm, postings)
        return postings

    def _attach_descriptions(self, firm: Firm, postings: list[Posting]) -> list[Posting]:
        import dataclasses

        limit = int(firm.options.get("description_limit", 40))
        cache: dict[str, str] = {}
        out: list[Posting] = []
        for i, p in enumerate(postings):
            desc = p.description
            url = p.url
            if not desc and url and url.startswith("http") and url != firm.careers_url and i < limit:
                if url not in cache:
                    try:
                        cache[url] = _detail_text(self.client.get_text(url) or "")
                    except Exception as e:  # noqa: BLE001 - detail fetch is best-effort
                        log.debug("%s: detail fetch failed for %s (%s)", firm.name, url, e)
                        cache[url] = ""
                desc = cache[url]
            out.append(dataclasses.replace(p, description=desc) if desc else p)
        return out

    def _render_with_playwright(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover - optional dep
            raise RuntimeError(
                "Firm requires Playwright rendering but playwright is not "
                "installed. `pip install playwright && playwright install chromium`"
            ) from e
        with sync_playwright() as p:  # pragma: no cover - needs a browser
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=self.client.session.headers["User-Agent"])
                page.goto(url, wait_until="networkidle", timeout=int(self.client.timeout * 1000))
                return page.content()
            finally:
                browser.close()
