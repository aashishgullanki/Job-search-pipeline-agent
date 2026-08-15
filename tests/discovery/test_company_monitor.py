import sqlite3
from pathlib import Path

import pytest

import src.discovery.company_monitor as monitor
from src.db.connection import init_db

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


# --- normalize_html_to_text ---


def test_normalize_strips_scripts_and_produces_stable_hash_across_deploys():
    """Same visible content, different script bundle/build-id -> identical hash.

    This is the core noise-rejection property: page redeploys (new bundle
    hash, new buildId) must not look like a content change.
    """
    text1, _ = monitor.normalize_html_to_text(_read("monitor_page_v1.html"))
    text2, _ = monitor.normalize_html_to_text(_read("monitor_page_v2_same_visible_diff_script.html"))

    assert monitor.hash_content(text1) == monitor.hash_content(text2)


def test_normalize_detects_a_real_content_change():
    text1, _ = monitor.normalize_html_to_text(_read("monitor_page_v1.html"))
    text3, _ = monitor.normalize_html_to_text(_read("monitor_page_v3_new_job.html"))

    assert monitor.hash_content(text1) != monitor.hash_content(text3)
    assert "Software Engineer" in text3


def test_normalize_flags_spa_shell_as_low_confidence():
    _, low_confidence = monitor.normalize_html_to_text(_read("monitor_page_spa_shell.html"))
    assert low_confidence is True


def test_normalize_scrubs_per_request_hex_tokens():
    # Found live on Phenom-People-backed sites (Snowflake, eBay): a fresh
    # 32-char hex token rendered inline in visible text on every single
    # fetch, with no real content change. Must not affect the hash.
    html_a = "<body>All Rights Reserved 38d8cf5942b547219be4b3573c0f5a64</body>"
    html_b = "<body>All Rights Reserved c3c4a76bf9234f63a3590863e8fce732</body>"

    text_a, _ = monitor.normalize_html_to_text(html_a)
    text_b, _ = monitor.normalize_html_to_text(html_b)

    assert monitor.hash_content(text_a) == monitor.hash_content(text_b)


def test_normalize_does_not_flag_content_rich_page_as_low_confidence():
    # v1's fixture body is short by construction; lower the threshold to
    # match so this test exercises "has real content relative to its own
    # scale" rather than requiring a 200-word fixture just to pass.
    _, low_confidence = monitor.normalize_html_to_text(
        _read("monitor_page_v1.html"), low_content_word_threshold=10
    )
    assert low_confidence is False


# --- diff_added_lines / find_job_signal ---


def test_diff_added_lines_finds_the_new_job():
    old_text, _ = monitor.normalize_html_to_text(_read("monitor_page_v1.html"))
    new_text, _ = monitor.normalize_html_to_text(_read("monitor_page_v3_new_job.html"))

    added = monitor.diff_added_lines(old_text, new_text)

    assert any("Software Engineer" in line for line in added)


def test_find_job_signal_positive_with_nyc_keyword():
    signal = monitor.find_job_signal(["Software Engineer, New York, NY"])
    assert signal is not None
    assert "software engineer" in signal["title_keywords"]
    assert signal["confidence"] == "high"


def test_find_job_signal_positive_without_nyc_keyword_is_medium_confidence():
    signal = monitor.find_job_signal(["Software Engineer, Austin, TX"])
    assert signal is not None
    assert signal["confidence"] == "medium"


def test_find_job_signal_negative_for_unrelated_diff():
    old_text, _ = monitor.normalize_html_to_text(_read("monitor_page_v1.html"))
    new_text, _ = monitor.normalize_html_to_text(_read("monitor_page_v4_non_job_change.html"))
    added = monitor.diff_added_lines(old_text, new_text)

    assert monitor.find_job_signal(added) is None


# --- check_company orchestration (mocked fetch) ---


def _patch_fetch(monkeypatch, html_by_call):
    """html_by_call: list of HTML strings (or Exception instances) returned
    in order across successive calls to fetch_page."""
    calls = iter(html_by_call)

    def fake_fetch(url, timeout=20):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(monitor, "fetch_page", fake_fetch)


def test_first_run_seeds_baseline_without_alerting(conn, monkeypatch):
    _patch_fetch(monkeypatch, [_read("monitor_page_v1.html")])

    result = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")

    assert result["status"] == "baseline_seeded"
    assert conn.execute("SELECT COUNT(*) FROM company_monitor_alerts").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM company_page_hashes WHERE company='ExampleCo'").fetchone()
    assert row["page_hash"]
    assert row["last_content"]


def test_unchanged_page_produces_no_alert(conn, monkeypatch):
    _patch_fetch(monkeypatch, [_read("monitor_page_v1.html"), _read("monitor_page_v1.html")])

    monitor.check_company(conn, "ExampleCo", "https://example.com/careers")
    result = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")

    assert result["status"] == "unchanged"
    assert conn.execute("SELECT COUNT(*) FROM company_monitor_alerts").fetchone()[0] == 0


def test_job_shaped_change_triggers_alert(conn, monkeypatch):
    _patch_fetch(monkeypatch, [_read("monitor_page_v1.html"), _read("monitor_page_v3_new_job.html")])

    monitor.check_company(conn, "ExampleCo", "https://example.com/careers")
    result = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")

    assert result["status"] == "alert"
    assert result["signal"]["confidence"] == "high"
    alerts = conn.execute("SELECT * FROM company_monitor_alerts").fetchall()
    assert len(alerts) == 1
    assert "Software Engineer" in alerts[0]["diff_snippet"]


def test_non_job_change_updates_state_but_does_not_alert(conn, monkeypatch):
    _patch_fetch(
        monkeypatch, [_read("monitor_page_v1.html"), _read("monitor_page_v4_non_job_change.html")]
    )

    monitor.check_company(conn, "ExampleCo", "https://example.com/careers")
    result = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")

    assert result["status"] == "changed_no_signal"
    assert conn.execute("SELECT COUNT(*) FROM company_monitor_alerts").fetchone()[0] == 0
    # state was still updated to the new content, so the next diff is against v4, not v1
    row = conn.execute("SELECT last_content FROM company_page_hashes WHERE company='ExampleCo'").fetchone()
    assert "worldwide" in row["last_content"]


def test_repeat_identical_alert_is_deduped(conn, monkeypatch):
    # v1 -> v3 (alert) -> v1 -> v3 again: same diff signal recurring should not double-alert
    _patch_fetch(
        monkeypatch,
        [
            _read("monitor_page_v1.html"),
            _read("monitor_page_v3_new_job.html"),
            _read("monitor_page_v1.html"),
            _read("monitor_page_v3_new_job.html"),
        ],
    )

    monitor.check_company(conn, "ExampleCo", "https://example.com/careers")  # baseline
    r2 = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")  # alert
    monitor.check_company(conn, "ExampleCo", "https://example.com/careers")  # reverts, no signal either way is fine
    r4 = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")  # same diff signal again

    assert r2["status"] == "alert"
    assert r4["status"] == "changed_signal_already_alerted"
    assert conn.execute("SELECT COUNT(*) FROM company_monitor_alerts").fetchone()[0] == 1


def test_fetch_failure_is_tracked_and_does_not_raise(conn, monkeypatch):
    _patch_fetch(monkeypatch, [RuntimeError("fetch failed for https://x: HTTP 403")])

    result = monitor.check_company(conn, "BlockedCo", "https://example.com/careers")

    assert result["status"] == "fetch_failed"
    assert result["consecutive_fetch_failures"] == 1
    row = conn.execute("SELECT * FROM company_page_hashes WHERE company='BlockedCo'").fetchone()
    assert row["consecutive_fetch_failures"] == 1
    assert row["last_content"] is None


def test_consecutive_fetch_failures_increment(conn, monkeypatch):
    _patch_fetch(
        monkeypatch,
        [RuntimeError("HTTP 403"), RuntimeError("HTTP 403"), RuntimeError("HTTP 403")],
    )

    for _ in range(3):
        result = monitor.check_company(conn, "BlockedCo", "https://example.com/careers")

    assert result["consecutive_fetch_failures"] == 3


def test_fetch_failure_does_not_reset_a_prior_good_baseline(conn, monkeypatch):
    _patch_fetch(monkeypatch, [_read("monitor_page_v1.html"), RuntimeError("HTTP 500")])

    monitor.check_company(conn, "ExampleCo", "https://example.com/careers")  # good baseline
    result = monitor.check_company(conn, "ExampleCo", "https://example.com/careers")  # transient failure

    assert result["status"] == "fetch_failed"
    row = conn.execute("SELECT * FROM company_page_hashes WHERE company='ExampleCo'").fetchone()
    assert row["last_content"] is not None  # baseline content preserved, not wiped by the failure
    assert row["consecutive_fetch_failures"] == 1
