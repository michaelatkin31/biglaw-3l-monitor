"""Notification layer.

Rendering (build the digest) is separated from delivery (send it) behind a small
`Notifier` interface, so a future read-only web UI over state.db, or an
alternate channel (Slack, etc.), can reuse `render_digest` without touching SMTP.

SMTP config is read entirely from environment variables:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_TO, EMAIL_FROM
No secrets are ever hardcoded.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from html import escape
from typing import Callable, Optional, Protocol

from .models import Posting, RunSummary

log = logging.getLogger(__name__)


@dataclass
class Digest:
    subject: str
    text_body: str
    html_body: str
    match_count: int


def _dedup(postings: list[Posting]) -> list[Posting]:
    """Collapse presentational duplicates (order-preserving): the same role can
    arrive under two job_ids (so both are "new") yet render as identical lines.
    Dedup by the visible fields; a different location/url keeps both."""
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Posting] = []
    for p in postings:
        key = (
            p.firm.lower(),
            (p.title or "").lower().strip(),
            (p.location or "").lower().strip(),
            (p.url or "").lower().strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _group_by_firm(postings: list[Posting]) -> dict[str, list[Posting]]:
    grouped: dict[str, list[Posting]] = {}
    for p in postings:
        grouped.setdefault(p.firm, []).append(p)
    return {firm: grouped[firm] for firm in sorted(grouped)}


def render_digest(
    new_postings: list[Posting],
    summary: Optional[RunSummary] = None,
    score_fn: Optional[Callable[[Posting], int]] = None,
) -> Digest:
    """Build a subject + text + HTML digest.

    When `score_fn` is given (PostingFilter.entry_score), postings with a positive
    entry-level signal are surfaced in a "Likely entry-level" section at the top,
    ranked highest-first; everything else falls into "Other associate roles"
    grouped by firm. With no score_fn (or when nothing scores), it degrades to the
    plain firm-grouped digest, so ordinary days look unchanged.
    """
    today = date.today().isoformat()
    postings = _dedup(new_postings)
    n = len(postings)
    score = score_fn or (lambda _p: 0)
    likely = sorted(
        (p for p in postings if score(p) > 0),
        key=lambda p: (-score(p), p.firm.lower(), (p.title or "").lower()),
    )
    other = [p for p in postings if score(p) == 0]

    if not n:
        subject = f"[BigLaw 3L Monitor] No new postings — {today}"
    elif likely:
        subject = (
            f"[BigLaw 3L Monitor] {n} new posting(s), "
            f"{len(likely)} likely entry-level — {today}"
        )
    else:
        subject = f"[BigLaw 3L Monitor] {n} new posting(s) — {today}"

    def _line(p: Posting, with_firm: bool) -> tuple[str, str]:
        who = f"{p.firm} — " if with_firm else ""
        loc = f" — {p.location}" if p.location else ""
        when = f" (posted {p.posted_date})" if p.posted_date else ""
        return f"  • {who}{p.title}{loc}{when}", f"    {p.url}"

    # --- plain text ---
    text_lines = [f"{n} new 3L / entry-level associate posting(s) as of {today}", ""]
    if not n:
        text_lines.append("No new postings today. The monitor ran successfully.")
        text_lines.append("")
    if likely:
        text_lines.append(f"** LIKELY ENTRY-LEVEL ({len(likely)}) **")
        for p in likely:
            text_lines.extend(_line(p, with_firm=True))
        text_lines.append("")
    if other:
        # Only label the second tier when a first tier exists above it.
        if likely:
            text_lines.append(f"-- OTHER ASSOCIATE ROLES ({len(other)}) --")
        for firm, posts in _group_by_firm(other).items():
            text_lines.append(f"== {firm} ==")
            for p in posts:
                text_lines.extend(_line(p, with_firm=False))
            text_lines.append("")
    if summary is not None:
        text_lines.append("---")
        text_lines.append(summary.as_line())
    text_body = "\n".join(text_lines)

    # --- html ---
    def _li(p: Posting, with_firm: bool) -> str:
        who = f"<strong>{escape(p.firm)}</strong> — " if with_firm else ""
        loc = f" &middot; {escape(p.location)}" if p.location else ""
        when = (
            f" <span style=\"color:#888\">(posted {escape(p.posted_date)})</span>"
            if p.posted_date
            else ""
        )
        return (
            f"<li style=\"margin:6px 0\">{who}<a href=\"{escape(p.url)}\" "
            f"style=\"color:#1a5fb4;text-decoration:none\">{escape(p.title)}</a>"
            f"{loc}{when}</li>"
        )

    html_parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "max-width:640px;margin:0 auto;color:#1a1a1a\">",
        f"<h2 style=\"margin:0 0 4px\">BigLaw 3L / Entry-Level Monitor</h2>",
        f"<p style=\"color:#666;margin:0 0 16px\">{n} new posting(s)"
        + (f" &middot; {len(likely)} likely entry-level" if likely else "")
        + f" — {escape(today)}</p>",
    ]
    if not n:
        html_parts.append(
            "<p style=\"margin:0 0 16px\">No new postings today. "
            "The monitor ran successfully.</p>"
        )
    if likely:
        html_parts.append(
            "<h3 style=\"margin:20px 0 6px;color:#1a7f37;border-bottom:2px solid "
            "#1a7f37;padding-bottom:4px\">⭐ Likely entry-level "
            f"({len(likely)})</h3><ul style=\"padding-left:18px\">"
        )
        html_parts.extend(_li(p, with_firm=True) for p in likely)
        html_parts.append("</ul>")
    if other:
        if likely:
            html_parts.append(
                "<h3 style=\"margin:28px 0 6px;color:#666;border-bottom:1px solid "
                f"#eee;padding-bottom:4px\">Other associate roles ({len(other)})</h3>"
            )
        for firm, posts in _group_by_firm(other).items():
            html_parts.append(
                f"<h4 style=\"margin:16px 0 4px\">{escape(firm)}</h4>"
                "<ul style=\"padding-left:18px\">"
            )
            html_parts.extend(_li(p, with_firm=False) for p in posts)
            html_parts.append("</ul>")
    if summary is not None:
        html_parts.append(
            f"<p style=\"color:#999;font-size:12px;margin-top:24px;"
            f"border-top:1px solid #eee;padding-top:8px\">{escape(summary.as_line())}</p>"
        )
    html_parts.append("</div>")
    html_body = "".join(html_parts)

    return Digest(subject=subject, text_body=text_body, html_body=html_body, match_count=n)


class Notifier(Protocol):
    def notify(self, digest: Digest) -> None:
        ...


class ConsoleNotifier:
    """Prints the digest to stdout. Used by --dry-run and for local debugging."""

    def notify(self, digest: Digest) -> None:
        print("=" * 70)
        print(f"SUBJECT: {digest.subject}")
        print("=" * 70)
        print(digest.text_body)
        print("=" * 70)


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: Optional[str]
    password: Optional[str]
    email_to: str
    email_from: str

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        missing = [
            v
            for v in ("SMTP_HOST", "EMAIL_TO", "EMAIL_FROM")
            if not os.environ.get(v)
        ]
        if missing:
            raise RuntimeError(
                f"Missing required SMTP env vars: {', '.join(missing)}. "
                "Set SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASS/EMAIL_TO/EMAIL_FROM "
                "or run with --dry-run."
            )
        return cls(
            host=os.environ["SMTP_HOST"],
            port=int(os.environ.get("SMTP_PORT", "587")),
            user=os.environ.get("SMTP_USER"),
            password=os.environ.get("SMTP_PASS"),
            email_to=os.environ["EMAIL_TO"],
            email_from=os.environ["EMAIL_FROM"],
        )


class EmailNotifier:
    """Delivers the digest via SMTP. Port 465 => implicit TLS; else STARTTLS."""

    def __init__(self, config: SmtpConfig) -> None:
        self.config = config

    def notify(self, digest: Digest) -> None:
        msg = EmailMessage()
        msg["Subject"] = digest.subject
        msg["From"] = self.config.email_from
        # EMAIL_TO may be a comma-separated list.
        recipients = [r.strip() for r in self.config.email_to.split(",") if r.strip()]
        msg["To"] = ", ".join(recipients)
        msg.set_content(digest.text_body)
        msg.add_alternative(digest.html_body, subtype="html")

        cfg = self.config
        ctx = ssl.create_default_context()
        if cfg.port == 465:
            with smtplib.SMTP_SSL(cfg.host, cfg.port, context=ctx) as server:
                self._login_send(server, msg, recipients)
        else:
            with smtplib.SMTP(cfg.host, cfg.port) as server:
                server.ehlo()
                server.starttls(context=ctx)
                server.ehlo()
                self._login_send(server, msg, recipients)
        log.info("Sent digest email to %s (%d matches)", recipients, digest.match_count)

    def _login_send(self, server, msg, recipients) -> None:
        if self.config.user and self.config.password:
            server.login(self.config.user, self.config.password)
        server.send_message(msg, to_addrs=recipients)
