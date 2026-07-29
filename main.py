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
import copy
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
    p.add_argument(
        "--history",
        nargs="?",
        const=20,
        type=int,
        default=None,
        metavar="N",
        help="Print the most recent N notification attempts and exit (default: 20).",
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


def build_shadow_filter(
    filter_cfg: dict,
    firm_location_policies: dict[str, str],
) -> PostingFilter | None:
    """Build the broad, non-emailing recall filter from the precision config."""
    shadow = filter_cfg.get("shadow_recall", {})
    if not shadow.get("enabled", False):
        return None
    shadow_cfg = copy.deepcopy(filter_cfg)
    shadow_cfg["include_keywords"] = list(shadow.get("include_keywords", []))
    shadow_cfg["include_regexes"] = []
    shadow_cfg["priority_include_regexes"] = []
    shadow_cfg["target_class_years"] = []
    shadow_cfg["enforce_target_class_year"] = False
    return PostingFilter(
        shadow_cfg,
        firm_location_policies=firm_location_policies,
    )


def print_history(store: DiffStore, limit: int) -> None:
    runs = store.list_notification_runs(limit)
    if not runs:
        print("No exact notification audit rows yet.")
    for audit_run in runs:
        completed = f" -> {audit_run.completed_at}" if audit_run.completed_at else ""
        print(
            f"{audit_run.started_at}{completed} [{audit_run.status.upper()}] "
            f"{audit_run.match_count} emailed / {audit_run.shadow_count} shadow"
        )
        print(f"  {audit_run.subject}")
        for item in store.notification_items(audit_run.run_id):
            location = f" — {item['location']}" if item["location"] else ""
            print(f"  • {item['firm']} — {item['title']}{location}")
            print(f"    {item['url']}")
        if audit_run.error:
            print(f"  ERROR: {audit_run.error}")

    legacy = store.list_unlinked_seen(limit)
    if legacy:
        print(
            "\nLegacy sent/seeded candidates (exact digest/status was not recorded "
            "before this audit existed):"
        )
        for item in legacy:
            location = f" — {item['location']}" if item["location"] else ""
            print(
                f"  {item['first_seen']} • {item['firm']} — "
                f"{item['title']}{location}"
            )


def run(args: argparse.Namespace) -> int:
    config = load_yaml(Path(args.config))
    db_path = args.db or config.get("db_path", str(HERE / "state.db"))
    if args.history is not None:
        with DiffStore(db_path) as store:
            print_history(store, args.history)
        return 0

    firms = select_firms(load_firms(Path(args.firms)), args)
    http_cfg = config.get("http", {})
    client = HttpClient(
        timeout=http_cfg.get("timeout", 20.0),
        retries=http_cfg.get("retries", 3),
        backoff_factor=http_cfg.get("backoff_factor", 1.0),
        user_agent=http_cfg.get("user_agent") or DEFAULT_UA,
    )
    registry = build_registry(client)
    firm_location_policies = {
        firm.name: firm.options.get("location_policy")
        for firm in firms
        if firm.options.get("location_policy")
    }
    posting_filter = PostingFilter(
        config.get("filters", {}),
        firm_location_policies=firm_location_policies,
    )
    shadow_filter = build_shadow_filter(
        config.get("filters", {}),
        firm_location_policies,
    )

    summary = RunSummary()
    all_matches: list[Posting] = []
    all_shadow_matches: list[Posting] = []
    match_reasons: dict[tuple[str, str], str] = {}
    shadow_reasons: dict[tuple[str, str], str] = {}

    for firm in firms:
        fetcher = get_fetcher(registry, firm.ats_type)
        if fetcher is None:
            # unknown / unsupported ATS -> skip without crashing.
            log.info("SKIP %s: ats_type=%s not supported", firm.name, firm.ats_type)
            summary.add(FirmResult(firm.name, firm.ats_type, ok=True, fetched=0))
            continue
        try:
            postings = fetcher.fetch(firm)
            matched: list[Posting] = []
            shadow_matched: list[Posting] = []
            for posting in postings:
                decision = posting_filter.decide(posting)
                if decision.matched:
                    matched.append(posting)
                    match_reasons[posting.key()] = decision.reason
                    continue
                log.debug(
                    "FILTERED[%s] %s -- %s",
                    posting.firm,
                    posting.title,
                    decision.reason,
                )
                if shadow_filter is not None:
                    shadow_decision = shadow_filter.decide(posting)
                    if shadow_decision.matched:
                        shadow_matched.append(posting)
                        shadow_reasons[posting.key()] = shadow_decision.reason
                        log.debug(
                            "SHADOW [%s] %s -- %s",
                            posting.firm,
                            posting.title,
                            shadow_decision.reason,
                        )
            all_matches.extend(matched)
            all_shadow_matches.extend(shadow_matched)
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
    summary.shadow_matches = len({posting.key() for posting in all_shadow_matches})

    # --- diff against seen-state ---
    if args.dry_run:
        # Read-only diff so we can preview without mutating state.
        with DiffStore(db_path) as store:
            new_matches = store.select_unseen(all_matches)
    else:
        with DiffStore(db_path) as store:
            new_matches = store.select_unseen(all_matches)

    summary.new_matches = len(new_matches)
    log.info("RUN SUMMARY: %s", summary.as_line())

    # --- notify ---
    # Always send a digest, even on empty days, so a delivered email doubles as
    # a heartbeat confirming the monitor ran. render_digest handles n == 0.
    digest = render_digest(new_matches, summary, score_fn=posting_filter.entry_score)
    if args.dry_run:
        log.info("[DRY-RUN] Would email %d new match(es):", len(new_matches))
        ConsoleNotifier().notify(digest)
        if all_shadow_matches:
            log.info(
                "[DRY-RUN] %d broader candidate(s) would be retained in shadow audit.",
                summary.shadow_matches,
            )
        return 0

    with DiffStore(db_path) as store:
        run_id = store.begin_notification(
            digest,
            summary.as_line(),
            new_matches,
            shadow_postings=all_shadow_matches,
            match_reasons=match_reasons,
            shadow_reasons=shadow_reasons,
            score_fn=posting_filter.entry_score,
        )
        if args.seed:
            store.finish_notification(run_id, new_matches, status="seeded")
            log.info("[SEED] Wrote %d matches to state; no email sent.", len(new_matches))
            return 0
        try:
            notifier = EmailNotifier(SmtpConfig.from_env())
            notifier.notify(digest)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to send email: %s", e)
            store.fail_notification(run_id, str(e))
            # The audit is retained, but seen-state is intentionally untouched
            # so the same postings remain eligible for the next successful run.
            ConsoleNotifier().notify(digest)
            return 1
        store.finish_notification(run_id, new_matches, status="sent")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
