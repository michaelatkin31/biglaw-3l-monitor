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

## 2. No network in the build environment → classification is search-derived, not live-verified

Outbound HTTPS in the build sandbox was blocked by org egress policy (HTTP 403
on every host, including the Greenhouse/Lever APIs, Workday, and every firm's
site). So no careers page could be fetched *directly* here.

Classification was instead done via **WebSearch** (which routes through separate
infrastructure): research sub-agents read each firm's ATS host **out of the URLs
that appear in search results** — e.g. a search hit whose link is
`skadden.wd5.myworkdayjobs.com/Skadden_Careers` yields tenant `skadden`, site
`Skadden_Careers`, host `skadden.wd5...`. No tokens were guessed; an identifier
is recorded only where the actual URL was seen. This classified **66 of 73
firms** (see `firms.yaml` header for the breakdown). All 20 Workday firms have
their `workday_host` pinned (parsed from the observed URL), so the Workday
fetcher never has to probe data-center subdomains.

**Caveat: this data is search-derived, not confirmed against a live fetch.**
Some entries are explicitly low/medium confidence (e.g. Gibson Dunn's Greenhouse
token was seen on a *staff* posting; Baker Botts' Flo classification is weak).
So `classify.py` remains in the repo as the **verification/refresh** tool: run it
from any machine with open egress (or the `classify-firms` Action) to re-probe
each careers page and confirm/correct `ats_type` / `ats_identifier` /
`workday_host` in place. It was originally built to bootstrap classification;
with the search-derived data already in place its role is now to verify and keep
it fresh.

## 3. Firm list: source, date, and intersection caveats

Target set = **Vault Law 100 ∩ Am Law 200**.

- **Vault Law 100** — 2026 edition (released mid-2025), `vault.com`. Gated;
  could not be enumerated in full from search snippets.
- **Am Law 200** — 2025 edition (2024 gross revenue), `law.com/americanlawyer`.
  Paywalled; not enumerable in full.

Because both lists are gated, `firms.yaml` is a **best-effort intersection**:
**73 firms** — the high-confidence core (firms confidently on *both* lists),
reconstructed from confirmed search anchors + domain knowledge. The Vault "tail"
(roughly ranks 70–100) could not be enumerated from search snippets, so the list
is likely **short by ~10–20 firms** of the full intersection (the spec's expected
~80–100). Rather than pad it with speculative unknowns, I kept the defensible
core and documented the gap. Sources + dates are at the top of `firms.yaml`;
the full per-firm breakdown is in that file's `coverage_summary`.

Firms **flagged** rather than silently dropped/kept (see inline `note:` fields):

- **Magic Circle firms excluded**: Freshfields, Clifford Chance, Linklaters are
  Vault-ranked but London-HQ and tracked in the Global 200, generally **not** in
  the Am Law 200 → excluded from the intersection (noted where researched).
- **A&O Shearman**: kept but flagged — post-2024-merger US revenue reporting is
  ambiguous; may or may not belong.
- **Kramer Levin / HSF Kramer**: flagged — after its merger it likely dropped out
  of the current Vault 100.
- **10 large-revenue firms** (Gunderson, Foley, Baker McKenzie, Greenberg
  Traurig, DLA Piper, Reed Smith, Troutman Pepper Locke, Crowell & Moring, Morgan
  Lewis, Holland & Knight) are solidly Am Law 100/200 but their *Vault-100*
  placement couldn't be independently confirmed — flagged in `coverage_summary`
  for re-verification against the full Vault list.
- **Tail firms not enumerable** (Keker Van Nest, Kobre & Kim, Nixon Peabody,
  Venable, Seyfarth, Duane Morris, Faegre Drinker, Ballard Spahr, …) are named in
  `coverage_summary` as known-missing candidates so the gap is explicit.

## 4. Known coverage ceiling (the risk the spec asked me to record)

A meaningful share of big-law entry-level hiring flows through **OCI / Symplicity
/ Flo Recruit / viRecruit** school-gated or vendor portals and **never appears on
a publicly queryable careers API**. The classified data makes this ceiling
concrete: of the 73 firms, only **~22 sit on a fetcher-supported public ATS**
(2 Greenhouse + 20 Workday) — the monitor actively polls those. The remaining
~51 are `flo_recruit` (10), `virecruit` (4), `viglobal` (1), or `other` (29,
mostly iCIMS / Taleo / custom / email) plus 7 `unknown`, and are **skipped at
runtime** because there is no public JSON endpoint to poll. `public_entry_level`
is `true` for ~55 firms (they surface summer/entry/3L programs on some public
page) but that page usually isn't machine-pollable. **Net: public-API coverage
of entry-level roles is inherently partial — expect the monitor to catch new
postings only at the Greenhouse/Workday firms.** Extending coverage would mean
adding fetchers for Flo Recruit / viRecruit / iCIMS / Taleo (several are
scriptable) — deferred (see §9).

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
- **Generic fetcher** extracts schema.org `JobPosting` in both standard
  encodings: `application/ld+json` blocks (what most ATSs emit for Google-for-Jobs
  SEO) and inline **microdata** (`itemtype=".../JobPosting"` + `itemprop` on the
  rendered cards). The microdata path is what makes some WordPress careers
  front-ends pollable over plain HTTP — e.g. Kilpatrick's
  `kilpatrickrecruits.com/open-positions/` mirrors its iCIMS jobs into static
  microdata cards, so it needs no browser despite an earlier note calling it "not
  pollable." Playwright is **optional** and used only for firms explicitly marked
  `render: playwright`, to keep the CI run light.

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

- **Live verification** of the search-derived ATS data (run `classify.py` /
  the `classify-firms` Action from an environment with open egress).
- **Fetchers for gated/vendor ATSs** (Flo Recruit, viRecruit, iCIMS, Taleo) so
  the ~51 currently-skipped firms could be polled — several are scriptable.
- Completing the Vault tail to the full ~80–100 firm list (blocked by paywalls).
- Re-confirming the 10 flagged large-firm Vault placements and the
  merger/boutique edge cases against the full Vault list.
- Weekly heartbeat email; web UI over `state.db` (renderer interface left in
  place for it).
