# NYC Job Search Pipeline Agent — Architecture Plan

## Purpose

Automate the job search pipeline end-to-end: discover new postings across (a) a curated target-company list and (b) the broader NYC market, score them for fit, tailor a resume for top matches, and surface it for review. Form-fill/auto-apply is explicitly out of scope (see note below) — the pipeline's job ends at giving you a reviewed, tailored, ready-to-submit package; you apply manually from there.

---

## 1. High-Level Pipeline

```
┌─────────────┐     ┌──────────┐     ┌───────────┐     ┌────────────┐
│  DISCOVERY  │ --> │  FILTER  │ --> │   SCORE    │ --> │  TAILOR    │
│  (multi-src)│     │ (hard    │     │ (LLM fit   │     │ (resume +  │
│             │     │  rules)  │     │  judge)    │     │  outreach) │
└─────────────┘     └──────────┘     └───────────┘     └────────────┘
                                                               │
                                                               v
                                                     ┌───────────────────┐
                                                     │  REVIEW DASHBOARD  │
                                                     │  (you apply manually)│
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
- Greenhouse's `posted_at` originally pulled the wrong field (`updated_at` instead of `first_published`) — fixed and backfilled

**Status:** 56/56 companies, unit-tested against fixtures (no network), live end-to-end verified with two passes to confirm idempotency.

### Track C — Direct Company Monitoring ✅ Built
Fallback for the 32 companies without a clean ATS (custom in-house career sites, e.g. big tech, big banks) — guarantees every company on the list gets *some* form of check, even without a structured API.

**Actual implementation (deviates from earlier design speculation, based on what live pages showed):**
- **Hashes normalized visible text, not raw HTML or embedded SPA state.** Embedded JSON state (e.g. Next.js `__NEXT_DATA__`) was tested first as a cleaner signal and rejected — tested live on Goldman Sachs, it contained no job data but did contain a `buildId` that changes on every redeploy, which would false-positive constantly.
- **Full normalized text is stored alongside the hash**, not just the hash itself, so changes are diffable, not just detectable.
- **Change detection is diff + keyword-gate, deterministic** — not a live Claude web-search call as originally proposed. Chosen for zero added per-run cost and full fixture-testability.
- **Noise scrubbing:** Snowflake and eBay (both Phenom-People-backed career platforms) regenerate a 32-character hex token in visible text on every fetch, which would have kept both permanently flagged "changed" with no real signal. Fixed by scrubbing 16+ character hex runs before hashing.
- **Alerts write to a separate `company_monitor_alerts` table, never into `postings`.** Track C output is inherently lower-confidence — no structured title/URL is reliably extractable from 32 bespoke page layouts — so it gets its own review surface instead of polluting the main discovery pipeline.
- **`low_confidence` flag:** 10 companies with client-rendered shells (e.g. Oracle, JPMorgan's Oracle Fusion UI, Goldman Sachs) are explicitly flagged rather than papered over — text-hashing can't reliably see their listings at all, so a monitor runs but may rarely or never catch a real change. This is a known, accepted limitation, not a bug.

**Status:** 32/32 companies, 34 unit tests against fixtures, two live passes across all companies verified.

**Open question, not yet decided:** whether the 10 `low_confidence` companies are worth a heavier Playwright-based (real browser rendering) monitor eventually, or whether that gap is accepted as-is.

### Track B — Broad NYC Market Scrape ✅ Built
- Apify LinkedIn jobs scraper (`curious_coder/linkedin-jobs-scraper`), built fresh for this project — casts a wider net beyond the tier list, catches NYC SWE/AI Engineer roles at companies not on your radar.
- **Agentic feature — adaptive scrape broadening:** if a scrape returns too few results, the agent automatically widens search terms/radius before giving up, rather than just reporting "nothing found."
- Writes into the same `postings` table as Track A, tagged `source: linkedin`.
- **Live-verified bug found and fixed:** LinkedIn's `link` URL embeds per-search tracking params (`refId`, `trackingId`, `position`) that regenerate on every fresh search of the same query, even for the exact same job — dedup was keying off this raw link and treating every re-fetch as new. Fixed by keying off the stable `id` field instead.
- Idempotency confirmed live after the fix.

### Coverage Matrix ✅ Passing
A table in `config/companies.yaml` mapping **every one of the 88 companies** (89 minus the Microsoft/VS Code dedup) to exactly one track (A or C — Track B is supplementary, not part of the per-company guarantee) plus its config values. The build includes a check that fails loudly if any company has no track assigned.

**Current state: Track A (56) + Track C (32) = 88/88 covered. Coverage-matrix check passes.**

---

## 3. Filter Stage ✅ Built

Cheap, deterministic, no LLM call:
- **Location:** handles Greenhouse's spelled-out state format and Ashby/Workday's bare-city format via split-on-`;`-then-first-comma-segment matching. Remote-paired NYC roles (e.g. "New York, NY; Remote") correctly pass.
- **Employment type:** only Ashby and LinkedIn expose a structured field; Greenhouse/Workday expose none, so this falls back to a title heuristic rather than defaulting to exclude (which would've wrongly gutted most of Track A).
- **Title:** matches Software Engineer / SWE / Software Developer / Backend Engineer keyword set, word-boundary matched, plus narrow AI/ML-specific keywords (AI Engineer, AI Research Engineer, ML Engineer, Deep Learning Engineer, bare "Research Engineer") added after auditing real excluded postings — deliberately does NOT include bare "Data Engineer," which real samples showed pulls in infra/pipeline noise rather than real matches. Excludes Senior/Sr./Staff/Principal/Manager/Lead.
- **Staleness/recency (added later, a real goal-critical piece):** LinkedIn postings must be ≤24h old; Greenhouse/Ashby/Workday postings must be ≤14 days old (using a real posted-date field, not `first_seen_at`). Workday's imprecise "30+ Days Ago" bucket and postings with no date at all are excluded outright, per an explicit choice to prioritize application-timing realism (a 30+ day old posting has likely already drawn heavy applicant volume) over passing through unconfirmable data.
  - **Open follow-up, not yet resolved:** postings with no date at all (e.g. Blackstone) were meant to be flagged `low_confidence`/unverified-age and kept visible, consistent with Track C's pattern — needs confirming this was actually implemented that way rather than flatly excluded.
- Dedup against `filter_results` table — a posting already filtered (pass or fail) is never re-evaluated.

**Status:** live-verified against the full postings table. Current real numbers: staleness excludes the large majority of volume (most Track A volume is huge companies posting globally, not NYC-specific), location excludes most of what survives staleness, title/seniority trims the rest — real end-to-end funnel confirmed, not just unit-tested.

---

## 4. Score Stage (LLM-as-judge) ✅ Built

- Model: **Claude Haiku 4.5**
- Input: job description + candidate profile extracted from `resumes/Baseline Resume.tex` via a LaTeX-macro-aware parser
- Output: structured `{score, reasoning}` via forced tool-use (not regex-parsed free text)
- **Agentic feature — LLM contextual filtering:** confirmed working live — e.g. a BlackRock "Rust, AI Engineer, Director" posting passed the deterministic Filter (title matched, "Director" wasn't in the keyword exclusion list), but Haiku correctly scored it 3/10 for the seniority mismatch. This is the exact judgment call this stage exists to make.
- Hand-checked (not just unit-tested): sampled scores across the full range confirmed specific, grounded reasoning on both sides — no hallucinated claims, no generic filler.

**Status:** live-verified, idempotent, hand-checked.

---

## 5. Tailor Stage (score ≥8 only) ✅ Built

- Model: **Claude Sonnet**
- **Full fidelity to the real `resume-tailor` skill** (not a simplified version) — keyword categorization (🔴 technical / 🔵 soft / 🟡 tools-frameworks / 🟢 hard requirements), action-verb leads, bullet reordering within each experience block so the most relevant leads, Technical Skills updates with matched tools, required Tailoring Summary per posting.
- **Two programmatic guardrails**, not just prompt instructions:
  - Fabrication check — rejects any proposed skill not already present in the source resume (confirmed live: correctly rejected a proposed "Docker" for a posting where it wasn't genuinely on the resume)
  - Coursework-annotation preservation — auto-restores `(coursework: ...)` tags if the LLM drops them, so C++ and similar coursework-only skills never get implied as production experience
- **One page is a hard constraint.** Compile-loop only permits dropping a bullet below the original count after an overflow is actually observed, never pre-emptively. If a Skills-augmented resume doesn't fit, trims the lowest-priority addition rather than shrinking font or shipping over one page.
- Block-level reordering (moving whole project/job sections relative to each other) is explicitly out of scope — the splice architecture only reorders bullets within a block. A real bug where the Tailoring Summary falsely claimed a block-level move happened was found via audit and fixed (prompt constraint + `validate_no_block_reorder_claims()` regression check against all real summary lines).
- **Real bug found only via live compile testing:** a Skills-section splice bug left the `itemize` environment with no `\item` command at all — all 84 mocked unit tests passed since they never verified the surrounding LaTeX stayed valid. Fixed, and a real-pdflatex integration test added specifically to catch this bug class going forward.
- **Differentiation audited, not assumed:** built from real `.tex` output, not trusted summary text. Confirmed genuinely different leading bullets and Technical Skills ordering across different role types (e.g. AI-heavy postings surface AI/ML skills first; general SWE postings don't). Cases of identical output across postings were explained, not hand-waved (e.g. two very similar generic "Software Engineer" postings producing an identical Skills line is expected, not a bug).

**Status:** live-verified, audited for real differentiation, one real bug found and fixed with regression coverage.

**Cost-control practice adopted going forward:** verify any fix or new feature needing a live LLM call against 3-5 representative postings first, only scale to full batch once confirmed — adopted after Tailor-stage debugging drove repeated full-batch re-runs and higher-than-expected Console API spend.

---

## 6. Outreach Draft (Track B matches only) ✅ Built — unit-tested, live verification pending real Track B data

- Model: **Claude Haiku 4.5** (switched from Sonnet to control API cost — a short outreach message doesn't need Sonnet-level writing quality the way a tailored resume does)
- For Track B (LinkedIn-sourced) postings only, and only ones that already have a successfully tailored resume — Track A/C postings are direct applications through your named companies, where outreach matters less
- Contact discovery via Apify LinkedIn post-search (`curious_coder/linkedin-post-search-scraper`, same publisher as the jobs-scraper actor; `"hiring" AND "software engineering" AND "new york"`, filtered by company, up to 3 contacts per match), reusing `run_apify_actor()` from the same Apify integration already built for Track B
- **Agentic feature — adaptive contact search retries:** widens once (drops the location phrase) on a zero-raw-result search before giving up, same pattern as Track A/B/C's existing retry logic
- Drafts a short outreach message per posting with a found contact, stored in `tailored.outreach_draft` (overwrites the generic contact-less draft the Tailor stage's own skill already writes there)
- Deliberately prioritized over Form-Fill: reuses existing infra (Apify, and Haiku rather than Sonnet), additive rather than blocking, no irreversible-action risk
- 60 unit tests, all mocked — no live run yet since zero Track B postings are currently tailored; deferred until real ones exist in a future session

---

## 7. Review Dashboard ✅ Built

- Generated markdown digest (`src/dashboard/`), no new schema — reuses the existing `applications` table for status tracking
- Surfaces three things:
  1. Score ≥8 postings with tailored resume PDF link (into `reviewed_output/`), fit reasoning, Tailoring Summary
  2. Score 2-7 postings (the "review list") — company, title, score, reasoning, link — for manual judgment calls, not auto-tailored
  3. Track C `company_monitor_alerts`, kept clearly separate with a ⚠ heading and "not a confirmed job posting" caveat
- "Accept" mechanism: a real CLI command (`set_application_status.py`) shown inline per posting, status rendered as a markdown checkbox — reuses the `applications` table rather than inventing new state
- `ensure_reviewed_output_copies()` copies tailored PDFs into `reviewed_output/` before the digest links to them, idempotently — specifically closes the gap that caused an earlier incident where a cleanup command deleted the tailored output before it had been reviewed

**Status:** built, unit-tested (33 tests). Pending: a real-data verification pass (the DB was found empty between sessions immediately after this stage was built — root cause not yet confirmed) to actually see a populated digest rendered, rather than only the empty-DB smoke test.

---

## 8. Form-Fill / Auto-Apply — OUT OF SCOPE (deliberately not building)

**Decision:** explicitly descoped. Reasoning:
- Every company's application form differs in structure, and this doesn't reuse Track A's job-board API integration — it's a separate, largely new integration challenge per ATS
- Materially higher risk than every other stage: a "never submit" guardrail has to hold up against real automation drift (sites change their DOM, scripts break silently) in a context where a mistake means an actual bad interaction with a real employer's system — a different risk category than anything else in this build
- The pipeline is already highly useful without it: discovery, scoring, and tailoring get you a reviewed, ready-to-submit package in the dashboard — clicking "apply" and pasting in an already-tailored resume is a small manual step, not the bottleneck this project set out to remove
- If revisited later, treat it as an optional, separately-scoped effort — not a blocker for calling the core pipeline done

---

## 9. Storage Schema (SQLite)

- `postings` — id, source, company, title, url, location, posted_at, first_seen_at, raw_json
- `company_monitor_alerts` — id, company, changed_at, diff_summary, confidence (`normal`/`low_confidence`) — Track C output, kept separate from `postings`
- `filter_results` — posting_id, passed, excluded_by, low_confidence_age, filtered_at — dedup for Filter stage; `low_confidence_age` flags a passing posting whose age couldn't be confirmed rather than excluding it outright (added alongside the staleness rule)
- `scores` — posting_id, score, reasoning, scored_at
- `tailored` — posting_id, resume_pdf_path, resume_tex_path, outreach_draft, tailoring_summary, attempts, failure_reason, tailored_at, plus six `outreach_*` columns (status, contact_name, contact_profile_url, source_post_url, candidate_contacts, drafted_at) added when step 6 was built — Outreach Draft overwrites `outreach_draft` for Track B postings rather than living in a separate table
- `applications` — posting_id, status (`pending_review` / `accepted` / `submitted` / `rejected`), reviewed_at — the accept mechanism Form-Fill will read from if it's ever built

---

## 10. Orchestration & Infra

- **Scheduling:** GitHub Actions cron (daily) — `.github/workflows/pipeline.yml`, **built**, not yet run in real CI (needs `ANTHROPIC_API_KEY`/`APIFY_TOKEN` configured as repo secrets first). State persistence across ephemeral runners handled via GitHub Actions artifacts (download the previous run's DB, upload the updated one), not by committing the DB to git or standing up external infra — see README's "Deployment" section for the full reasoning.
- **Language:** Python throughout (requests, sqlite3, Apify client)
- **Anthropic auth:** `CLAUDE_CODE_OAUTH_TOKEN` (generated locally via `claude setup-token`, tied to your Pro/Max subscription) for Claude Code's own operation/orchestration — officially supported by `anthropics/claude-code-action`, draws from subscription rate limits instead of metered billing
  - **Important distinction confirmed:** this token only authenticates Claude Code itself (CLI, extension, `claude-code-action`, Agent SDK) — it does NOT work for application code making direct Messages API calls, per Anthropic's own docs
  - The pipeline's own runtime calls (Score stage's Haiku calls, Tailor stage's Sonnet calls) require a genuine `ANTHROPIC_API_KEY` from console.anthropic.com, billed separately per-token — this is a real, ongoing cost distinct from the OAuth-token-covered Claude Code orchestration
  - Token can expire/invalidate if you log out of Claude Code locally — build in a periodic reminder to regenerate and refresh the GitHub secret
- **Secrets:** GitHub Actions secrets for `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, Apify token
- **Deployment note:** SQLite state must persist across GitHub Actions runs (runners are ephemeral) — commit the DB file back to the repo each run, or use an external lightweight store. Confirm this is handled before deployment.
- **Cost reality check (based on real usage, not the original estimate):** Score stage is cheap per-call (~$0.002-0.003/posting on Haiku); Tailor stage is the real cost driver (~$0.03-0.05/posting on Sonnet), especially during development where fixes required repeated full-batch re-runs. Steady-state ongoing cost once deployed is expected to be low (low single-digit dollars/month), but actual volume depends on Track B's configured search breadth, which hasn't been tuned for real deployment yet.

---

## 11. Claude Code Subagent Structure

| Subagent | Role | Tools | Status |
|---|---|---|---|
| `ats-discovery` | Polls Greenhouse/Ashby/Workday, diffs vs DB, returns only new postings | Bash, WebFetch, Read | ✅ Built |
| `company-monitor` | Hashes/diffs career pages for Track C companies, writes to `company_monitor_alerts` | Bash, WebFetch, Read | ✅ Built |
| `linkedin-discovery` | Runs Apify actor, handles adaptive broadening | Bash, Read | ✅ Built |
| `fit-scorer` | Scores filtered postings against profile | Read (no Bash needed) | ✅ Built |
| `resume-tailor` | Edits LaTeX copy, compiles PDF, retry loop, full skill fidelity | Read, Write, Edit, Bash | ✅ Built |
| `outreach-drafter` | Finds contacts via Apify, drafts outreach message (Haiku) | Bash, Read | ✅ Built |

Each subagent scoped narrowly per the "bounded work" principle — no generalist agent trying to do everything.

---

## 12. Build Order (current, revised)

1. ✅ **Company ATS audit**
2. ✅ **Discovery (Track A)** — Greenhouse + Ashby + Workday, including live-probed pagination/wraparound handling and the Greenhouse `posted_at` fix
3. ✅ **Discovery (Track C)** — hash-diff monitoring, deterministic diff+keyword-gate, `low_confidence` flagging
4. ✅ **Discovery (Track B)** — Apify LinkedIn scraper, tracking-param dedup bug found and fixed
5. ✅ **Filter stage** — hard rules, dedup, staleness/recency logic
6. ✅ **Score stage** — Haiku fit judge, hand-verified
7. ✅ **Tailor stage** — full-fidelity resume-tailor skill, audited, one real bug fixed
8. ✅ **Outreach draft** (Track B matches only) — built, unit-tested; live run deferred until real Track B postings are tailored
9. ~~Form-fill stage~~ — **descoped, treated as optional/later** (see section 8)
10. ✅ **Review dashboard** — built, live-verified against a full fresh pipeline run
11. ✅ **Wrap in GitHub Actions cron** — built, not yet run in real CI (needs repo secrets configured first)

**Remaining work: real CI run of the GitHub Actions workflow once secrets are configured, and a live run of Outreach Draft once real Track B postings get tailored.** No Form-Fill work planned unless revisited later.

---

## 13. Known Gaps / Fallbacks to Revisit Later

- The 10 `low_confidence` Track C companies (client-rendered shells: Oracle, JPMorgan's Oracle Fusion UI, Goldman Sachs, etc.) can't be reliably text-hashed — open question whether a heavier Playwright-based monitor is worth building for them later, or whether this gap is accepted
- Quant firm hiring cycles are often batch/annual rather than rolling — may be better served by a calendar reminder than pure hash-diffing for those specific companies
- Postings with no date at all (missing-date case in Filter's staleness logic) were meant to be flagged `low_confidence`/visible rather than excluded — needs confirming this was actually implemented that way
- `pipeline_runs` table / "only show what's new since last successful run" refinement — deliberately deferred until the core pipeline is fully built
- Form-fill/auto-apply — explicitly out of scope; the pipeline's deliverable ends at a reviewed, tailored, dashboard-ready package
- Track B's real ongoing search breadth/schedule (and therefore its real Apify + Anthropic cost) hasn't been tuned for deployment yet — was validated functionally, not for production volume