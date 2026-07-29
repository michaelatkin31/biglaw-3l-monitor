"""Monitor recruiting landing pages for an actually open entry-level path.

Firm recruiting pages are often not ATS job boards. They may expose a resume
collect, a "3L hiring" application, or a target-class opportunity directly in
ordinary page copy. This fetcher emits a synthetic Posting only when an explicit
entry-level signal is paired with open-application evidence (or a target class
year), avoiding alerts for generic evergreen law-student marketing pages.
"""

from __future__ import annotations

import hashlib
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from core.models import Posting
from core.normalize import clean_text

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_ENTRY = re.compile(
    r"\b(?:"
    r"entry[- ]level(?:\s+associate)?(?:\s+(?:opportunities|recruiting|candidates))?"
    r"|3l(?:\s+(?:hiring|candidate|opportunit\w*))?"
    r"|first[- ]year\s+associates?(?:\s+candidates)?"
    r"|incoming\s+associates?"
    r"|new\s+associates?"
    r")\b",
    re.IGNORECASE,
)
_OPEN = re.compile(
    r"\b(?:"
    r"(?:to\s+)?apply(?:\s+(?:now|here|online|for))?"
    r"|submit\s+(?:your\s+)?(?:application|materials|resume)"
    r"|resume\s+collect"
    r"|applications?\s+(?:are\s+)?(?:now\s+)?open"
    r"|accepting\s+applications?"
    r"|current\s+(?:openings|opportunities)"
    r"|view\s+(?:openings|opportunities)"
    r"|application\s+portal"
    r")\b",
    re.IGNORECASE,
)
_CLOSED = re.compile(
    r"\b(?:"
    r"applications?\s+(?:are\s+)?closed"
    r"|not\s+currently\s+accepting"
    r"|no\s+(?:current\s+)?(?:openings|positions|opportunities)"
    r"|check\s+back"
    r")\b",
    re.IGNORECASE,
)
_SUMMER = re.compile(
    r"\b(?:1l|2l|summer)\b.{0,30}\b(?:associate|program|clerk|opportunit\w*)\b",
    re.IGNORECASE,
)
_ENTRY_ACTION = re.compile(
    r"\b(?:candidates?|opportunit\w*|3l|hiring|apply|application)\b",
    re.IGNORECASE,
)
_NON_3L_ENTRY = re.compile(r"\b(?:judicial\s+clerk|post[- ]clerkship)\b", re.IGNORECASE)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        if self._href is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean_text(" ".join(self._anchor_text))))
            self._href = None
            self._anchor_text = []


class EntryPageFetcher(Fetcher):
    ats_type = "entrypage"

    def __init__(self, client, target_years: list[int] | set[int] | None = None) -> None:
        super().__init__(client)
        self.target_years = {int(year) for year in (target_years or [])}

    def _get_html(self, url: str, render: bool) -> str:
        if not render:
            return self.client.get_text(url) or ""

        # Reuse the browser fetcher's lazy Chromium instance so a monitor run
        # launches at most one browser even when several pages need rendering.
        from .browser import BrowserFetcher

        ctx = BrowserFetcher._page(BrowserFetcher(self.client))
        try:
            page = ctx.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            status = response.status if response is not None else 0
            body = (page.inner_text("body") or "").strip()
            if (status and status >= 400) or len(body) < 100:
                raise RuntimeError(
                    f"entry page render failed or was blocked (status={status})"
                )
            return page.content()
        finally:
            ctx.close()

    def _target_year_pattern(self) -> re.Pattern | None:
        if not self.target_years:
            return None
        years = "|".join(re.escape(str(year)) for year in sorted(self.target_years))
        role = r"(?:associate|jd|class|graduate|entry[- ]level|3l|hiring)"
        return re.compile(
            rf"(?:\b(?:{years})\b.{{0,60}}\b{role}\b"
            rf"|\b{role}\b.{{0,60}}\b(?:{years})\b)",
            re.IGNORECASE,
        )

    def fetch_page(self, firm: Firm, page_config: dict | str) -> list[Posting]:
        cfg = {"url": page_config} if isinstance(page_config, str) else page_config
        url = cfg.get("url")
        if not url:
            raise ValueError(f"{firm.name}: entry page requires a url")
        html = self._get_html(url, bool(cfg.get("render", False)))
        parser = _PageParser()
        parser.feed(html)
        text = clean_text(" ".join(parser.text))
        if not text:
            raise RuntimeError(f"{firm.name}: entry page returned no readable text")

        target_year_pattern = self._target_year_pattern()
        evidence: list[str] = []
        for hit in _ENTRY.finditer(text):
            window = text[max(0, hit.start() - 220): hit.end() + 420]
            open_hit = _OPEN.search(window)
            year_hit = target_year_pattern.search(window) if target_year_pattern else None
            if year_hit and _SUMMER.search(year_hit.group(0)):
                year_hit = None
            closed_hit = _CLOSED.search(window)
            if closed_hit and not open_hit:
                continue
            if open_hit or year_hit:
                evidence.append(hit.group(0).lower())
                if open_hit:
                    evidence.append(open_hit.group(0).lower())
                if year_hit:
                    evidence.append(year_hit.group(0).lower())

        # A year-specific entry opportunity is strong enough even if the page
        # uses a bare "Apply" button that our open-language regex does not see.
        if target_year_pattern:
            for hit in target_year_pattern.finditer(text):
                window = text[max(0, hit.start() - 220): hit.end() + 420]
                if _SUMMER.search(hit.group(0)):
                    continue
                if not _CLOSED.search(window) or _OPEN.search(window):
                    evidence.append(hit.group(0).lower())

        if not evidence:
            log.debug("%s: entry page has no open target evidence: %s", firm.name, url)
            return []

        entry_links: list[tuple[str, str]] = []
        open_links: list[str] = []
        for href, anchor_text in parser.links:
            if not href:
                continue
            destination = urljoin(url, href)
            if (
                _ENTRY.search(anchor_text)
                and _ENTRY_ACTION.search(anchor_text)
                and not _NON_3L_ENTRY.search(anchor_text)
                and not destination.lower().endswith(".pdf")
            ):
                entry_links.append((destination, anchor_text))
            elif _OPEN.search(anchor_text):
                open_links.append(destination)
        entry_links = sorted(set(entry_links))
        open_links = sorted(set(open_links))
        label = cfg.get("label") or "Entry-Level Recruiting"
        opportunities = entry_links or [
            (open_links[0] if open_links else url, label)
        ]
        postings: list[Posting] = []
        for destination, anchor_text in opportunities:
            title = anchor_text if entry_links and len(anchor_text) <= 140 else label
            fingerprint = "\n".join(
                [url, title.lower(), *sorted(set(evidence)), destination]
            )
            job_id = "entry-" + hashlib.sha256(
                fingerprint.encode("utf-8")
            ).hexdigest()[:20]
            postings.append(
                Posting(
                    firm=firm.name,
                    job_id=job_id,
                    title=title,
                    location=cfg.get("location", "United States"),
                    url=destination,
                    ats=self.ats_type,
                )
            )
        return postings

    def fetch(self, firm: Firm) -> list[Posting]:
        postings: list[Posting] = []
        for page_config in firm.options.get("entry_pages", []):
            postings.extend(self.fetch_page(firm, page_config))
        return postings
