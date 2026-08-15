"""CLI entry point: the Filter stage (architecture doc section 3).

Cheap, deterministic rules only -- no LLM calls. Runs against every
posting in `postings` (Track A + B output) that hasn't been filtered yet,
records a pass/fail + reason in `filter_results`, and reports a
rule-by-rule breakdown. Re-running is a no-op for postings already
filtered -- see src/filter/store.py.

Usage:
    python3 -m src.filter.run_filter [--db PATH]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.filter.rules import evaluate_posting
from src.filter.store import get_unfiltered_postings, record_filter_result

RULE_LABELS = {
    "location": "not NYC-located",
    "employment_type": "not full-time",
    "title_keyword": "title doesn't match SWE/AI Engineer keywords",
    "title_seniority": "title indicates Senior/Staff/Principal/Manager level",
}


def run(db_path: Path = DEFAULT_DB_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    to_evaluate = get_unfiltered_postings(conn)
    already_filtered = conn.execute("SELECT COUNT(*) FROM filter_results").fetchone()[0]

    counts: Counter[str] = Counter()
    for row in to_evaluate:
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        result = evaluate_posting(row["title"], row["location"], raw)
        record_filter_result(conn, row["id"], result["passed"], result["excluded_by"])
        counts["passed" if result["passed"] else result["excluded_by"]] += 1

    total_now = already_filtered + len(to_evaluate)
    passed_total = conn.execute("SELECT COUNT(*) FROM filter_results WHERE passed = 1").fetchone()[0]

    print(f"{len(to_evaluate)} newly evaluated | {already_filtered} already filtered (skipped)")
    print(f"\nBreakdown of this run's {len(to_evaluate)} newly evaluated postings:")
    print(f"  passed: {counts.get('passed', 0)}")
    for rule, label in RULE_LABELS.items():
        print(f"  excluded ({rule} -- {label}): {counts.get(rule, 0)}")

    print(f"\nAll-time: {passed_total}/{total_now} postings in filter_results have passed.")

    conn.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db))
