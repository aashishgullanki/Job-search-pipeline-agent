"""CLI entry point: Track C hash-diff monitoring for the 32 custom/
workday_protected companies (see src/discovery/company_monitor.py for the
full design -- hashing granularity, the diff/keyword-gate check on change,
and how false positives are kept off the main review surface).

Usage:
    python3 -m src.discovery.run_track_c_monitor [--db PATH]
"""

import argparse
import sys
import time
from pathlib import Path

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.discovery.company_monitor import check_company
from src.discovery.config import track_c_companies

INTER_COMPANY_DELAY_SECONDS = 0.5


def run(db_path: Path = DEFAULT_DB_PATH) -> int:
    conn = get_connection(db_path)
    init_db(conn)

    companies = track_c_companies()
    counts: dict[str, int] = {}
    failures: list[tuple[str, str]] = []
    alerts: list[str] = []
    low_confidence: list[str] = []

    for i, c in enumerate(companies):
        name = c["name"]
        result = check_company(conn, name, c["careers_url"])
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1

        if result.get("low_confidence"):
            low_confidence.append(name)

        if status == "fetch_failed":
            failures.append((name, result["error"]))
            print(
                f"[fetch failed] {name}: {result['error']} "
                f"(consecutive failures: {result['consecutive_fetch_failures']})"
            )
        elif status == "alert":
            alerts.append(name)
            sig = result["signal"]
            print(
                f"[ALERT] {name}: possible new posting "
                f"(keywords: {sig['title_keywords']}, confidence: {sig['confidence']})"
            )
        else:
            print(f"{name}: {status}")

        if i < len(companies) - 1:
            time.sleep(INTER_COMPANY_DELAY_SECONDS)

    print(f"\n{len(companies)} companies checked | " + " | ".join(f"{k}={v}" for k, v in counts.items()))
    if alerts:
        print(f"New alerts written to company_monitor_alerts: {alerts}")
    if low_confidence:
        print(
            f"Low-confidence (client-rendered shell, hash-diff is a weak signal here): {low_confidence}"
        )
    if failures:
        print("Fetch failures:")
        for name, err in failures:
            print(f"  - {name}: {err}")

    conn.close()

    # A handful of companies being bot-blocked (403/400 from an anti-bot
    # layer, not this code) is expected steady-state, not a run worth
    # failing over -- confirmed live: 29/32 succeeded with only 3
    # consistently-blocked failures (Tesla, Citadel Securities, Meta).
    # Only hard-fail on something systemic: every company failed (real
    # outage or a genuine code/config bug, not anti-bot noise) or a
    # majority did (more failures than successes means something's
    # actually broken, not just a few sites blocking scrapers).
    if len(companies) == 0:
        print("[error] no Track C companies loaded from config -- treating as a config error, not a clean run")
        return 1
    if len(failures) == len(companies):
        print("[error] every Track C company failed to fetch -- likely a systemic issue, not anti-bot noise")
        return 1
    if len(failures) * 2 > len(companies):
        print(f"[error] {len(failures)}/{len(companies)} companies failed -- majority failed, not a clean run")
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db))
