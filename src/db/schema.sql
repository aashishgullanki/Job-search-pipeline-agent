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
    checked_at TEXT NOT NULL DEFAULT (datetime('now'))
);
