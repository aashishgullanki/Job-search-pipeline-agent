CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,              -- 'ats:{company}' or 'linkedin'
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    location TEXT,
    posted_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    score INTEGER NOT NULL,
    reasoning TEXT,
    scored_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (posting_id)
);

CREATE TABLE IF NOT EXISTS tailored (
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    status TEXT NOT NULL,              -- 'tailored' (success) | 'failed' (exhausted compile retries)
    resume_pdf_path TEXT,              -- NULL if status = 'failed'
    resume_tex_path TEXT,              -- kept alongside the PDF for review even on success
    outreach_draft TEXT,               -- generic draft from the Tailor stage's own skill for every
                                        -- source; overwritten with a contact-specific message by the
                                        -- Outreach Draft stage for Track B postings (see below)
    tailoring_summary TEXT,            -- newline-joined list of changes made and why (skill's required output)
    attempts INTEGER NOT NULL,         -- how many compile attempts this took (<= MAX_COMPILE_ATTEMPTS)
    failure_reason TEXT,               -- NULL unless status = 'failed'
    tailored_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Outreach Draft stage (architecture doc section 6) -- Track B (LinkedIn) postings only, all
    -- NULL until that stage runs against a given posting, and NULL forever for Track A/C postings.
    outreach_status TEXT,              -- 'drafted' (contact found + outreach_draft overwritten) |
                                        -- 'no_contact_found' (outreach_draft left as the generic one)
    outreach_contact_name TEXT,
    outreach_contact_profile_url TEXT, -- LinkedIn profile of the contact the message was drafted for
    outreach_source_post_url TEXT,     -- the LinkedIn post that surfaced this contact
    outreach_candidate_contacts TEXT,  -- JSON: up to 3 contacts discovery found (only the first is
                                        -- used for the actual draft) if drafted, or the widening
                                        -- attempts_log if no_contact_found -- kept for review either way
    outreach_drafted_at TEXT,
    PRIMARY KEY (posting_id)
);

CREATE TABLE IF NOT EXISTS applications (
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    status TEXT NOT NULL DEFAULT 'pending_review', -- pending_review | accepted | submitted | rejected
    reviewed_at TEXT,
    PRIMARY KEY (posting_id)
);

CREATE TABLE IF NOT EXISTS company_page_hashes (
    company TEXT PRIMARY KEY,          -- Track C companies only
    page_hash TEXT NOT NULL,
    last_content TEXT,                 -- normalized text the hash was computed from; kept so the
                                        -- next run can diff against it, not just detect "changed"
    low_confidence INTEGER NOT NULL DEFAULT 0,   -- 1 if the fetched page looks like a client-rendered
                                                  -- shell with little real content (hash-diffing is a
                                                  -- weak signal here -- see company_monitor.py)
    consecutive_fetch_failures INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS company_monitor_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    careers_url TEXT NOT NULL,
    diff_snippet TEXT NOT NULL,        -- the added-lines excerpt that triggered this alert
    snippet_hash TEXT NOT NULL,        -- dedup key so a chronically-noisy diff doesn't re-alert every run
    status TEXT NOT NULL DEFAULT 'unreviewed', -- unreviewed | dismissed | confirmed
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company, snippet_hash)
);

CREATE TABLE IF NOT EXISTS filter_results (
    posting_id INTEGER NOT NULL REFERENCES postings(id),
    passed INTEGER NOT NULL,           -- 1 = passed every rule, 0 = excluded
    excluded_by TEXT,                  -- rule that excluded it (staleness | location |
                                        -- employment_type | title_keyword | title_seniority),
                                        -- NULL if passed
    low_confidence_age INTEGER NOT NULL DEFAULT 0,  -- 1 if posted_at was missing/unparseable --
                                        -- no evidence either way about age, so NOT excluded
                                        -- outright (unlike Workday's "30+ Days Ago" bucket, which
                                        -- IS a confirmed lower bound and does get excluded), but
                                        -- flagged as unverified-age since a real cutoff couldn't
                                        -- actually be checked against it
    filtered_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (posting_id)
);
