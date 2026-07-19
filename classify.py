#!/usr/bin/env python3
"""Auto-classify each firm's ATS by probing its careers page.

This is a one-off / occasional maintenance tool, not part of the daily run. It
exists because ATS classification requires fetching each firm's careers page,
which the build sandbox blocked (org egress policy). Run it from any machine
with open outbound HTTPS -- your laptop, or GitHub Actions:

    python classify.py                 # dry report of what it detects
    python classify.py --write         # detect AND update firms.yaml in place
    python classify.py --all --write   # re-probe every firm, not just unknowns
    python classify.py --firm "Latham & Watkins" -v

Detection is by URL signature: it fetches careers_url (and follows a few likely
"open positions"/"search jobs" links if the landing page has no ATS signature),
then looks for the fingerprint of each supported ATS in the HTML. It NEVER
guesses tokens -- an identifier is only recorded if the actual URL was seen.

`--write` edits firms.yaml line-by-line so all comments, notes, and structure
are preserved (only the ats_type / ats_identifier values change, and a
workday_host line is added when resolved).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from core.http import HttpClient

log = logging.getLogger("classify")
HERE = Path(__file__).resolve().parent

# --- ATS URL fingerprints --------------------------------------------------
_GREENHOUSE = re.compile(
    r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9]+)"
    r"|boards-api\.greenhouse\.io/v1/boards/([a-z0-9]+)"
    r"|greenhouse\.io/embed/job_board\?for=([a-z0-9]+)",
    re.IGNORECASE,
)
_LEVER = re.compile(
    r"(?:jobs\.lever\.co|api\.lever\.co/v0/postings)/([a-z0-9-]+)", re.IGNORECASE
)
_WORKDAY = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[^/]+/([^/]+)|"
    r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+))",
    re.IGNORECASE,
)
# Legal-specific / gated systems we can recognize but not query publicly.
_OTHER_SIGNATURES = {
    "viglobal": re.compile(r"vi(?:global|recruit|desktop)|apply\.viglobal", re.I),
    "symplicity": re.compile(r"symplicity\.com|csm\.symplicity", re.I),
    "flo_recruit": re.compile(r"flo\.?recruit", re.I),
    "icims": re.compile(r"icims\.com", re.I),
    "taleo": re.compile(r"taleo\.net", re.I),
    "jobvite": re.compile(r"jobvite\.com", re.I),
}

_FOLLOW_HINTS = re.compile(
    r"(open|search|view|current|all)[- ]?(position|job|opportunit|opening)"
    r"|apply|join[- ]?us|associate|lateral|recruit",
    re.IGNORECASE,
)
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass
class Detection:
    ats_type: str
    identifier: Optional[str] = None
    workday_host: Optional[str] = None
    source_url: Optional[str] = None


def detect_ats(html: str) -> Optional[Detection]:
    """Return a Detection from a page's HTML, or None if no signature found."""
    m = _GREENHOUSE.search(html)
    if m:
        token = next(g for g in m.groups() if g)
        return Detection("greenhouse", identifier=token)

    m = _LEVER.search(html)
    if m:
        return Detection("lever", identifier=m.group(1))

    m = _WORKDAY.search(html)
    if m:
        tenant, wd = m.group(1), m.group(2)
        # site is either the cxs segment (group 3) or the path segment (group 4)
        site = m.group(3) or m.group(4)
        host = f"{tenant}.{wd}.myworkdayjobs.com"
        if site and site.lower() not in {"en", "wday"}:
            return Detection(
                "workday", identifier=f"{tenant}/{site}", workday_host=host
            )

    for name, rx in _OTHER_SIGNATURES.items():
        if rx.search(html):
            # Recognized but not one of our queryable fetchers -> "other".
            return Detection("other", identifier=name)

    return None


def _candidate_links(html: str, base_url: str, limit: int = 4) -> list[str]:
    """Same-site links that look like they lead to a job listing."""
    from urllib.parse import urljoin, urlparse

    base_host = urlparse(base_url).netloc
    seen: list[str] = []
    for href in _HREF.findall(html):
        if not _FOLLOW_HINTS.search(href):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc not in (base_host, ""):
            continue
        if full not in seen and full != base_url:
            seen.append(full)
        if len(seen) >= limit:
            break
    return seen


def probe_firm(client: HttpClient, firm: dict) -> Optional[Detection]:
    url = firm.get("careers_url")
    if not url:
        return None
    try:
        html = client.get_text(url) or ""
    except Exception as e:  # noqa: BLE001
        log.warning("%s: fetch failed (%s)", firm["name"], e)
        return None

    det = detect_ats(html)
    if det:
        det.source_url = url
        return det

    # No signature on the landing page -> follow a few likely links.
    for link in _candidate_links(html, url):
        try:
            sub_html = client.get_text(link) or ""
        except Exception as e:  # noqa: BLE001
            log.debug("%s: sub-fetch %s failed (%s)", firm["name"], link, e)
            continue
        det = detect_ats(sub_html)
        if det:
            det.source_url = link
            log.debug("%s: detected via %s", firm["name"], link)
            return det
    return None


# --- writing back (line-oriented, comment-preserving) ----------------------

def _write_back(path: Path, detections: dict[str, Detection]) -> int:
    lines = path.read_text().splitlines(keepends=True)
    out: list[str] = []
    cur_firm: Optional[str] = None
    changed = 0
    name_re = re.compile(r'^\s*-\s+name:\s*["\']?(.+?)["\']?\s*$')

    for line in lines:
        m = name_re.match(line)
        if m:
            cur_firm = m.group(1)
            out.append(line)
            continue
        det = detections.get(cur_firm) if cur_firm else None
        if det:
            indent_m = re.match(r"^(\s*)ats_type:\s", line)
            if indent_m:
                out.append(f"{indent_m.group(1)}ats_type: {det.ats_type}\n")
                changed += 1
                continue
            id_m = re.match(r"^(\s*)ats_identifier:\s", line)
            if id_m:
                val = det.identifier if det.identifier is not None else "null"
                # Quote workday "tenant/site" and any value with a slash.
                if val != "null" and ("/" in val or ":" in val):
                    val = f'"{val}"'
                out.append(f"{id_m.group(1)}ats_identifier: {val}\n")
                # Add workday_host right after, if resolved and not already there.
                if det.workday_host:
                    out.append(f"{id_m.group(1)}workday_host: {det.workday_host}\n")
                continue
        out.append(line)
    path.write_text("".join(out))
    return changed


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Classify firms by ATS")
    p.add_argument("--firms", default=str(HERE / "firms.yaml"))
    p.add_argument("--write", action="store_true", help="Update firms.yaml in place")
    p.add_argument("--all", action="store_true", help="Re-probe all firms, not just unknown")
    p.add_argument("--firm", action="append", help="Only these firm name(s)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--timeout", type=float, default=25.0)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    path = Path(args.firms)
    data = yaml.safe_load(path.read_text())
    firms = data.get("firms", [])

    targets = firms
    if not args.all:
        targets = [f for f in targets if (f.get("ats_type") or "unknown") == "unknown"]
    if args.firm:
        wanted = {x.lower() for x in args.firm}
        targets = [f for f in targets if f["name"].lower() in wanted]
    if args.limit is not None:
        targets = targets[: args.limit]

    client = HttpClient(timeout=args.timeout)
    detections: dict[str, Detection] = {}
    for firm in targets:
        det = probe_firm(client, firm)
        if det:
            detections[firm["name"]] = det
            extra = f" host={det.workday_host}" if det.workday_host else ""
            log.info(
                "%-45s -> %-11s %s%s",
                firm["name"], det.ats_type, det.identifier or "", extra,
            )
        else:
            log.info("%-45s -> unknown (no signature found)", firm["name"])
    client.close()

    detected = {k: v for k, v in detections.items() if v.ats_type != "other"}
    log.info(
        "Probed %d firm(s): %d classified to a queryable ATS, %d 'other', %d unknown",
        len(targets),
        len(detected),
        sum(1 for v in detections.values() if v.ats_type == "other"),
        len(targets) - len(detections),
    )

    if args.write:
        n = _write_back(path, detections)
        log.info("Wrote %d ats_type value(s) back to %s", n, path)
    else:
        log.info("Dry report only. Re-run with --write to update firms.yaml.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
