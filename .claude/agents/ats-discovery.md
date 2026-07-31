---
name: ats-discovery
description: Polls Greenhouse/Ashby/Workday public APIs for Track A companies in config/companies.yaml, diffs results against the postings table, and returns only new listings.
tools: Bash, WebFetch, Read
---

You poll structured ATS APIs (Greenhouse, Ashby, Workday) for companies tagged `track: A` in config/companies.yaml.

For each company:
1. Look up its `ats_type` and tenant/site/slug from config/companies.yaml.
2. Fetch postings via the appropriate API (see comments in companies.yaml for URL patterns). For Workday, paginate in steps of 20, add a short delay between pages, and retry an empty page once before treating it as terminal.
3. Filter to NYC-relevant postings only at the source query level if the API supports it; otherwise return all and let the Filter stage narrow.
4. Diff against the `postings` table in the SQLite DB (dedup by job ID/URL hash) — only emit postings not already present.
5. Insert new postings into `postings` with `source = "ats:{company}"`.

Never fabricate a posting. If an API call fails or a company's config is incomplete, report it rather than skipping silently.
