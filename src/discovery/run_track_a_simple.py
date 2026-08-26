"""CLI entry point: Track A discovery for Greenhouse + Ashby companies.

Workday is deliberately excluded here -- it needs paginated POST requests,
inter-page delays, and empty-page retry logic (see the architecture doc),
which lives in its own runner. This one only covers the simple GET-based
APIs (Greenhouse boards-api, Ashby posting-api).

Usage:
    python3 -m src.discovery.run_track_a_simple [--db PATH]
"""

import argparse
import sys
from pathlib import Path

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.discovery.ashby import fetch_ashby_jobs
from src.discovery.config import track_a_simple_companies
from src.discovery.greenhouse import fetch_greenhouse_jobs
from src.discovery.store import insert_new_postings


def run(db_path: Path = DEFAULT_DB_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    companies = track_a_simple_companies()
    total_new = 0
    total_skipped = 0
    errors: list[tuple[str, str]] = []

    for c in companies:
        name = c["name"]
        try:
            if c["ats_type"] == "greenhouse":
                jobs = fetch_greenhouse_jobs(c["slug"], name)
            else:
                jobs = fetch_ashby_jobs(c["slug"], name)
        except RuntimeError as e:
            errors.append((name, str(e)))
            print(f"[error] {name}: {e}")
            continue

        new, skipped = insert_new_postings(conn, jobs)
        total_new += new
        total_skipped += skipped
        print(f"{name}: {len(jobs)} fetched, {new} new, {skipped} already seen")

    print(
        f"\n{len(companies)} companies polled | "
        f"{total_new} new postings | {total_skipped} already seen | "
        f"{len(errors)} errors"
    )
    if errors:
        print("Failures:")
        for name, err in errors:
            print(f"  - {name}: {err}")

    conn.close()

    # Same reasoning as run_track_a_workday.py and Track C's monitor: one
    # company hitting a fetch problem shouldn't fail the whole run. Only
    # hard-fail on something systemic (a majority failed, all failed, or
    # no companies loaded -- a real config error).
    if len(companies) == 0:
        print("[error] no Track A Greenhouse/Ashby companies loaded from config -- treating as a config error, not a clean run")
        return 1
    if len(errors) == len(companies):
        print("[error] every Track A Greenhouse/Ashby company failed -- likely a systemic issue, not an isolated fetch problem")
        return 1
    if len(errors) * 2 > len(companies):
        print(f"[error] {len(errors)}/{len(companies)} companies failed -- majority failed, not a clean run")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db))
