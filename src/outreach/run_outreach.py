"""CLI entry point: the Outreach Draft stage (architecture doc section 6).
Track B (LinkedIn-sourced) postings only, and only ones that already have
a successfully tailored resume. Two paid calls per posting processed: one
Apify contact search (widened once on a zero-result search), and -- only
if a contact was actually found -- one Haiku draft call. Requires
APIFY_TOKEN and ANTHROPIC_API_KEY. Re-running is a no-op for postings
already processed (drafted or confirmed no-contact-found) -- see
src/outreach/store.py.

Usage:
    python3 -m src.outreach.run_outreach [--db PATH] [--limit N]
"""

import argparse
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from src.db.connection import DEFAULT_DB_PATH, get_connection, init_db
from src.outreach.contacts import find_contacts_for_company
from src.outreach.draft import draft_outreach_message
from src.outreach.store import (
    get_track_b_postings_needing_outreach,
    record_no_contact_found,
    record_outreach_drafted,
)
from src.score.profile import build_profile_summary


def run(db_path: Path = DEFAULT_DB_PATH, limit: int | None = None) -> int:
    load_dotenv()
    apify_token = os.environ.get("APIFY_TOKEN")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not apify_token:
        print("[error] APIFY_TOKEN not set (checked environment and .env). Add it to run contact discovery.")
        return 1
    if not anthropic_key:
        print("[error] ANTHROPIC_API_KEY not set (checked environment and .env). Add it to draft messages.")
        return 1

    client = anthropic.Anthropic(api_key=anthropic_key)
    profile = build_profile_summary()

    conn = get_connection(db_path)
    init_db(conn)

    to_process = get_track_b_postings_needing_outreach(conn, limit=limit)
    if limit is not None:
        print(f"[limit={limit}] processing at most {len(to_process)} posting(s) this run")

    drafted = 0
    no_contact = 0
    errors: list[tuple[str, str]] = []

    for row in to_process:
        label = f"{row['company']} — {row['title']}"
        try:
            contacts, attempts_log = find_contacts_for_company(apify_token, row["company"])
        except RuntimeError as e:
            errors.append((label, str(e)))
            print(f"[error] contact search failed for {label}: {e}")
            continue

        if not contacts:
            record_no_contact_found(conn, row["id"], attempts_log)
            no_contact += 1
            tries = len(attempts_log)
            print(f"[no contact] {label} (tried {tries} quer{'y' if tries == 1 else 'ies'}, generic draft kept)")
            continue

        top_contact = contacts[0]
        try:
            message = draft_outreach_message(client, profile, row["company"], row["title"], row["url"], top_contact)
        except (RuntimeError, ValueError) as e:
            errors.append((label, str(e)))
            print(f"[error] draft failed for {label}: {e}")
            continue

        record_outreach_drafted(conn, row["id"], message, top_contact, contacts)
        drafted += 1
        print(f"[drafted] {label} -> {top_contact['name']} ({top_contact['profile_url']})")

    print(f"\n{len(to_process)} newly processed | {drafted} drafted | {no_contact} no contact found | {len(errors)} errors")
    if errors:
        print("\nFailures:")
        for label, err in errors:
            print(f"  - {label}: {err}")

    conn.close()
    return 1 if errors else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--limit", type=int, default=None, help="Cap how many postings to process this run")
    args = parser.parse_args()
    sys.exit(run(db_path=args.db, limit=args.limit))
