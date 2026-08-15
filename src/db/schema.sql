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
    resume_pdf_path TEXT,
    outreach_draft TEXT,
    tailored_at TEXT NOT NULL DEFAULT (datetime('now')),
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
