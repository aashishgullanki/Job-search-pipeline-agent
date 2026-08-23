from pathlib import Path

import pytest

from src.common.features import is_outreach_enabled, load_features


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "features.yaml"
    path.write_text(content)
    return path


def test_missing_file_defaults_to_empty_dict(tmp_path):
    result = load_features(tmp_path / "does_not_exist.yaml")
    assert result == {}


def test_missing_file_means_outreach_disabled(tmp_path):
    assert is_outreach_enabled(tmp_path / "does_not_exist.yaml") is False


def test_empty_file_means_outreach_disabled(tmp_path):
    path = _write_config(tmp_path, "")
    assert is_outreach_enabled(path) is False


def test_missing_key_defaults_to_disabled(tmp_path):
    path = _write_config(tmp_path, "some_other_flag: true\n")
    assert is_outreach_enabled(path) is False


def test_explicit_false(tmp_path):
    path = _write_config(tmp_path, "outreach_enabled: false\n")
    assert is_outreach_enabled(path) is False


def test_explicit_true(tmp_path):
    path = _write_config(tmp_path, "outreach_enabled: true\n")
    assert is_outreach_enabled(path) is True


def test_real_repo_config_defaults_to_off():
    # config/features.yaml as actually checked in -- must default off.
    assert is_outreach_enabled() is False
