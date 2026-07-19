"""Generic HTML fallback fetcher.

Used only for firms not on Greenhouse/Lever/Workday. Strategy, cheapest first:

  1. Lightweight HTML GET of `careers_url`, then extract schema.org JobPosting
     objects from <script type="application/ld+json"> blocks. Many careers CMSs
     (and every ATS that cares about Google for Jobs SEO) embed these.
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
from core.normalize import normalize_jsonld_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


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

        postings = extract_jsonld_jobs(firm.name, html, firm.careers_url)
        if not postings:
            log.debug(
                "%s: generic fetch found no JSON-LD JobPosting at %s",
                firm.name,
                firm.careers_url,
            )
        return postings

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
