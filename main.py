#!/usr/bin/env python3
"""BigLaw 3L / entry-associate posting monitor.

Orchestrates: fetch -> normalize -> filter -> diff -> notify.

Usage:
    python main.py                 # full run (needs SMTP env vars set)
    python main.py --dry-run       # fetch + filter + print; no state write, no email
    python main.py --firm "Latham & Watkins" --dry-run   # single firm
    python main.py --limit 10 -v   # first 10 firms, verbose

Robustness: one firm failing never aborts the run -- each firm is wrapped in
try/except, logged, and recorded in the run summary.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from core.diff import DiffStore
from core.filter import PostingFilter
from core.http import DEFAULT_UA, HttpClient
from core.models import FirmResult, Posting, RunSummary
from core.notify import (
    ConsoleNotifier,
    EmailNotifier,
    SmtpConfig,
    render_digest,
)
from fetchers import build_registry, get_fetcher
from fetchers.base import Firm

log = logging.getLogger("biglaw_monitor")

HERE = Path(__file__).resolve().parent


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy libraries unless we're debugging.
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_firms(path: Path) -> list[Firm]:
    data = load_yaml(path)
    return [Firm.from_dict(d) for d in data.get("firms", [])]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BigLaw 3L / entry-associate monitor")
    p.add_argument("--config", default=str(HERE / "config.yaml"))
    p.add_argument("--firms", default=str(HERE / "firms.yaml"))
    p.add_argument("--db", default=None, help="Path to SQLite state DB (overrides config)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + filter + print what would be emailed; no state write, no email.",
    )
    p.add_argument(
        "--seed",
        action="store_true",
        help="Fetch + filter + WRITE state but never email. Use once on first "
        "setup so the initial real run doesn't email the entire current backlog.",
    )
    p.add_argument("--firm", action="append", help="Only run these firm name(s).")
    p.add_argument("--limit", type=int, default=None, help="Only process first N firms.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def select_firms(firms: list[Firm], args: argparse.Namespace) -> list[Firm]:
    if args.firm:
        wanted = {f.lower() for f in args.firm}
        firms = [f for f in firms if f.name.lower() in wanted]
    if args.limit is not None:
        firms = firms[: args.limit]
    return firms


def run(args: argparse.Namespace) -> int:
    config = load_yaml(Path(args.config))
    firms = select_firms(load_firms(Path(args.firms)), args)

    http_cfg = config.get("http", {})
    client = HttpClient(
        timeout=http_cfg.get("timeout", 20.0),
        retries=http_cfg.get("retries", 3),
        backoff_factor=http_cfg.get("backoff_factor", 1.0),
        user_agent=http_cfg.get("user_agent") or DEFAULT_UA,
    )
    registry = build_registry(client)
    posting_filter = PostingFilter(config.get("filters", {}))

    db_path = args.db or config.get("db_path", str(HERE / "state.db"))

    summary = RunSummary()
    all_matches: list[Posting] = []

    for firm in firms:
        fetcher = get_fetcher(registry, firm.ats_type)
        if fetcher is None:
            # unknown / unsupported ATS -> skip without crashing.
            log.info("SKIP %s: ats_type=%s not supported", firm.name, firm.ats_type)
            summary.add(FirmResult(firm.name, firm.ats_type, ok=True, fetched=0))
            continue
        try:
            postings = fetcher.fetch(firm)
            matched = posting_filter.apply(postings)
            all_matches.extend(matched)
            summary.add(
                FirmResult(
                    firm.name, firm.ats_type, ok=True,
                    fetched=len(postings), matched=len(matched),
                )
            )
            log.info(
                "%s [%s]: %d fetched, %d matched",
                firm.name, firm.ats_type, len(postings), len(matched),
            )
        except Exception as e:  # noqa: BLE001 - per-firm isolation is the point
            log.warning("%s [%s]: FAILED -- %s", firm.name, firm.ats_type, e)
            summary.add(FirmResult(firm.name, firm.ats_type, ok=False, error=str(e)))

    client.close()

    # --- diff against seen-state ---
    if args.dry_run:
        # Read-only diff so we can preview without mutating state.
        with DiffStore(db_path) as store:
            new_matches = store.select_unseen(all_matches)
    else:
        with DiffStore(db_path) as store:
            new_matches = store.select_unseen(all_matches)
            store.mark_seen(new_matches)

    summary.new_matches = len(new_matches)
    log.info("RUN SUMMARY: %s", summary.as_line())

    if args.seed:
        log.info("[SEED] Wrote %d matches to state; no email sent.", len(new_matches))
        return 0

    # --- notify ---
    # Always send a digest, even on empty days, so a delivered email doubles as
    # a heartbeat confirming the monitor ran. render_digest handles n == 0.
    digest = render_digest(new_matches, summary)
    if args.dry_run:
        log.info("[DRY-RUN] Would email %d new match(es):", len(new_matches))
        ConsoleNotifier().notify(digest)
    else:
        try:
            notifier = EmailNotifier(SmtpConfig.from_env())
            notifier.notify(digest)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to send email: %s", e)
            # State was already written; surface via console so nothing is lost.
            ConsoleNotifier().notify(digest)
            return 1
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
