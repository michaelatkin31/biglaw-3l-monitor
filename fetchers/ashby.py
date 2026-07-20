"""Ashby fetcher.

Public posting API -- no auth required, returns the whole board in one request:
    https://api.ashbyhq.com/posting-api/job-board/{org}

`ats_identifier` is the org slug (from jobs.ashbyhq.com/{org}).
"""

from __future__ import annotations

import logging

from core.models import Posting
from core.normalize import normalize_ashby_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_API = "https://api.ashbyhq.com/posting-api/job-board/{org}"


class AshbyFetcher(Fetcher):
    ats_type = "ashby"

    def fetch(self, firm: Firm) -> list[Posting]:
        org = firm.ats_identifier
        if not org:
            raise ValueError(f"{firm.name}: ashby requires ats_identifier (org slug)")
        data = self.client.get_json(_API.format(org=org))
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        postings = [
            p for j in jobs if (p := normalize_ashby_job(firm.name, j)) is not None
        ]
        log.debug("%s: ashby returned %d listed jobs", firm.name, len(postings))
        return postings
