"""Feature-flag gating for the Outreach Draft CLI runner -- when disabled,
this must exit cleanly before ever checking for APIFY_TOKEN/
ANTHROPIC_API_KEY, let alone calling either. No mocking of the actual
contact-search/drafting path needed here since disabled means neither is
ever reached.
"""

import src.outreach.run_outreach as run_mod


def test_disabled_skips_before_checking_env_vars(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "is_outreach_enabled", lambda: False)
    # Deliberately no APIFY_TOKEN/ANTHROPIC_API_KEY in the environment --
    # if the disabled check didn't come first, this would fail on the
    # missing-token branch instead of skipping cleanly.
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exit_code = run_mod.run(db_path=tmp_path / "unused.db")

    assert exit_code == 0


def test_disabled_never_touches_the_db(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "is_outreach_enabled", lambda: False)
    db_path = tmp_path / "should_not_be_created.db"

    run_mod.run(db_path=db_path)

    assert not db_path.exists()
