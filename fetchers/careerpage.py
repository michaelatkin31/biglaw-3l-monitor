"""career.page fetcher (Jibe/Phenom SEO front-end).

Some firms whose real ATS is gated (e.g. Morrison & Foerster on iCIMS) still
expose a clean, unauthenticated JSON board through a `*.career.page` front-end:

    https://{identifier}.career.page/api/jobs?limit=N&offset=M

`ats_identifier` is the subdomain (e.g. "mofo" for mofo.career.page).
"""

from __future__ import annotations

import logging

from core.models import Posting
from core.normalize import normalize_careerpage_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_PAGE = 100
_MAX = 1000  # safety cap


class CareerPageFetcher(Fetcher):
    ats_type = "careerpage"

    def fetch(self, firm: Firm) -> list[Posting]:
        ident = firm.ats_identifier
        if not ident:
            raise ValueError(
                f"{firm.name}: careerpage requires ats_identifier (a *.career.page "
                f"subdomain OR a full Jibe /api/jobs URL)"
            )
        # A bare token means the hosted {sub}.career.page host; a full URL (e.g.
        # a self-hosted Jibe front-end like careers.ogletree.com/api/jobs) is used
        # as-is. Both share the same {jobs:[{data}], totalCount} response shape.
        base = ident if ident.startswith("http") else f"https://{ident}.career.page/api/jobs"
        postings: list[Posting] = []
        offset = 0
        total = None
        while offset < _MAX:
            data = self.client.get_json(base, params={"limit": _PAGE, "offset": offset})
            if not isinstance(data, dict):
                break
            if total is None:
                total = int(data.get("totalCount") or 0)
            jobs = data.get("jobs") or []
            if not jobs:
                break
            for j in jobs:
                p = normalize_careerpage_job(firm.name, j)
                if p is not None:
                    postings.append(p)
            offset += _PAGE
            if total is not None and offset >= total:
                break
        log.debug("%s: careerpage returned %d jobs", firm.name, len(postings))
        return postings
