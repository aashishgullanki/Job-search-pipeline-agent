"""send_email tests -- entirely mocked, no real SendGrid calls. Mirrors
tests/discovery/test_linkedin.py's FakeResponse pattern for the Apify
integration.
"""

import json

import pytest

import src.notify.email as email_mod


class FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=""):
        self._json = json_data or {}
        self.status_code = status_code
        self.text = text or json.dumps(self._json)[:300]

    def json(self):
        return self._json


def test_send_email_posts_correct_payload_shape(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResponse(status_code=202)

    monkeypatch.setattr(email_mod.requests, "post", fake_post)

    email_mod.send_email("api-key-123", "from@example.com", "to@example.com", "Subject line", "<p>body</p>")

    assert captured["url"] == email_mod.SENDGRID_SEND_URL
    assert captured["headers"]["Authorization"] == "Bearer api-key-123"
    assert captured["json"]["personalizations"] == [{"to": [{"email": "to@example.com"}]}]
    assert captured["json"]["from"] == {"email": "from@example.com"}
    assert captured["json"]["subject"] == "Subject line"
    assert captured["json"]["content"] == [{"type": "text/html", "value": "<p>body</p>"}]


def test_send_email_accepts_202_as_success(monkeypatch):
    # SendGrid's real success response for /mail/send is 202 Accepted, not 200.
    monkeypatch.setattr(email_mod.requests, "post", lambda *a, **kw: FakeResponse(status_code=202))

    result = email_mod.send_email("key", "from@example.com", "to@example.com", "Subject", "<p>x</p>")

    assert result.status_code == 202


def test_send_email_raises_on_non_2xx(monkeypatch):
    monkeypatch.setattr(
        email_mod.requests,
        "post",
        lambda *a, **kw: FakeResponse(status_code=401, text="Unauthorized"),
    )

    with pytest.raises(RuntimeError, match="401"):
        email_mod.send_email("bad-key", "from@example.com", "to@example.com", "Subject", "<p>x</p>")


def test_send_email_raises_on_network_error(monkeypatch):
    import requests

    def raise_conn_error(*args, **kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(email_mod.requests, "post", raise_conn_error)

    with pytest.raises(RuntimeError, match="boom"):
        email_mod.send_email("key", "from@example.com", "to@example.com", "Subject", "<p>x</p>")
