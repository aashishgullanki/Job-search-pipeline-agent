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
```

Both runners are idempotent — re-running against the same DB only inserts postings not already seen (deduped by URL hash). The Workday runner is slower by design: it paginates in steps of 20 with a delay between pages and retries an empty page before treating it as terminal, per the architecture doc.

## Status

- Step 1 (company ATS audit) — done, see `config/companies.yaml` (88 companies, 56 Track A / 32 Track C).
- Step 2 (Track A discovery) — done, all three sources:
  - Greenhouse + Ashby: `src/discovery/{greenhouse,ashby}.py` + `run_track_a_simple.py`, 39 companies.
  - Workday: `src/discovery/workday.py` + `run_track_a_workday.py`, 17 companies, paginated POST with empty-page retry. Two undocumented Workday quirks drove the pagination logic (see the module docstring): the `total` field goes unreliable near the end of a board, and an out-of-range offset silently re-serves page 1 instead of coming back empty -- so termination is based on partial-page-size, not on `total` or on emptiness alone.
  - Shared `normalize.py` / `store.py` / `config.py` across all three sources -- no per-source dedup logic duplicated.
  - Tested live end-to-end for both runners (fresh-DB pass + idempotency-check pass), not just unit tests against fixtures.
- Track C (company-monitor hash-diffing), Track B (LinkedIn), Filter/Score/Tailor/Form-fill stages — not started yet.
