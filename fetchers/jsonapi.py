"""Generic auto-detecting JSON-API fetcher.

Many firms expose a public JSON endpoint carrying their attorney openings, but every
one has a different shape (Ropes `/sitecore/api/jobsearch`, Orrick `/api/jobs`,
Vedder Price Next.js `/_next/data/...`, Flo Recruit `/api/v2/public-jobs/{firm}/careers`,
ClearCompany, Paycom, UltiPro, ...). Rather than one fetcher per firm, we GET the
endpoint and auto-locate the jobs array + map title/location/url/id by key name.

`ats_identifier` is the full endpoint URL (discovered via a headless browser).
"""

from __future__ import annotations

import logging

from core.models import Posting
from core.normalize import find_job_array, normalize_jsonapi_item, parse_rss_jobs

from .base import Fetcher, Firm

log = logging.getLogger(__name__)


class JsonApiFetcher(Fetcher):
    ats_type = "jsonapi"

    def fetch(self, firm: Firm) -> list[Posting]:
        url = firm.ats_identifier
        if not url:
            raise ValueError(f"{firm.name}: jsonapi requires ats_identifier (endpoint URL)")
        opts = firm.options or {}

        # RSS/Atom feed (e.g. eArcu) -> parse XML, not JSON.
        if opts.get("rss") or url.rstrip("/").lower().endswith("rss"):
            posts = parse_rss_jobs(firm.name, self.client.get_text(url) or "")
            if not posts:
                raise RuntimeError(f"{firm.name}: RSS feed had no items: {url}")
            log.debug("%s: jsonapi(rss) parsed %d jobs", firm.name, len(posts))
            return posts

        # Some boards (UltiPro, Avature, Algolia, custom search) only answer to a
        # POST with a JSON body; store it as `json_post` (and optional `json_headers`).
        post_body = opts.get("json_post")
        if post_body is not None:
            data = self.client.post_json(url, post_body, headers=opts.get("json_headers") or {})
        else:
            data = self.client.get_json(url)
        arr = find_job_array(data)
        if not arr:
            raise RuntimeError(
                f"{firm.name}: jsonapi endpoint returned no recognizable job array: {url}"
            )
        postings = [
            p for it in arr if isinstance(it, dict)
            and (p := normalize_jsonapi_item(firm.name, it, url)) is not None
        ]
        log.debug("%s: jsonapi parsed %d jobs", firm.name, len(postings))
        return postings
