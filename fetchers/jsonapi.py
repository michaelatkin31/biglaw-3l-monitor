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
from core.normalize import find_job_array, normalize_jsonapi_item

from .base import Fetcher, Firm

log = logging.getLogger(__name__)


class JsonApiFetcher(Fetcher):
    ats_type = "jsonapi"

    def fetch(self, firm: Firm) -> list[Posting]:
        url = firm.ats_identifier
        if not url:
            raise ValueError(f"{firm.name}: jsonapi requires ats_identifier (endpoint URL)")
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
