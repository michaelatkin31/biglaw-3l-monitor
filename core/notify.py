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
from typing import Optional, Protocol

from .models import Posting, RunSummary

log = logging.getLogger(__name__)


@dataclass
class Digest:
    subject: str
    text_body: str
    html_body: str
    match_count: int


def _group_by_firm(postings: list[Posting]) -> dict[str, list[Posting]]:
    grouped: dict[str, list[Posting]] = {}
    for p in postings:
        grouped.setdefault(p.firm, []).append(p)
    return {firm: grouped[firm] for firm in sorted(grouped)}


def render_digest(
    new_postings: list[Posting], summary: Optional[RunSummary] = None
) -> Digest:
    """Build a subject + text + HTML digest grouped by firm."""
    today = date.today().isoformat()
    n = len(new_postings)
    if n:
        subject = f"[BigLaw 3L Monitor] {n} new entry-level posting(s) — {today}"
    else:
        subject = f"[BigLaw 3L Monitor] No new postings — {today}"

    grouped = _group_by_firm(new_postings)

    # --- plain text ---
    text_lines = [f"{n} new entry-level / 3L associate posting(s) as of {today}", ""]
    if not n:
        text_lines.append("No new postings today. The monitor ran successfully.")
        text_lines.append("")
    for firm, posts in grouped.items():
        text_lines.append(f"== {firm} ==")
        for p in posts:
            loc = f" — {p.location}" if p.location else ""
            when = f" (posted {p.posted_date})" if p.posted_date else ""
            text_lines.append(f"  • {p.title}{loc}{when}")
            text_lines.append(f"    {p.url}")
        text_lines.append("")
    if summary is not None:
        text_lines.append("---")
        text_lines.append(summary.as_line())
    text_body = "\n".join(text_lines)

    # --- html ---
    html_parts = [
        "<div style=\"font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "max-width:640px;margin:0 auto;color:#1a1a1a\">",
        f"<h2 style=\"margin:0 0 4px\">BigLaw 3L / Entry-Level Monitor</h2>",
        f"<p style=\"color:#666;margin:0 0 16px\">{n} new posting(s) — {escape(today)}</p>",
    ]
    if not n:
        html_parts.append(
            "<p style=\"margin:0 0 16px\">No new postings today. "
            "The monitor ran successfully.</p>"
        )
    for firm, posts in grouped.items():
        html_parts.append(
            f"<h3 style=\"margin:20px 0 6px;border-bottom:1px solid #eee;"
            f"padding-bottom:4px\">{escape(firm)}</h3><ul style=\"padding-left:18px\">"
        )
        for p in posts:
            loc = f" &middot; {escape(p.location)}" if p.location else ""
            when = (
                f" <span style=\"color:#888\">(posted {escape(p.posted_date)})</span>"
                if p.posted_date
                else ""
            )
            html_parts.append(
                f"<li style=\"margin:6px 0\"><a href=\"{escape(p.url)}\" "
                f"style=\"color:#1a5fb4;text-decoration:none\">{escape(p.title)}</a>"
                f"{loc}{when}</li>"
            )
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
