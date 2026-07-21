"""Headless-browser fetcher for firms whose openings only exist in the rendered DOM.

Last resort for careers sites that are JS-rendered with no server-rendered HTML and
no discoverable JSON API (e.g. Hunton, Baker McKenzie/Avature, Herrick). Loads the
page in headless Chromium, follows one "openings" hop if needed, and extracts the
repeated per-job links (id-based or a job-title slug) across all frames.

Requires Playwright + Chromium at runtime:  pip install playwright && playwright install chromium
`ats_identifier` is the URL to render (defaults to careers_url).

NOTE: heavier and flakier than the HTTP fetchers -- per-firm failure is caught by
main.py and never aborts the run.
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import urlparse

from core.models import Posting
from core.normalize import clean_text

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_JOBHREF = re.compile(
    r'/job[s]?/\d|/careers?/job[-/][a-z0-9-]{4,}|[?&](job|req|posting)[-_]?id=|/viewjob'
    r'|jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{8}|boards\.greenhouse\.io/[^/]+/jobs/\d'
    r'|myworkdayjobs\.com/.+/job/|/opening[s]?/\d|/position[s]?/\d|icims\.com/jobs/\d',
    re.I,
)
_SLUGHREF = re.compile(
    r'/(?:jobs?|opening[s]?|position[s]?|vacanc\w+|opportunit\w+)/[a-z][a-z0-9]+(?:-[a-z0-9]+){2,}/?$',
    re.I,
)
_CATEGORY = re.compile(
    r'laterals?-and-experienced|experienced-lawyers$|overview|how-to-apply|why-|our-|life-at'
    r'|north-america|asia-pacific|-london|-brussels|-geneva|-munich$|-paris$|professional-staff',
    re.I,
)
_ROLE = re.compile(r'associate|attorney|counsel|lawyer|litigation|corporate|paralegal|clerk', re.I)
_GENERIC_CTA = re.compile(
    r'^(apply( now| here| online)?|view( job| details| posting)?|here|details|learn more'
    r'|read more|more( info)?|see (job|more|details)|open|opportunit\w*|explore|>+)\s*$', re.I)
_NAV = re.compile(r'search|view|current|open|opportunit|browse|position|vacan|lateral', re.I)

# Per anchor: its href, own text, and the nearest ancestor heading (the job-card
# title, for sites where the link text is just "Apply").
_EVAL_LINKS = (
    "els=>els.map(e=>{let ct='';let n=e;"
    "for(let i=0;i<5&&n;i++){n=n.parentElement;if(!n)break;"
    "let hh=n.querySelector('h1,h2,h3,h4,h5,[class*=title],[class*=Title],[class*=jobTitle]');"
    "if(hh&&hh.innerText&&hh.innerText.trim().length>6){ct=hh.innerText.trim();break;}}"
    "return {h:e.href,t:(e.innerText||e.getAttribute('aria-label')||'').trim(),ct:ct};})"
    ".filter(x=>x.h&&x.h.startsWith('http'))"
)


# A string that reads like a real job title (not a nav item): has a fee-earner role
# word AND some specificity (a qualifier, punctuation, or a class year), and is not
# a bare category label.
_TITLEISH = re.compile(r'\b(associate|attorney|counsel|lawyer|paralegal|clerk)\b', re.I)
_TITLE_SPECIFIC = re.compile(r'[-,/(|]|\b(20\d\d|junior|senior|mid|entry|lateral|partner|of counsel'
                             r'|litigation|corporate|real estate|tax|ip|patent|labor|employment'
                             r'|finance|regulatory|trust|estate|privacy|m&a|bankruptcy|restructuring'
                             r'|first[- ]year|new grad|summer|post[- ]clerk)\b', re.I)
_NAV_TITLE = re.compile(r'^(experienced |lateral |our |meet our |why |about |current )?'
                        r'(attorneys?|lawyers?|associates?|counsel|paralegals?|openings?|opportunities?'
                        r'|positions?|advertising|professionals?|staff)([ &].*)?$', re.I)
_TITLE_ELS = ("h2,h3,h4,h5,li,td,p,a,span,[class*=job],[class*=position],[class*=opening],"
              "[class*=title],[class*=posting],[class*=vacancy],[class*=role]")
_EVAL_TITLES = (
    "els=>els.map(e=>{let a=e.matches('a')?e:(e.closest('a')||e.querySelector('a'));"
    "return {t:(e.innerText||'').trim(), h:(a&&a.href)||''};})"
    ".filter(x=>x.t&&x.t.length>=8&&x.t.length<=110)"
)


def _is_job_title(t: str) -> bool:
    t = t.strip()
    if not _TITLEISH.search(t) or _NAV_TITLE.match(t) or _GENERIC_CTA.match(t):
        return False
    if len(t.split()) < 2:
        return False
    return bool(_TITLE_SPECIFIC.search(t)) or len(t) >= 22


def _collect(page) -> dict:
    out: dict[str, str] = {}          # href-or-titlekey -> title
    for fr in page.frames:
        # 1) id/slug-based job links (precise; carry a stable url)
        try:
            got = fr.eval_on_selector_all("a[href]", _EVAL_LINKS)
        except Exception:
            got = []
        for a in got:
            h = (a["h"] or "").split("#")[0].rstrip("/")
            t = (a["t"] or "").split("\n")[0]
            if not h.startswith("http"):
                continue
            path = urlparse(h).path
            if _CATEGORY.search(path):
                continue
            if _JOBHREF.search(h) or (_SLUGHREF.search(path) and (_ROLE.search(t) or _ROLE.search(path))):
                title = "" if (not t or _GENERIC_CTA.match(t)) else t
                if not title:
                    ct = (a.get("ct") or "").split("\n")[0]
                    title = ct if (ct and not _GENERIC_CTA.match(ct)) else ""
                if not title:
                    seg = max([s for s in path.split("/") if s], key=lambda s: s.count("-"), default="")
                    if seg.count("-") >= 2:
                        title = re.sub(r"%[0-9a-f]{2}", " ", seg, flags=re.I).replace("-", " ").title()
                if title and h not in out:
                    out[h] = title[:90]
        # 2) title-based (server-rendered job rows whose links don't match a pattern)
        try:
            cells = fr.eval_on_selector_all(_TITLE_ELS, _EVAL_TITLES)
        except Exception:
            cells = []
        for c in cells:
            t = c["t"].split("\n")[0].strip()
            if not _is_job_title(t):
                continue
            h = (c["h"] or "").split("#")[0].rstrip("/")
            key = h if (h.startswith("http") and not _CATEGORY.search(urlparse(h).path)) else f"title::{t.lower()}"
            if key not in out:
                out[key] = t[:90]
    return out


_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class BrowserFetcher(Fetcher):
    """Reuses a single headless Chromium across all firms in a run (launching one
    per firm would add minutes). The browser is started lazily on first use and
    torn down at process exit."""

    ats_type = "browser"
    _pw = None
    _browser = None

    def _page(self):
        import atexit
        if BrowserFetcher._browser is None:
            from playwright.sync_api import sync_playwright
            BrowserFetcher._pw = sync_playwright().start()
            BrowserFetcher._browser = BrowserFetcher._pw.chromium.launch(headless=True)
            atexit.register(self._shutdown)
        ctx = BrowserFetcher._browser.new_context(user_agent=_UA)
        return ctx

    @classmethod
    def _shutdown(cls):
        try:
            if cls._browser: cls._browser.close()
            if cls._pw: cls._pw.stop()
        except Exception:
            pass
        cls._browser = cls._pw = None

    def fetch(self, firm: Firm) -> list[Posting]:
        url = firm.ats_identifier or firm.careers_url
        if not url:
            raise ValueError(f"{firm.name}: browser fetcher requires ats_identifier or careers_url")
        try:
            ctx = self._page()
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                f"{firm.name}: browser fetcher needs playwright "
                f"(pip install playwright && playwright install chromium)"
            ) from e

        try:
            pg = ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(6000)
            links = _collect(pg)
            if len(links) < 3:  # some boards load jobs via a slow XHR -- wait & retry
                pg.wait_for_timeout(7000)
                links = _collect(pg) or links
            if len(links) < 3:  # try one hop toward the openings list
                try:
                    cands = pg.eval_on_selector_all("a", _EVAL_LINKS)
                    hop = next((c["h"] for c in cands
                                if _NAV.search(c["t"] or "")
                                and re.search(r'job|open|opportunit|position|search|vacan|lateral', c["h"], re.I)), None)
                    if hop:
                        pg.goto(hop, wait_until="domcontentloaded", timeout=35000)
                        pg.wait_for_timeout(5000)
                        links = _collect(pg) or links
                except Exception:
                    pass
        finally:
            try: ctx.close()
            except Exception: pass

        if not links:
            raise RuntimeError(f"{firm.name}: browser render found no job listings: {url}")
        postings = []
        for key, title in links.items():
            title = clean_text(title)
            if not title:
                continue
            job_url = key if key.startswith("http") else url          # title-only -> page url
            id_seed = key if key.startswith("http") else f"{firm.name}:{title}"
            job_id = "br-" + hashlib.md5(id_seed.encode("utf-8")).hexdigest()[:16]
            postings.append(Posting(firm=firm.name, job_id=job_id, title=title,
                                    location="", url=job_url, ats="browser", posted_date=None))
        log.debug("%s: browser found %d jobs", firm.name, len(postings))
        return postings
