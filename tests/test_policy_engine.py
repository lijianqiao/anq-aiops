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


# ---- _get_value ----


def test_get_value_simple_key():
    from src.policy.engine import _get_value

    assert _get_value({"a": 1}, "a") == 1


def test_get_value_dotted_path():
    from src.policy.engine import _get_value

    ctx = {"params": {"path": "/tmp", "min_age_days": 7}}
    assert _get_value(ctx, "params.path") == "/tmp"
    assert _get_value(ctx, "params.min_age_days") == 7


def test_get_value_missing_returns_none():
    from src.policy.engine import _get_value

    assert _get_value({"a": 1}, "b") is None
    assert _get_value({"a": 1}, "a.b.c") is None


def test_get_value_intermediate_not_dict_returns_none():
    """中间路径不是 dict 时不应崩"""
    from src.policy.engine import _get_value

    assert _get_value({"a": "string"}, "a.b") is None


# ---- _match_condition ----


def test_match_condition_equality_default():
    """`field: value` 默认是 eq"""
    from src.policy.engine import _match_condition

    assert _match_condition({"runbook_id": "disk_cleanup"}, {"runbook_id": "disk_cleanup"}) is True
    assert _match_condition({"runbook_id": "disk_cleanup"}, {"runbook_id": "service_restart"}) is False


def test_match_condition_in_operator():
    from src.policy.engine import _match_condition

    ctx = {"params": {"path": "/tmp"}}
    assert _match_condition({"params.path": {"in": ["/tmp", "/var/log"]}}, ctx) is True
    assert _match_condition({"params.path": {"in": ["/etc", "/usr"]}}, ctx) is False


def test_match_condition_not_in():
    from src.policy.engine import _match_condition

    ctx = {"runbook_id": "disk_cleanup"}
    assert _match_condition({"runbook_id": {"not_in": ["service_restart", "exotic"]}}, ctx) is True
    assert _match_condition({"runbook_id": {"not_in": ["disk_cleanup"]}}, ctx) is False


def test_match_condition_gte_lte():
    from src.policy.engine import _match_condition

    ctx = {"confidence": 0.9}
    assert _match_condition({"confidence": {"gte": 0.85}}, ctx) is True
    assert _match_condition({"confidence": {"gte": 0.95}}, ctx) is False
    assert _match_condition({"confidence": {"lte": 0.95}}, ctx) is True
    assert _match_condition({"confidence": {"lte": 0.5}}, ctx) is False


def test_match_condition_gt_lt():
    from src.policy.engine import _match_condition

    ctx = {"confidence": 0.9}
    assert _match_condition({"confidence": {"gt": 0.85}}, ctx) is True
    assert _match_condition({"confidence": {"gt": 0.9}}, ctx) is False  # 严格大于
    assert _match_condition({"confidence": {"lt": 1.0}}, ctx) is True


def test_match_condition_ne():
    from src.policy.engine import _match_condition

    ctx = {"risk_level": "low"}
    assert _match_condition({"risk_level": {"ne": "high"}}, ctx) is True
    assert _match_condition({"risk_level": {"ne": "low"}}, ctx) is False


def test_match_condition_unknown_operator_raises():
    """避免 yaml 写错 operator 静默放过"""
    from src.policy.engine import _match_condition

    with pytest.raises(ValueError, match="unknown operator"):
        _match_condition({"x": {"weird_op": 1}}, {"x": 1})


def test_match_condition_missing_field_is_false():
    """字段不存在 → 条件不匹配（不抛错，避免规则因可选字段崩）"""
    from src.policy.engine import _match_condition

    assert _match_condition({"params.foo": "bar"}, {"params": {}}) is False
    assert _match_condition({"params.path": {"in": ["/tmp"]}}, {}) is False


def test_match_condition_missing_field_with_numeric_op_is_false():
    """缺失字段对 gte/lte 等数值 operator 也应 False，不抛 TypeError"""
    from src.policy.engine import _match_condition

    assert _match_condition({"confidence": {"gte": 0.85}}, {}) is False


def test_match_condition_multiple_keys_all_must_match():
    """单个 condition dict 里多个 key 是 AND"""
    from src.policy.engine import _match_condition

    ctx = {"runbook_id": "disk_cleanup", "risk_level": "low"}
    assert _match_condition({"runbook_id": "disk_cleanup", "risk_level": "low"}, ctx) is True
    assert _match_condition({"runbook_id": "disk_cleanup", "risk_level": "high"}, ctx) is False
