# NYC Job Search Pipeline Agent

See [job_search_agent_architecture.md](job_search_agent_architecture.md) for the full design.

## Repo layout

- `config/companies.yaml` — tier-list companies mapped to ATS type + Track (A/B/C). Populated by the company ATS audit (build order step 1).
- `.claude/agents/` — subagent definitions for each pipeline stage (ats-discovery, linkedin-discovery, company-monitor, fit-scorer, resume-tailor, form-filler).
- `src/db/schema.sql` — SQLite schema (postings, scores, tailored, applications, company_page_hashes).
- `src/discovery/`, `src/filter/`, `src/score/`, `src/tailor/`, `src/formfill/`, `src/dashboard/` — pipeline stage implementations, filled in per the build order.
- `resumes/` — existing per-company LaTeX resume bases (baseline tailoring inputs).
- `data/` — local SQLite DB and generated artifacts (gitignored).

## Status

Build order step 1 (company ATS audit) in progress — see `config/companies.yaml`.
