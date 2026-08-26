"""CLI entry point: Track A discovery for Workday companies.

Separate from run_track_a_simple.py (Greenhouse/Ashby) because Workday
needs paginated POST requests, inter-page delays, and empty-page retry
logic -- see src/discovery/workday.py for the pagination quirks that drove
that design. Both runners share the same normalize/store/dedup logic.

Usage:
    python3 -m src.discovery.run_track_a_workday [--db PATH]
"""

import argparse
import sys
from pathlib import Path

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.discovery.config import track_a_workday_companies
from src.discovery.store import insert_new_postings
from src.discovery.workday import fetch_workday_jobs


def run(db_path: Path = DEFAULT_DB_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    companies = track_a_workday_companies()
    total_new = 0
    total_skipped = 0
    errors: list[tuple[str, str]] = []

    for c in companies:
        name = c["name"]
        try:
            jobs, error = fetch_workday_jobs(
                tenant=c["tenant"],
                site=c["site"],
                company=name,
                careers_url=c["careers_url"],
            )
        except Exception as e:
            # fetch_workday_jobs itself no longer raises for a page-fetch
            # failure (it returns partial jobs + an error message instead,
            # see its docstring) -- this is a safety net for anything
            # genuinely unexpected, not the normal transient-failure path.
            errors.append((name, str(e)))
            print(f"[error] {name}: {e}")
            continue

        new, skipped = insert_new_postings(conn, jobs)
        total_new += new
        total_skipped += skipped
        if error:
            errors.append((name, error))
            print(f"[partial] {name}: {len(jobs)} fetched before failure ({new} new, {skipped} already seen) -- {error}")
        else:
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

    # A single company hitting a transient/unrecoverable fetch problem is
    # expected steady-state, not a run worth failing over -- live-observed
    # twice: Nvidia's Workday board 500ing mid-pagination while 16/17 (94%)
    # of companies succeeded cleanly. Same reasoning as Track C's monitor:
    # only hard-fail on something systemic (a majority failed, all failed,
    # or no companies loaded at all -- a real config error).
    if len(companies) == 0:
        print("[error] no Track A Workday companies loaded from config -- treating as a config error, not a clean run")
        return 1
    if len(errors) == len(companies):
        print("[error] every Track A Workday company failed -- likely a systemic issue, not an isolated fetch problem")
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
