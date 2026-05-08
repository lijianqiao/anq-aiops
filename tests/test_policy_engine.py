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


# ---- evaluate_policy ----


import textwrap


def _write_policy(tmp_path, yaml_content: str):
    """写一个临时 policy yaml 并返回路径"""
    p = tmp_path / "policies.yaml"
    p.write_text(textwrap.dedent(yaml_content), encoding="utf-8")
    return p


def test_evaluate_policy_default_is_approval_required(tmp_path, monkeypatch):
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, "policies: []")
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "1.1.1.1", "path": "/tmp"},
        alert={"severity": "high"},
        plan={"risk_level": "low", "confidence": 0.9},
    )
    assert result.decision == Decision.APPROVAL_REQUIRED
    assert result.matched_policy == "default"


def test_evaluate_policy_deny_takes_priority(tmp_path, monkeypatch):
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, """
        policies:
          - name: deny_root_cleanup
            description: 禁止根目录清理
            effect: deny
            conditions:
              - runbook_id: disk_cleanup
              - params.path: { in: ["/", "/etc"] }
          - name: low_risk_allow
            description: 低风险放行
            effect: allow
            conditions:
              - runbook_id: disk_cleanup
              - risk_level: low
    """)
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    # /etc 应被 DENY，即使 risk_level=low（DENY 优先）
    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "1.1.1.1", "path": "/etc"},
        alert={},
        plan={"risk_level": "low", "confidence": 0.95},
    )
    assert result.decision == Decision.DENY
    assert result.matched_policy == "deny_root_cleanup"
    assert "根目录" in result.reason


def test_evaluate_policy_require_approval_before_allow(tmp_path, monkeypatch):
    """require_approval 比 allow 优先（即使 conditions 都满足）"""
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, """
        policies:
          - name: prod_must_approve
            description: 生产必须审批
            effect: require_approval
            conditions:
              - host_tier: production
          - name: low_risk_auto
            description: 低风险自动
            effect: allow
            conditions:
              - risk_level: low
              - confidence: { gte: 0.8 }
    """)
    monkeypatch.setattr(settings, "policy_config_path", str(p))
    monkeypatch.setattr(settings, "production_hosts", "1.1.1.1")

    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "1.1.1.1", "path": "/tmp"},
        alert={},
        plan={"risk_level": "low", "confidence": 0.95},
    )
    # 同时匹配 prod_must_approve 和 low_risk_auto，前者赢
    assert result.decision == Decision.APPROVAL_REQUIRED
    assert result.matched_policy == "prod_must_approve"


def test_evaluate_policy_allow_when_all_conditions_match(tmp_path, monkeypatch):
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, """
        policies:
          - name: low_risk_tmp
            description: /tmp 清理低风险
            effect: allow
            conditions:
              - runbook_id: disk_cleanup
              - params.path: /tmp
              - risk_level: low
              - confidence: { gte: 0.9 }
    """)
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "1.1.1.1", "path": "/tmp"},
        alert={},
        plan={"risk_level": "low", "confidence": 0.95},
    )
    assert result.decision == Decision.ALLOW
    assert result.matched_policy == "low_risk_tmp"


def test_evaluate_policy_allow_falls_through_when_condition_fails(tmp_path, monkeypatch):
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, """
        policies:
          - name: low_risk_tmp
            effect: allow
            description: x
            conditions:
              - confidence: { gte: 0.95 }
    """)
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    # confidence 不够 → allow 不命中 → 默认 APPROVAL_REQUIRED
    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "x"},
        alert={},
        plan={"risk_level": "low", "confidence": 0.7},
    )
    assert result.decision == Decision.APPROVAL_REQUIRED
    assert result.matched_policy == "default"


def test_evaluate_policy_uses_host_tier_in_context(tmp_path, monkeypatch):
    """ctx 应包含 host_tier，从 settings 自动推导"""
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, """
        policies:
          - name: prod_deny
            description: 生产拒绝
            effect: deny
            conditions:
              - host_tier: production
    """)
    monkeypatch.setattr(settings, "policy_config_path", str(p))
    monkeypatch.setattr(settings, "production_hosts", "9.9.9.9")

    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "9.9.9.9"},
        alert={},
        plan={},
    )
    assert result.decision == Decision.DENY


def test_evaluate_policy_invalid_yaml_raises(tmp_path, monkeypatch):
    """yaml 损坏应抛异常，让 activity 层降级到默认 APPROVAL_REQUIRED"""
    from src.policy.engine import evaluate_policy

    p = tmp_path / "bad.yaml"
    p.write_text("not: yaml: [broken", encoding="utf-8")
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    with pytest.raises(Exception):
        evaluate_policy(runbook_id="x", params={}, alert={}, plan={})


def test_evaluate_policy_missing_file_raises(monkeypatch):
    from src.policy.engine import evaluate_policy

    monkeypatch.setattr(settings, "policy_config_path", "/nonexistent/policies.yaml")
    with pytest.raises(FileNotFoundError):
        evaluate_policy(runbook_id="x", params={}, alert={}, plan={})


def test_evaluate_policy_top_level_shortcuts_in_context(tmp_path, monkeypatch):
    """规则可以直接用 risk_level/confidence/host_ip 这些顶层字段，不必走 plan.xxx"""
    from src.models import Decision
    from src.policy.engine import evaluate_policy

    p = _write_policy(tmp_path, """
        policies:
          - name: cf_check
            description: confidence 不够走人工
            effect: require_approval
            conditions:
              - confidence: { lt: 0.9 }
    """)
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    result = evaluate_policy(
        runbook_id="disk_cleanup",
        params={"target_host": "x"},
        alert={},
        plan={"risk_level": "low", "confidence": 0.5},
    )
    assert result.decision == Decision.APPROVAL_REQUIRED
    assert result.matched_policy == "cf_check"
