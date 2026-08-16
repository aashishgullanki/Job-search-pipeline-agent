"""CLI entry point: the accept mechanism. Updates a posting's
applications.status by hand -- this is what Form-Fill will eventually read
from to know what's cleared to act on (architecture doc section 7). Just
flips a field; regenerate the dashboard digest afterward to see it
reflected there.

Usage:
    python3 -m src.dashboard.set_application_status <posting_id> <accepted|rejected|pending_review|submitted>
"""

import argparse
import sys
from pathlib import Path

from src.dashboard.store import VALID_APPLICATION_STATUSES, set_application_status
from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db


def run(posting_id: int, status: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    updated = set_application_status(conn, posting_id, status)
    if not updated:
        print(
            f"[error] no applications row for posting_id={posting_id} -- "
            "run the dashboard digest first (python3 -m src.dashboard.run_digest)"
        )
        conn.close()
        return 1

    print(f"posting_id={posting_id} -> {status}")
    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("posting_id", type=int)
    parser.add_argument("status", choices=sorted(VALID_APPLICATION_STATUSES))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    args = parser.parse_args()
    sys.exit(run(args.posting_id, args.status, db_path=args.db))
