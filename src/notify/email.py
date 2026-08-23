"""Sends the rendered digest via SendGrid's REST API directly (architecture
doc section 7's "digest delivered via email" future step, now built).
Calls the HTTP API with `requests`, same as the Apify integration --
no SendGrid SDK dependency for what's a single POST call.
"""

import requests

SENDGRID_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


def send_email(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    timeout: int = 30,
) -> requests.Response:
    """Raises RuntimeError on any failure (network error or non-2xx) so
    the caller treats a failed send as a real, visible problem rather
    than silently doing nothing -- you wouldn't otherwise know the digest
    never arrived.
    """
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email},
        "subject": subject,
        "content": [{"type": "text/html", "value": html_body}],
    }
    try:
        resp = requests.post(
            SENDGRID_SEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"SendGrid send failed: {e}") from e

    # SendGrid's /mail/send returns 202 Accepted on success, not 200 --
    # same "don't hardcode exactly 200" lesson already learned from Apify
    # returning 201 for a run that actually executed.
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"SendGrid send failed: HTTP {resp.status_code} - {resp.text[:300]}")

    return resp
