# BigLaw 3L / Entry-Associate Posting Monitor

Monitors the public careers pages of large US law firms (the **Vault Law 100 ∩
Am Law 200** intersection) and emails a **daily digest** when new *3L /
first-year / entry-level associate* postings appear.

Personal, low-traffic tool. Optimized for reliability and a low false-positive
rate — not scale.

---

## How it works

```
firms.yaml            # registry: name, ats_type, per-ATS identifiers
config.yaml           # http settings + include/exclude keyword filters (tunable)
fetchers/
  greenhouse.py       # boards-api.greenhouse.io public JSON
  lever.py            # api.lever.co public JSON
  workday.py          # POST .../wday/cxs/{tenant}/{site}/jobs (JSON, paginated)
  generic.py          # HTML fallback: schema.org JobPosting JSON-LD (+ optional Playwright)
core/
  models.py           # the normalized Posting shape
  normalize.py        # raw ATS payload -> {firm, job_id, title, location, url, posted_date, ats}
  filter.py           # include/exclude keyword + class-year logic
  diff.py             # SQLite: track seen (firm, job_id)
  notify.py           # email digest (SMTP) behind a small Notifier interface
  http.py             # polite client: real UA, timeouts, retry+backoff
main.py               # orchestrates: fetch -> normalize -> filter -> diff -> notify
classify.py           # helper: auto-detect each firm's ATS from its careers page
```

**Core architecture principle:** one fetcher per ATS backend, *not* one scraper
per firm. Each firm is classified once (by `ats_type` in `firms.yaml`) and routed
to the matching fetcher at runtime. Firms with `ats_type: unknown` are skipped
(never crash the run).

The pipeline per run:

1. **Fetch** each firm via its ATS fetcher (per-firm `try/except` — one firm
   failing never aborts the run).
2. **Normalize** every posting to `{firm, job_id, title, location, url,
   posted_date, ats}`.
3. **Filter** by include/exclude keywords + class-year regexes (see below).
4. **Diff** against `state.db` — a posting is *new* if `(firm, job_id)` hasn't
   been seen **and** it passes the filter. Re-running the same day emails
   nothing (idempotent).
5. **Notify** — one grouped-by-firm email digest. Silent on empty days.

---

## Coverage: what actually gets polled

`firms.yaml` ships with **73 firms** (the best-effort Vault-100 ∩ Am-Law-200
intersection) already **classified by ATS**. Classification was derived from URLs
seen in web-search results (the build environment had no direct egress), so it's
good but **not live-verified**.

Only three ATS backends have public JSON fetchers, so the monitor actively polls:

- **1 Greenhouse** firm (Fried Frank). *(Gibson Dunn's `gibsondunn` board was
  live-verified to be staff-only — no attorney/associate pipeline — and moved to
  `other`; its real attorney apply path is a custom/viRecruit portal.)*
- **20 Workday** firms (Skadden, Simpson Thacher, Weil, Cooley, Dechert, King &
  Spalding, Fenwick, Goodwin, McDermott, Hogan Lovells, Norton Rose Fulbright,
  DLA Piper, Alston & Bird, Morgan Lewis, Holland & Knight, Munger Tolles,
  Perkins Coie, Wilson Sonsini, Gunderson, Troutman) — all with `workday_host`
  pinned so no data-center probing is needed.

The other ~51 firms use `flo_recruit` / `virecruit` / `viglobal` / iCIMS / Taleo
/ custom portals (`other`) or are `unknown` — they're **recognized but skipped**
because there's no public API to poll. This is the coverage ceiling: most BigLaw
first-year hiring runs through OCI / school-gated portals, not a public feed. See
`DECISIONS.md` §4.

**Verify / refresh the classification** (optional but recommended) from any
machine with open outbound HTTPS:

```bash
python classify.py            # dry report: what ATS it detects per firm
python classify.py --write    # detect AND update firms.yaml in place
```

Or trigger the **`classify-firms`** GitHub Action in your fork (Actions →
classify-firms → Run workflow) — it re-probes and commits `firms.yaml`.
`classify.py` never guesses tokens; firms it can't fingerprint stay `unknown`.

---

## Run locally

```bash
pip install -r requirements.txt

# Validate end-to-end WITHOUT sending mail or touching state:
python main.py --dry-run

# One firm, verbose (shows every fetched-but-filtered posting at DEBUG).
# Use a Greenhouse/Workday firm -- others have no public fetcher and just skip:
python main.py --firm "Cooley" --dry-run -v

# Seed state on first setup so the first real run doesn't email the whole
# current backlog:
python main.py --seed

# Real run (needs SMTP env vars, see below):
python main.py
```

CLI flags: `--dry-run`, `--seed`, `--firm NAME` (repeatable), `--limit N`,
`--config`, `--firms`, `--db`, `-v/--verbose`.

---

## Email / secrets

Delivery is SMTP, configured entirely via environment variables — **no secrets
in code**. A Gmail app-password works well.

| Env var      | Required | Notes |
|--------------|----------|-------|
| `SMTP_HOST`  | yes      | e.g. `smtp.gmail.com` |
| `SMTP_PORT`  | no       | default `587` (STARTTLS). `465` → implicit TLS |
| `SMTP_USER`  | no*      | required if your server needs auth (Gmail does) |
| `SMTP_PASS`  | no*      | Gmail: an **app password**, not your login password |
| `EMAIL_TO`   | yes      | recipient; comma-separated for multiple |
| `EMAIL_FROM` | yes      | sender address |

In GitHub Actions, set these as **repository secrets** (Settings → Secrets and
variables → Actions). The workflow maps each secret to the matching env var.

---

## Scheduled runs (GitHub Actions)

`.github/workflows/monitor.yml` runs daily at **12:00 UTC** (= 8:00 AM ET during
EDT / 7:00 AM ET during EST — GitHub cron is always UTC; edit the cron for a
fixed local hour) and also supports **manual runs** (`workflow_dispatch`).

State persistence: the workflow commits the updated `state.db` back to the repo
after each run (bot commit, guarded against empty commits) so the next run diffs
against it. Simple and free for a personal tool; swap to an Actions cache/artifact
if you'd rather not commit a binary.

> **Activating the schedule:** GitHub only runs workflows found at the repository
> root's `.github/workflows/`. If you keep this project as a subdirectory, move
> it to its own repo (or hoist `.github/` to the repo root) for the cron to fire.

---

## Tuning the filter

All keyword/regex lists live in `config.yaml` under `filters:` — no code edits
needed.

- **Include** (any match): `first-year associate`, `entry-level associate`,
  `new grad`, `3L`, `entering class`, plus class-year regexes (a near-future
  4-digit year next to "associate"/"class", e.g. `Class of 2027`).
- **Exclude** (any match wins over include): `lateral`, `partner`, `counsel`,
  `staff`, `paralegal`, `secretary`, `patent agent`, `librarian`, `law clerk`,
  `intern`.
- **Summer associate**: excluded by default; flip `include_summer_associate:
  true` to include (this promotes the summer keywords to *includes*).
- Short tokens like `3L` are matched on word boundaries so they don't match
  inside unrelated words.

Every fetched-but-filtered posting is logged at DEBUG (`-v`) so you can audit the
false-negative rate.

---

## Adding / reclassifying a firm

Edit `firms.yaml`:

```yaml
- name: "Example LLP"
  careers_url: "https://www.example.com/careers"
  ats_type: greenhouse          # greenhouse | lever | workday | generic | unknown
  ats_identifier: "examplellp"  # GH board token | Lever slug | "tenant/site" (Workday)
  public_entry_level: unknown
  note: ""
```

Per-ATS `ats_identifier`:

- **greenhouse** — the board token (`boards.greenhouse.io/{token}`)
- **lever** — the company slug (`jobs.lever.co/{company}`)
- **workday** — `"tenant/site"`. Optionally pin `workday_host:
  tenant.wdN.myworkdayjobs.com` to skip data-center probing.
- **generic** — leave `null`; the fetcher reads `careers_url` and extracts
  schema.org `JobPosting` JSON-LD. For a truly JS-rendered page add
  `render: playwright` (and install Playwright — see `requirements.txt`).

Or just run `python classify.py --firm "Example LLP" --write`.

---

## Testing

```bash
python -m pytest -q
```

Unit tests cover normalization, filtering, diffing, the generic JSON-LD parser,
the ATS-detection logic, and the full orchestration (with a fake fetcher — no
network).
