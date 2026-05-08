"""Policy 引擎单元测试"""

import pytest

from src.config import settings


@pytest.fixture
def mock_tiers(monkeypatch):
    monkeypatch.setattr(settings, "production_hosts", "192.168.1.10,192.168.1.11")
    monkeypatch.setattr(settings, "staging_hosts", "192.168.1.20")


# ---- host_tiers ----


def test_lookup_tier_production(mock_tiers):
    from src.policy.host_tiers import lookup_tier

    assert lookup_tier("192.168.1.10") == "production"
    assert lookup_tier("192.168.1.11") == "production"


def test_lookup_tier_staging(mock_tiers):
    from src.policy.host_tiers import lookup_tier

    assert lookup_tier("192.168.1.20") == "staging"


def test_lookup_tier_unknown_defaults_to_dev(mock_tiers):
    from src.policy.host_tiers import lookup_tier

    assert lookup_tier("10.0.0.99") == "dev"


def test_lookup_tier_handles_whitespace_in_settings(monkeypatch):
    from src.policy.host_tiers import lookup_tier

    monkeypatch.setattr(settings, "production_hosts", " 1.1.1.1 ,2.2.2.2 ")
    monkeypatch.setattr(settings, "staging_hosts", "")
    assert lookup_tier("1.1.1.1") == "production"
    assert lookup_tier("2.2.2.2") == "production"


def test_lookup_tier_none_or_empty_input():
    from src.policy.host_tiers import lookup_tier

    assert lookup_tier(None) == "dev"
    assert lookup_tier("") == "dev"


def test_lookup_tier_production_takes_priority_over_staging(monkeypatch):
    """同一 IP 在两个列表里时，production 赢"""
    from src.policy.host_tiers import lookup_tier

    monkeypatch.setattr(settings, "production_hosts", "1.1.1.1")
    monkeypatch.setattr(settings, "staging_hosts", "1.1.1.1")
    assert lookup_tier("1.1.1.1") == "production"
