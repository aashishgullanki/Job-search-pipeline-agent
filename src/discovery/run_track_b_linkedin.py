"""CLI entry point: Track B discovery via the Apify LinkedIn jobs scraper.

Requires an Apify API token (env var APIFY_TOKEN, or a .env file -- see
.env.example). Reuses the exact same store.insert_new_postings dedup logic
as Track A/C; the only new code here is the Apify call and the
normalize_linkedin_job mapping (src/discovery/normalize.py).

Usage:
    python3 -m src.discovery.run_track_b_linkedin [--db PATH] [--location LOC] [--limit N]
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.discovery.linkedin import (
    DEFAULT_LIMIT,
    DEFAULT_LOCATION,
    MIN_RESULTS_THRESHOLD,
    fetch_linkedin_jobs_with_widening,
)
from src.discovery.store import insert_new_postings


def run(
    db_path: Path = DEFAULT_DB_PATH,
    location: str = DEFAULT_LOCATION,
    limit: int = DEFAULT_LIMIT,
    min_results: int = MIN_RESULTS_THRESHOLD,
) -> int:
    load_dotenv()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print(
            "[error] APIFY_TOKEN not set (checked environment and .env). "
            "Get one from https://console.apify.com/settings/integrations and "
            "either export it or add APIFY_TOKEN=... to a .env file in the repo root."
        )
        return 1

    conn = get_connection(db_path)
    init_db(conn)

    try:
        jobs, attempts = fetch_linkedin_jobs_with_widening(
            token, location=location, limit=limit, min_results=min_results
        )
    except RuntimeError as e:
        print(f"[error] {e}")
        return 1

    for a in attempts:
        print(
            f"attempt {a['attempt']}: keywords={a['keywords']!r} "
            f"distance={a['distance']}mi -> {a['results']} results"
        )

    new, skipped = insert_new_postings(conn, jobs)
    print(f"\n{len(jobs)} fetched (best attempt) | {new} new | {skipped} already seen")

    if len(attempts) > 1:
        print(f"Widened search {len(attempts) - 1} time(s) before settling on the best result set.")
    if len(jobs) < min_results:
        print(
            f"[warning] still below the {min_results}-result threshold after "
            f"{len(attempts)} attempt(s) -- LinkedIn results may genuinely be thin "
            f"right now, or the search config may need tuning."
        )

    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--location", default=DEFAULT_LOCATION, help="Search location")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max results per attempt")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db, location=args.location, limit=args.limit))
