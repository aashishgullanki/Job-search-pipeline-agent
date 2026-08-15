# NYC Job Search Pipeline Agent

See [job_search_agent_architecture.md](job_search_agent_architecture.md) for the full design.

## Repo layout

- `config/companies.yaml` — tier-list companies mapped to ATS type + Track (A/B/C). Populated by the company ATS audit (build order step 1).
- `.claude/agents/` — subagent definitions for each pipeline stage (ats-discovery, linkedin-discovery, company-monitor, fit-scorer, resume-tailor, form-filler).
- `src/db/schema.sql` — SQLite schema (postings, scores, tailored, applications, company_page_hashes).
- `src/discovery/`, `src/filter/`, `src/score/`, `src/tailor/`, `src/formfill/`, `src/dashboard/` — pipeline stage implementations, filled in per the build order.
- `resumes/` — existing per-company LaTeX resume bases (baseline tailoring inputs).
- `data/` — local SQLite DB and generated artifacts (gitignored).

## Running discovery

```
pip install -r requirements-dev.txt   # includes pytest
python3 -m pytest tests/              # unit tests (no network)
python3 -m src.discovery.run_track_a_simple    # live poll: Greenhouse + Ashby companies (39)
python3 -m src.discovery.run_track_a_workday   # live poll: Workday companies (17, paginated)
python3 -m src.discovery.run_track_c_monitor   # live check: hash-diff monitor, 32 custom/protected companies
```

The Track A runners are idempotent — re-running against the same DB only inserts postings not already seen (deduped by URL hash). The Workday runner is slower by design: it paginates in steps of 20 with a delay between pages and retries an empty page before treating it as terminal, per the architecture doc. The Track C runner is a monitor, not a discovery source -- it writes to `company_page_hashes` every run and only writes to `company_monitor_alerts` (a separate table from `postings`) when a hash change looks job-shaped; see `src/discovery/company_monitor.py`'s module docstring for the full design.

## Status

- Step 1 (company ATS audit) — done, see `config/companies.yaml` (88 companies, 56 Track A / 32 Track C).
- Step 2 (Track A discovery) — done, all three sources:
  - Greenhouse + Ashby: `src/discovery/{greenhouse,ashby}.py` + `run_track_a_simple.py`, 39 companies.
  - Workday: `src/discovery/workday.py` + `run_track_a_workday.py`, 17 companies, paginated POST with empty-page retry. Two undocumented Workday quirks drove the pagination logic (see the module docstring): the `total` field goes unreliable near the end of a board, and an out-of-range offset silently re-serves page 1 instead of coming back empty -- so termination is based on partial-page-size, not on `total` or on emptiness alone.
  - Shared `normalize.py` / `store.py` / `config.py` across all three sources -- no per-source dedup logic duplicated.
  - Tested live end-to-end for both runners (fresh-DB pass + idempotency-check pass), not just unit tests against fixtures.
- Track C (company-monitor hash-diffing) — done: `src/discovery/company_monitor.py` + `run_track_c_monitor.py`, 32 companies. Hashes normalized visible text (not raw HTML, not embedded JS state -- tested and rejected, see module docstring for why), diffs on a hash change and keyword-gates the diff before writing a low-confidence "worth a look" alert to its own table, never straight into `postings`. First run per company seeds a baseline only (no alert). Live-tested end-to-end (fresh pass + repeat pass): 29/32 companies fetch successfully, 3 are bot-blocked (Tesla, Citadel Securities, Meta -- tracked via `consecutive_fetch_failures`, not treated as a content change), 10 flagged `low_confidence` (client-rendered shells where hash-diffing is a weak signal), and one live noise source found and fixed mid-build (a per-request hex token on Phenom-People-backed sites that changed on every fetch regardless of content -- now scrubbed).
- Track B (LinkedIn), Filter/Score/Tailor/Form-fill stages — not started yet.
