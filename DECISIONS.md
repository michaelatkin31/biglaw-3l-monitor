# DECISIONS.md

Assumptions, deferrals, and judgment calls made while building this
autonomously. Read alongside `README.md`.

## 1. Repository location

The spec says "create a **new git repo** (`biglaw-3l-monitor`)". The execution
environment for this task is scoped to a single existing repository
(`genesistherapeutics/deep-affinity`) and a designated branch, and creating a
separate GitHub repository was outside that scope. So the full project was built
as a **self-contained directory** (`biglaw-3l-monitor/`) committed to the
designated branch. It is structured to be a standalone repo root: to make it one,
copy the directory out (`git init` / push to a new repo) or move its `.github/`
to the repo root. Nothing about the code depends on being nested.

- Consequence: the GitHub Actions workflows under `biglaw-3l-monitor/.github/`
  will **not** run while nested inside `deep-affinity` (GitHub only reads
  `.github/workflows/` at the repo root) — which is actually desirable here, so
  this personal cron doesn't execute in an unrelated repo's CI.

## 2. The big one: no network in the build environment → firms ship unclassified

Outbound HTTPS in the build sandbox was blocked by org egress policy (HTTP 403
on every host, including the Greenhouse/Lever APIs, Workday, and every firm's
site). That made it **impossible to classify any firm's ATS here**, because
classification requires fetching each careers page and inspecting where its
apply links point.

Rather than guess ATS types/tokens (explicitly disallowed by the spec, and a
source of silent false data), **every firm in `firms.yaml` ships as
`ats_type: unknown`**, and I built **`classify.py`** to do the classification
from any machine with open egress (your laptop, or GitHub Actions — the
`classify-firms` workflow). It probes each `careers_url`, follows a few likely
"open positions" links, fingerprints the ATS by URL signature, and writes
`ats_type` / `ats_identifier` back into `firms.yaml` (line-by-line, preserving
comments). It never records an identifier it didn't actually see in a URL.

**Until `classify.py` is run, the monitor fetches nothing** (unknown firms are
skipped). This is the one deferred step required to make the tool live, and it's
a single command / one-click workflow.

## 3. Firm list: source, date, and intersection caveats

Target set = **Vault Law 100 ∩ Am Law 200**.

- **Vault Law 100** — 2026 edition (released mid-2025), `vault.com`. Gated;
  could not be enumerated in full from search snippets.
- **Am Law 200** — 2025 edition (2024 gross revenue), `law.com/americanlawyer`.
  Paywalled; not enumerable in full.

Because both lists are gated, `firms.yaml` is a **best-effort intersection**:
**81 firms**. The research core (~69) is the high-confidence intersection
reconstructed from confirmed search anchors + domain knowledge; ~12 more
well-known Am Law 100/200 + Vault-ranked firms were added from domain knowledge
to approach the spec's expected ~80–100 range. The Vault "tail" (roughly ranks
70–100) could not be fully enumerated, so the list may be **short by ~5–15
firms**. Sources + dates are recorded at the top of `firms.yaml`.

Firms **flagged** rather than silently dropped/kept (see inline `note:` fields):

- **Magic Circle firms excluded**: Freshfields, Clifford Chance, Linklaters are
  Vault-ranked but London-HQ and tracked in the Global 200, generally **not** in
  the Am Law 200 → excluded from the intersection.
- **A&O Shearman**: kept but flagged — post-2024-merger US revenue reporting is
  ambiguous; may or may not belong.
- **Kramer Levin / HSF Kramer**: flagged — after its merger it likely dropped out
  of the current Vault 100.
- **Boutiques (Kellogg Hansen, Keker Van Nest, Kobre & Kim)**: Vault-ranked with
  very high revenue-per-lawyer, but Am Law 200 *membership* is uncertain given
  small headcount — flagged for verification.
- **"Kaye Scholer"**: included only as a **dedup marker** — it merged into
  Arnold & Porter in 2017 and is a duplicate of "Arnold & Porter Kaye Scholer".
  Remove it if you don't want the reminder.

## 4. Known coverage ceiling (the risk the spec asked me to record)

A meaningful share of big-law entry-level hiring flows through **OCI /
Symplicity / Flo Recruit** school-gated portals and **never appears on public
careers pages**. Public-page coverage is therefore inherently partial — this
tool sees only what firms post publicly. `classify.py` recognizes Symplicity /
Flo Recruit / viGlobal signatures and marks those firms `ats_type: other`
(recognized but not publicly queryable), which makes the ceiling visible: after
running it, `firms.yaml` will show roughly how many firms surface entry roles on
a queryable public ATS vs. how many are gated. Until then, `public_entry_level`
is `unknown` for all firms. Expect the genuinely-public entry-associate set to be
a minority of firms.

## 5. State / diff model

`state.db` (SQLite) records **only postings that matched the filter** — i.e. the
things we've already notified on — keyed by `(firm, job_id)` with a first-seen
timestamp and the normalized fields. This gives notification idempotency (the
point of the diff). Side effect: if a firm edits a previously-non-matching title
into a matching one, it's (correctly) treated as new. We deliberately do **not**
record every fetched job, to avoid suppressing a posting whose title later starts
matching.

## 6. Notification policy

- **Silent on empty days** (the spec's default). No weekly heartbeat was built —
  easy to add later (render an empty digest on a chosen weekday), but omitted to
  keep the tool quiet.
- `notify.py` splits **rendering** (`render_digest`) from **delivery**
  (`EmailNotifier` / `ConsoleNotifier`) behind a `Notifier` protocol, so a future
  read-only web UI over `state.db` — or a Slack channel — can reuse the renderer.
- **First-run backfill**: the first real run would email *every* currently-open
  matching posting. `--seed` writes state without emailing so you can start clean.

## 7. Fetcher specifics

- **Workday host resolution**: the public host includes a data-center number
  (`{tenant}.wdN.myworkdayjobs.com`) that isn't derivable from the tenant.
  `classify.py` captures it and pins `workday_host` in `firms.yaml`. If a firm is
  configured with only `tenant/site`, the fetcher probes a short, fixed list of
  data-center subdomains once and logs the winner (so it can be pinned) — kept
  deliberately small to avoid hammering Workday.
- **Workday `posted_date`** is a relative string ("Posted 5 Days Ago") — the CXS
  jobs list carries no absolute date. It's stored as-is (informational only;
  identity is `(firm, job_id)`).
- **Generic fetcher** extracts schema.org `JobPosting` JSON-LD (what ATSs emit
  for Google-for-Jobs SEO). Playwright is **optional** and used only for firms
  explicitly marked `render: playwright`, to keep the CI run light.

## 8. Runner / polite fetching

- Daily cron at **12:00 UTC** (~7–8 AM ET depending on DST). GitHub cron is UTC;
  edit for a fixed local hour.
- `state.db` is **committed back** to the repo by the Action (bot commit, empty-
  commit guarded). Chosen over Actions cache/artifact for simplicity and zero
  config for a personal tool.
- HTTP client: real browser User-Agent, 20s timeouts, 3 retries with exponential
  backoff, retryable on 429/5xx (incl. Workday's POST). Firms are fetched
  sequentially (small, polite concurrency).

## 9. Deferred / not done

- Actual ATS classification of the 81 firms (blocked by sandbox network; run
  `classify.py`).
- Completing the Vault tail to a full ~90–100 firm list (blocked by paywalls).
- Weekly heartbeat email; web UI over `state.db` (interface left in place).
- Verifying the flagged boutique/merger firms' actual list membership.
