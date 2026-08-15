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
python3 -m src.discovery.run_track_a_simple   # live poll: Greenhouse + Ashby companies (39)
```

The runner is idempotent — re-running it against the same DB only inserts postings not already seen (deduped by URL hash).

## Status

- Step 1 (company ATS audit) — done, see `config/companies.yaml` (88 companies, 56 Track A / 32 Track C).
- Step 2 (Track A discovery), Greenhouse + Ashby half — done: `src/discovery/{greenhouse,ashby,normalize,store,config}.py` + `run_track_a_simple.py`, covers 39 companies, tested live end-to-end including dedup idempotency.
- Workday polling (pagination/retry) — not started yet.
