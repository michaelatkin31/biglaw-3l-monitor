"""viRecruit / viGlobal fetcher (vi by Aderant -- the dominant BigLaw ATS).

Many large firms (Sullivan & Cromwell, Kirkland, Cleary, Gibson Dunn, Jones Day,
O'Melveny, ...) route attorney hiring through vi's "self-apply" portal. There is
no public JSON API and the *Apply* action is login-gated -- BUT the **listing**
page (``.../viRecruitSelfApply/RecDefault.aspx``) is public, server-rendered HTML
that lists every open role with its title, office, practice area and post date.

`ats_identifier` is the full listing URL. Each job row is an ASP.NET GridView row:

    <tr class="even-row"><td>
      <h4>{title}</h4>
      <section class="sub-title">
        <h5>Office <span>{office}</span></h5>
        <h5>Practice Area <span>{practice}</span></h5>
        <h5>Date Posted <span>{posted}</span></h5>
        <h5>Application Deadline <span>{deadline}</span></h5>
      </section>
      ...
    </td></tr>

We anchor on the <h4> title and the labeled <h5><span> fields, which is stable
across firms even when a firm prefixes the title with the office name.
"""

from __future__ import annotations

import logging
import re

from core.models import Posting
from core.normalize import clean_text, normalize_virecruit_job

from .base import Fetcher, Firm

log = logging.getLogger(__name__)

_TABLE = re.compile(
    r'<table[^>]*id="[^"]*gridviewList[^"]*"[^>]*>(.*?)</table>', re.S | re.I
)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_H4 = re.compile(r"<h4[^>]*>(.*?)</h4>", re.S | re.I)
# <h5>Office <span>Century City</span></h5>  -> ("Office", "Century City")
_H5 = re.compile(r"<h5[^>]*>(.*?)<span[^>]*>(.*?)</span>", re.S | re.I)


def parse_virecruit_html(html_text: str) -> list[dict]:
    """Extract job rows from a viRecruit self-apply listing page.

    Pure function (no I/O) so it can be unit-tested against a captured page.
    Returns a list of {title, office, practice, posted} dicts.
    """
    table = _TABLE.search(html_text or "")
    if not table:
        return []
    jobs: list[dict] = []
    for row in _ROW.findall(table.group(1)):
        m_title = _H4.search(row)
        if not m_title:
            continue
        title = clean_text(re.sub(r"<[^>]+>", " ", m_title.group(1)))
        if not title:
            continue
        fields = {
            clean_text(re.sub(r"<[^>]+>", " ", label)).rstrip().lower(): clean_text(
                re.sub(r"<[^>]+>", " ", value)
            )
            for label, value in _H5.findall(row)
        }
        jobs.append(
            {
                "title": title,
                "office": fields.get("office", ""),
                "practice": fields.get("practice area", ""),
                "posted": fields.get("date posted", ""),
            }
        )
    return jobs


class ViRecruitFetcher(Fetcher):
    ats_type = "virecruit"

    def fetch(self, firm: Firm) -> list[Posting]:
        url = firm.ats_identifier
        if not url:
            raise ValueError(
                f"{firm.name}: virecruit requires ats_identifier "
                f"(the viRecruitSelfApply RecDefault.aspx listing URL)"
            )
        html_text = self.client.get_text(url) or ""
        rows = parse_virecruit_html(html_text)
        if not rows:
            # A public viRecruit listing always renders the GridView; no rows
            # means the page was blocked (Cloudflare/login) or the markup moved.
            raise RuntimeError(
                f"{firm.name}: viRecruit listing returned no parseable jobs "
                f"(blocked, empty, or markup changed): {url}"
            )
        postings = [
            p for r in rows if (p := normalize_virecruit_job(firm.name, r, url)) is not None
        ]
        log.debug("%s: virecruit parsed %d jobs", firm.name, len(postings))
        return postings
