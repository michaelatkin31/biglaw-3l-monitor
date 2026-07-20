"""Radancy / TalentBrew fetcher (e.g. A&O Shearman).

The careers "search-jobs" page is server-rendered HTML (the jobs are in the
initial response -- no JS/XHR needed at runtime; that was confirmed with a
headless browser during discovery, but the fetcher itself is plain HTTP). Each
job is an anchor:

    <a href="/en/job/{city}/{slug}/{orgid}/{jobid}">Title</a>

`ats_identifier` is the full search-jobs listing URL. Results paginate with
`?p=N` (~15/page); we walk pages until one adds no new job id.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from core.models import Posting
from core.normalize import clean_text, normalize_radancy_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_JOB_ANCHOR = re.compile(r'<a[^>]*href="([^"]*/job/[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_MAX_PAGES = 15


class RadancyFetcher(Fetcher):
    ats_type = "radancy"

    def fetch(self, firm: Firm) -> list[Posting]:
        base = firm.ats_identifier
        if not base:
            raise ValueError(
                f"{firm.name}: radancy requires ats_identifier (the search-jobs URL)"
            )
        pu = urlparse(base)
        origin = f"{pu.scheme}://{pu.netloc}"
        sep = "&" if "?" in base else "?"

        postings: list[Posting] = []
        seen: set[str] = set()
        for page in range(1, _MAX_PAGES + 1):
            html_text = self.client.get_text(f"{base}{sep}p={page}") or ""
            added = 0
            for href, inner in _JOB_ANCHOR.findall(html_text):
                title = clean_text(re.sub(r"<[^>]+>", " ", inner))
                post = normalize_radancy_job(firm.name, href, title, origin)
                if post is None or post.job_id in seen:
                    continue
                seen.add(post.job_id)
                postings.append(post)
                added += 1
            if added == 0:  # page repeated or empty -> end of results
                break
        log.debug("%s: radancy parsed %d jobs", firm.name, len(postings))
        return postings
