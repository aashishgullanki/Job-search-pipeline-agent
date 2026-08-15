# NYC Job Search Pipeline Agent — Architecture Plan

## Purpose

Automate the job search pipeline end-to-end: discover new postings across (a) a curated target-company list and (b) the broader NYC market, score them for fit, tailor a resume for top matches, pre-fill the application, and surface it for a one-click review/accept. Nothing gets submitted without explicit approval.

---

## 1. High-Level Pipeline

```
┌─────────────┐     ┌──────────┐     ┌───────────┐     ┌────────────┐     ┌───────────┐
│  DISCOVERY  │ --> │  FILTER  │ --> │   SCORE    │ --> │  TAILOR    │ --> │ FORM-FILL │
│  (multi-src)│     │ (hard    │     │ (LLM fit   │     │ (resume +  │     │ (pre-fill,│
│             │     │  rules)  │     │  judge)    │     │  outreach) │     │  no submit│
└─────────────┘     └──────────┘     └───────────┘     └────────────┘     └───────────┘
                                                                                  │
                                                                                  v
                                                                        ┌───────────────────┐
                                                                        │  REVIEW DASHBOARD  │
                                                                        │  (you click accept)│
                                                                        └───────────────────┘
```

Every stage writes to SQLite. Every stage is independently re-runnable and idempotent (dedup by job ID / URL hash).

---

## 2. Discovery Sources (merged design)

Three parallel discovery tracks feed the same downstream pipeline:

### Track A — Target Company ATS Polling (from tier list) ✅ Built
Direct, structured polling of the 56 tier-list companies confirmed on a standard ATS, each tagged in config with its ATS type:

| ATS type | Method | Notes |
|---|---|---|
| `greenhouse` | GET `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | 36 companies |
| `ashby` | GET public Ashby board API | 3 companies |
| `workday` | POST `{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` | 17 companies. Paginate in steps of 20, delay between pages, retry an empty page before treating it as terminal |

Company→ATS mapping lives in `config/companies.yaml`, filled in during the audit step (tenant, site, slug per company).

**Live-verified quirks found only through hand-probing (not documented by Workday anywhere), now baked into the pagination logic:**
- `total` (the reported result count) goes stale/zero deep into a board — used only as a first-page estimate, never as the sole "done" signal
- Requesting an offset past the end doesn't return empty — it silently wraps around and re-serves page 1. Termination is based on "page shorter than requested," with the total-based estimate as a second guard that stops before ever requesting the offset that triggers the wraparound

**Status:** 56/56 companies, unit-tested against fixtures (no network), live end-to-end verified with two passes to confirm idempotency (14,521 postings first pass across Workday alone, 14,520/14,521 already-seen on the second — the 1 "new" was real turnover on a live board between runs, not a dedup bug).

### Track C — Direct Company Monitoring ✅ Built
Fallback for the 32 companies without a clean ATS (custom in-house career sites, e.g. big tech, big banks) — guarantees every company on the list gets *some* form of check, even without a structured API.

**Actual implementation (deviates from earlier design speculation, based on what live pages showed):**
- **Hashes normalized visible text, not raw HTML or embedded SPA state.** Embedded JSON state (e.g. Next.js `__NEXT_DATA__`) was tested first as a cleaner signal and rejected — tested live on Goldman Sachs, it contained no job data but did contain a `buildId` that changes on every redeploy, which would false-positive constantly.
- **Full normalized text is stored alongside the hash**, not just the hash itself, so changes are diffable, not just detectable.
- **Change detection is diff + keyword-gate, deterministic** — not a live Claude web-search call as originally proposed in this doc. Chosen for zero added per-run cost and full fixture-testability.
- **Noise scrubbing:** Snowflake and eBay (both Phenom-People-backed career platforms) regenerate a 32-character hex token in visible text on every fetch, which would have kept both permanently flagged "changed" with no real signal. Fixed by scrubbing 16+ character hex runs before hashing.
- **Alerts write to a separate `company_monitor_alerts` table, never into `postings`.** Track C output is inherently lower-confidence — no structured title/URL is reliably extractable from 32 bespoke page layouts — so it gets its own review surface instead of polluting the main discovery pipeline.
- **`low_confidence` flag:** 10 companies with client-rendered shells (e.g. Oracle, JPMorgan's Oracle Fusion UI, Goldman Sachs) are explicitly flagged rather than papered over — text-hashing can't reliably see their listings at all, so a monitor runs but may rarely or never catch a real change. This is a known, accepted limitation, not a bug.

**Status:** 32/32 companies, 34 unit tests against fixtures, two live passes across all companies. 28 of 29 successfully-fetched companies report clean `unchanged` between passes after the hex-token fix; the one residual (Spotify) is already correctly flagged `low_confidence`.

**Open question, not yet decided:** whether the 10 `low_confidence` companies are worth a heavier Playwright-based (real browser rendering) monitor eventually, or whether that gap is accepted as-is.

### Track B — Broad NYC Market Scrape ⬜ Not yet built
- Apify LinkedIn jobs scraper, built fresh for this project — casts a wider net beyond the tier list, catches NYC SWE/AI Engineer roles at companies not on your radar.
- **Agentic feature — adaptive scrape broadening:** if a scrape returns too few results, the agent automatically widens search terms/radius before giving up, rather than just reporting "nothing found."
- Writes into the same `postings` table as Track A, tagged `source: linkedin`.

### Coverage Matrix ✅ Passing
A table in `config/companies.yaml` mapping **every one of the 88 companies** (89 minus the Microsoft/VS Code dedup) to exactly one track (A or C — Track B is supplementary, not part of the per-company guarantee) plus its config values. The build includes a check that fails loudly if any company has no track assigned. This is what actually prevents a company silently falling through the cracks, not the discovery mechanism itself.

**Current state: Track A (56) + Track C (32) = 88/88 covered. Coverage-matrix check passes.**

---

## 3. Filter Stage ⬜ Not yet built

Cheap, deterministic, no LLM call:
- Location contains "New York, NY", excludes remote-only unless desired
- Employment type = full-time
- Title matches SWE/AI Engineer keyword set
- Dedup against `postings` table by job ID/URL hash — never re-process a seen posting

---

## 4. Score Stage (LLM-as-judge) ⬜ Not yet built

- Model: **Claude Haiku 4.5** (cheap, fast, good enough for a fit judgment)
- Input: job description + your profile summary (background, stack, target roles)
- Output: fit score (1–10) + 2–3 sentence reasoning
- **Agentic feature — LLM contextual filtering:** replaces brittle keyword exclusion (e.g. filtering out federal/public-sector roles) with judgment calls, since keyword rules miss edge cases
- Only postings above a score threshold move to the tailor stage

---

## 5. Tailor Stage (top matches only) ⬜ Not yet built

- Model: **Claude Sonnet** (higher quality for writing)
- Reuses your existing `resume-tailor` skill prompt verbatim
- Compiles LaTeX → PDF via `pdflatex`, enforces one-page output
- **Compile-loop:** up to 3 retry attempts to fix overflow/errors before flagging for manual review
- Also drafts an outreach message to a recruiter/HM contact if one was found (Track B only, via Apify LinkedIn post-search actors, following your existing manual search recipe — query pattern `"hiring" AND "software engineering" AND "new york"`, filtered by company, up to 3 contacts per match)
- **Agentic feature — adaptive contact search retries:** widens/retries the search query if the first pass returns no contacts

---

## 6. Form-Fill Stage ⬜ Not yet built

- Browser automation (e.g. Playwright) opens the application form and pre-fills fields from your profile + the tailored resume
- **Hard stop before submit** — this is a non-negotiable guardrail in both the subagent's tool permissions and the code itself. The agent prepares; it never clicks submit.
- Screenshot/state saved for the review dashboard

---

## 7. Review Dashboard ⬜ Not yet built

- Simple local dashboard (could start as a markdown/HTML digest, later a small web UI) showing: company, role, fit score + reasoning, tailored resume PDF, draft outreach message, pre-filled form state
- Also needs to surface `company_monitor_alerts` (Track C) as a separate, lower-confidence feed distinct from the main scored-posting list
- You review and click "Accept" → opens the browser session for final manual submit, or (later, once trusted) triggers the actual submit action
- Daily/periodic digest delivered via email (SendGrid) or Slack

---

## 8. Storage Schema (SQLite)

- `postings` — id, source, company, title, url, location, posted_at, first_seen_at, raw_json
- `company_monitor_alerts` — id, company, changed_at, diff_summary, confidence (`normal`/`low_confidence`) — **Track C output, kept separate from `postings`**
- `scores` — posting_id, score, reasoning, scored_at
- `tailored` — posting_id, resume_pdf_path, outreach_draft, tailored_at
- `applications` — posting_id, status (`pending_review` / `accepted` / `submitted` / `rejected`), reviewed_at

---

## 9. Orchestration & Infra

- **Scheduling:** GitHub Actions cron (daily) — stateless, no persistent server needed
- **Language:** Python throughout (requests, sqlite3, Apify client)
- **Anthropic auth:** `CLAUDE_CODE_OAUTH_TOKEN` (generated locally via `claude setup-token`, tied to your Pro/Max subscription), not a raw `ANTHROPIC_API_KEY` — officially supported by `anthropics/claude-code-action`, draws from subscription rate limits instead of metered billing
  - Token can expire/invalidate if you log out of Claude Code locally — build in a periodic reminder to regenerate and refresh the GitHub secret
  - Shares your subscription's rate-limit pool with interactive use — watch for contention on heavy pipeline days
- **Secrets:** GitHub Actions secrets for `CLAUDE_CODE_OAUTH_TOKEN`, Apify token, SendGrid key
- **Deployment note:** SQLite state must persist across GitHub Actions runs (runners are ephemeral) — commit the DB file back to the repo each run, or use an external lightweight store. Confirm this is handled before step 11.

---

## 10. Claude Code Subagent Structure

| Subagent | Role | Tools | Status |
|---|---|---|---|
| `ats-discovery` | Polls Greenhouse/Ashby/Workday, diffs vs DB, returns only new postings | Bash, WebFetch, Read | ✅ Built |
| `company-monitor` | Hashes/diffs career pages for Track C companies, writes to `company_monitor_alerts` | Bash, WebFetch, Read | ✅ Built |
| `linkedin-discovery` | Runs Apify actor, handles adaptive broadening | Bash, Read | ⬜ Not built |
| `fit-scorer` | Scores filtered postings against profile | Read (no Bash needed) | ⬜ Not built |
| `resume-tailor` | Edits LaTeX copy, compiles PDF, retry loop | Read, Write, Edit, Bash | ⬜ Not built |
| `form-filler` | Playwright pre-fill, screenshot, stop before submit | Read, Bash (scoped, no submit action ever coded in) | ⬜ Not built |

Each subagent scoped narrowly per the "bounded work" principle — no generalist agent trying to do everything.

---

## 11. Build Order (MVP → full pipeline)

1. ✅ **Company ATS audit** — 88/88 companies assigned a track, coverage-matrix check passes
2. ✅ **Discovery (Track A)** — Greenhouse (36) + Ashby (3) built and verified first, then Workday (17) built separately, including live-probed pagination/wraparound handling
3. ✅ **Discovery (Track C)** — hash-diff monitoring for the 32 non-ATS companies, deterministic diff+keyword-gate, `company_monitor_alerts` table, `low_confidence` flagging for 10 client-rendered shells
4. ⬜ **Discovery (Track B)** — build Apify LinkedIn scraper logic fresh — *next up*
5. ⬜ **Filter stage** — hard rules + dedup
6. ⬜ **Score stage** — Haiku fit judge
7. ⬜ **Tailor stage** — resume-tailor skill + compile loop
8. ⬜ **Outreach draft** (Track B matches only)
9. ⬜ **Form-fill stage** — Playwright pre-fill, hard-stop guardrail
10. ⬜ **Review dashboard + digest delivery** — must surface both `postings`-based scored matches and `company_monitor_alerts` as distinct feeds
11. ⬜ **Wrap in GitHub Actions cron**

---

## 12. Known Gaps / Fallbacks to Revisit Later

- The 10 `low_confidence` Track C companies (client-rendered shells: Oracle, JPMorgan's Oracle Fusion UI, Goldman Sachs, etc.) can't be reliably text-hashed — open question whether a heavier Playwright-based monitor is worth building for them later, or whether this gap is accepted
- Quant firm hiring cycles are often batch/annual rather than rolling — may be better served by a calendar reminder than pure hash-diffing for those specific companies
- Form-fill submit action stays manual until the pipeline has a track record you trust