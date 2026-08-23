"""Global pipeline feature flags, loaded from config/features.yaml. Kept
separate from src/discovery/config.py's companies.yaml loader -- that
file is a per-company ATS config list, not a natural home for a
pipeline-wide toggle. The point of a real config file (not a code
constant) is that flipping a flag needs no code changes.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FEATURES_CONFIG_PATH = REPO_ROOT / "config" / "features.yaml"


def load_features(config_path: Path = DEFAULT_FEATURES_CONFIG_PATH) -> dict:
    """A missing file or a missing key both default to that feature being
    off -- fails closed (skip the work, no wasted API calls) rather than
    silently enabling something nobody explicitly turned on.
    """
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def is_outreach_enabled(config_path: Path = DEFAULT_FEATURES_CONFIG_PATH) -> bool:
    return bool(load_features(config_path).get("outreach_enabled", False))
