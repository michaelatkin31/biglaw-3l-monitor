"""Greenhouse fetcher.

Public JSON board API -- no auth required:
    https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

`ats_identifier` is the board token (the {token} in boards.greenhouse.io/{token}).
"""

from __future__ import annotations

import logging

from core.models import Posting
from core.normalize import normalize_greenhouse_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseFetcher(Fetcher):
    ats_type = "greenhouse"

    def fetch(self, firm: Firm) -> list[Posting]:
        token = firm.ats_identifier
        if not token:
            raise ValueError(f"{firm.name}: greenhouse requires ats_identifier (board token)")
        url = _API.format(token=token)
        # content=true returns full job objects (incl. first_published); we only
        # need the metadata fields but it is a single request either way.
        data = self.client.get_json(url, params={"content": "true"})
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        postings = [normalize_greenhouse_job(firm.name, j) for j in jobs]
        log.debug("%s: greenhouse returned %d jobs", firm.name, len(postings))
        return postings
