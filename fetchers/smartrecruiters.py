"""SmartRecruiters fetcher.

Public postings API -- no auth required:
    https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=N

`ats_identifier` is the company identifier (e.g. "CrowellMoring").

Caveat: this endpoint returns HTTP 200 with totalFound:0 even for an unknown
company id, so an empty result is not proof the id is wrong -- confirm the id
against the hosted board (careers.smartrecruiters.com/{company}) when adding a firm.
"""

from __future__ import annotations

import logging

from core.models import Posting
from core.normalize import normalize_smartrecruiters_posting

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
_PAGE = 100
_MAX = 2000  # safety cap


class SmartRecruitersFetcher(Fetcher):
    ats_type = "smartrecruiters"

    def fetch(self, firm: Firm) -> list[Posting]:
        company = firm.ats_identifier
        if not company:
            raise ValueError(
                f"{firm.name}: smartrecruiters requires ats_identifier (company id)"
            )
        url = _API.format(company=company)
        postings: list[Posting] = []
        offset = 0
        total = None
        while offset < _MAX:
            data = self.client.get_json(url, params={"limit": _PAGE, "offset": offset})
            if not isinstance(data, dict):
                break
            if total is None:
                total = int(data.get("totalFound") or 0)
            content = data.get("content") or []
            if not content:
                break
            postings.extend(
                normalize_smartrecruiters_posting(firm.name, p, company) for p in content
            )
            offset += _PAGE
            if total is not None and offset >= total:
                break
        log.debug("%s: smartrecruiters returned %d jobs", firm.name, len(postings))
        return postings
