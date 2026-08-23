"""CLI entry point: email the rendered dashboard digest via SendGrid
(architecture doc section 7's "digest delivered via email" future step,
now built). Reads the already-generated data/dashboard.md, renders it to
real HTML -- not a markdown-source dump -- and sends it as the email body
itself, not a link to anywhere. Requires SENDGRID_API_KEY.

Usage:
    python3 -m src.notify.run_notify [--dashboard-path PATH]
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.notify.email import send_email
from src.notify.render import render_digest_html

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DASHBOARD_PATH = REPO_ROOT / "data" / "dashboard.md"

# Single Sender Verification in SendGrid was set up against this address
# for both sender and recipient -- sending to yourself, no second inbox
# needed. If a different sender was verified instead, update this.
FROM_EMAIL = "aashishgullanki@gmail.com"
TO_EMAIL = "aashishgullanki@gmail.com"


def run(dashboard_path: Path = DEFAULT_DASHBOARD_PATH) -> int:
    load_dotenv()
    api_key = os.environ.get("SENDGRID_API_KEY")
    if not api_key:
        print("[error] SENDGRID_API_KEY not set (checked environment and .env).")
        return 1

    if not dashboard_path.exists():
        print(
            f"[error] {dashboard_path} doesn't exist -- run the Dashboard stage first "
            "(python3 -m src.dashboard.run_digest)."
        )
        return 1

    markdown_text = dashboard_path.read_text()
    html_body = render_digest_html(markdown_text)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"Job Search Pipeline Digest -- {date_str}"

    try:
        send_email(api_key, FROM_EMAIL, TO_EMAIL, subject, html_body)
    except RuntimeError as e:
        print(f"[error] {e}")
        return 1

    print(f"Sent digest email to {TO_EMAIL} ({len(markdown_text)} chars of source markdown)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-path", type=Path, default=DEFAULT_DASHBOARD_PATH, help="Path to dashboard.md")
    args = parser.parse_args()
    sys.exit(run(dashboard_path=args.dashboard_path))
