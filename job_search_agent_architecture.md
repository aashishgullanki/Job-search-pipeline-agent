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

### Track A — Target Company ATS Polling (from tier list)
Direct, structured polling of the ~80 companies from your tier-list, each tagged in config with its ATS type:

| ATS type | Method | Notes |
|---|---|---|
| `greenhouse` | GET `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | Clean public API |
| `ashby` | GET public Ashby board API | Clean public API |
| `workday` | POST `{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` | Paginate in steps of 20, add delay between pages, retry empty pages before treating as terminal |
| `workday_protected` | N/A — flagged, needs browser automation fallback | e.g. Goldman Sachs |
| `custom`/`unknown` | Flagged for manual check or scrape fallback | Big tech, some quant firms |

Company→ATS mapping lives in `config/companies.yaml`, filled in during the audit step (tenant, site, slug per company).

### Track B — Broad NYC Market Scrape
- Apify LinkedIn jobs scraper (already used in Portfolio Agent) — casts a wider net beyond the tier list, catches NYC SWE/AI Engineer roles at companies not on your radar.
- **Agentic feature — adaptive scrape broadening:** if a scrape returns too few results, the agent automatically widens search terms/radius before giving up, rather than just reporting "nothing found."

Both tracks write into the same `postings` table, tagged with `source` (`ats:{company}` or `linkedin`).

### Track C — Direct Company Monitoring (fallback)
Guarantees every tier-list company gets *some* form of check, even ones not cleanly covered by Track A or reliably surfaced by Track B (e.g. `workday_protected` and `custom` companies like Goldman Sachs, big tech, some quant firms):
- Periodically snapshot each such company's careers/jobs page HTML and hash it
- On a hash change, run a targeted check — either a scrape of the diffed section or a Claude web-search call scoped to `"{company}" software engineer New York` — to see if a relevant posting appeared
- Slower and noisier than Track A's clean API polling, but closes the silent-gap risk

### Coverage Matrix (required, not optional)
A table in `config/companies.yaml` mapping **every single company** from the tier list to exactly one track (A, B, or C) plus its config values (ATS type/tenant/site for Track A, or "monitor via Track C" flag). The build includes a check that fails loudly if any company has no track assigned — this is what actually prevents a company silently falling through the cracks, not the discovery mechanism itself.

---

## 3. Filter Stage

Cheap, deterministic, no LLM call:
- Location contains "New York, NY", excludes remote-only unless desired
- Employment type = full-time
- Title matches SWE/AI Engineer keyword set
- Dedup against `postings` table by job ID/URL hash — never re-process a seen posting

---

## 4. Score Stage (LLM-as-judge)

- Model: **Claude Haiku 4.5** (cheap, fast, good enough for a fit judgment)
- Input: job description + your profile summary (background, stack, target roles)
- Output: fit score (1–10) + 2–3 sentence reasoning
- **Agentic feature — LLM contextual filtering:** replaces brittle keyword exclusion (e.g. filtering out federal/public-sector roles) with judgment calls, since keyword rules miss edge cases
- Only postings above a score threshold move to the tailor stage

---

## 5. Tailor Stage (top matches only)

- Model: **Claude Sonnet** (higher quality for writing)
- Reuses your existing `resume-tailor` skill prompt verbatim
- Compiles LaTeX → PDF via `pdflatex`, enforces one-page output
- **Compile-loop:** up to 3 retry attempts to fix overflow/errors before flagging for manual review
- Also drafts an outreach message to a recruiter/HM contact if one was found (Track B only, via Apify LinkedIn post-search actors, following your existing manual search recipe — query pattern `"hiring" AND "software engineering" AND "new york"`, filtered by company, up to 3 contacts per match)
- **Agentic feature — adaptive contact search retries:** widens/retries the search query if the first pass returns no contacts

---

## 6. Form-Fill Stage

- Browser automation (e.g. Playwright) opens the application form and pre-fills fields from your profile + the tailored resume
- **Hard stop before submit** — this is a non-negotiable guardrail in both the subagent's tool permissions and the code itself. The agent prepares; it never clicks submit.
- Screenshot/state saved for the review dashboard

---

## 7. Review Dashboard

- Simple local dashboard (could start as a markdown/HTML digest, later a small web UI) showing: company, role, fit score + reasoning, tailored resume PDF, draft outreach message, pre-filled form state
- You review and click "Accept" → opens the browser session for final manual submit, or (later, once trusted) triggers the actual submit action
- Daily/periodic digest delivered via email (SendGrid, same as Portfolio Agent) or Slack

---

## 8. Storage Schema (SQLite)

- `postings` — id, source, company, title, url, location, posted_at, first_seen_at, raw_json
- `scores` — posting_id, score, reasoning, scored_at
- `tailored` — posting_id, resume_pdf_path, outreach_draft, tailored_at
- `applications` — posting_id, status (`pending_review` / `accepted` / `submitted` / `rejected`), reviewed_at

---

## 9. Orchestration & Infra

- **Scheduling:** GitHub Actions cron (daily), matching your existing Portfolio Agent pattern — stateless, no persistent server needed
- **Language:** Python throughout (requests, sqlite3, existing Apify client patterns)
- **Anthropic auth:** `CLAUDE_CODE_OAUTH_TOKEN` (generated locally via `claude setup-token`, tied to your Pro/Max subscription), not a raw `ANTHROPIC_API_KEY` — officially supported by `anthropics/claude-code-action`, draws from subscription rate limits instead of metered billing
  - Token can expire/invalidate if you log out of Claude Code locally — build in a periodic reminder to regenerate and refresh the GitHub secret
  - Shares your subscription's rate-limit pool with interactive use — watch for contention on heavy pipeline days
- **Secrets:** GitHub Actions secrets for `CLAUDE_CODE_OAUTH_TOKEN`, Apify token, SendGrid key

---

## 10. Claude Code Subagent Structure

| Subagent | Role | Tools |
|---|---|---|
| `ats-discovery` | Polls Greenhouse/Ashby/Workday, diffs vs DB, returns only new postings | Bash, WebFetch, Read |
| `linkedin-discovery` | Runs Apify actor, handles adaptive broadening | Bash, Read |
| `company-monitor` | Hashes/diffs career pages for Track C companies, triggers targeted check on change | Bash, WebFetch, Read |
| `fit-scorer` | Scores filtered postings against profile | Read (no Bash needed) |
| `resume-tailor` | Edits LaTeX copy, compiles PDF, retry loop | Read, Write, Edit, Bash |
| `form-filler` | Playwright pre-fill, screenshot, stop before submit | Read, Bash (scoped, no submit action ever coded in) |

Each subagent scoped narrowly per the "bounded work" principle — no generalist agent trying to do everything.

---

## 11. Build Order (MVP → full pipeline)

1. **Company ATS audit** — determine `ats_type`/tenant/site per tier-list company, populate `config/companies.yaml`, assign every company to Track A, B, or C (coverage matrix check must pass)
2. **Discovery (Track A)** — Greenhouse/Ashby polling first (simplest), then Workday
3. **Discovery (Track C)** — hash-based monitoring for companies flagged `workday_protected`/`custom`
4. **Discovery (Track B)** — port existing Apify LinkedIn scraper logic
5. **Filter stage** — hard rules + dedup
6. **Score stage** — Haiku fit judge
7. **Tailor stage** — resume-tailor skill + compile loop
8. **Outreach draft** (Track B matches only)
9. **Form-fill stage** — Playwright pre-fill, hard-stop guardrail
10. **Review dashboard + digest delivery**
11. **Wrap in GitHub Actions cron**

---

## 12. Known Gaps / Fallbacks to Revisit Later

- Track C is slower/noisier than API polling — fine for infrequent postings, but quant firm hiring cycles are often batch/annual rather than rolling, so a calendar reminder may complement Track C better than pure hash-diffing for those specific companies
- Form-fill submit action stays manual until the pipeline has a track record you trust
