# Phase 3: Policy 层 + Shadow Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Policy 规则引擎 + Shadow/Live 模式开关，按 YAML 规则自动决定告警是 ALLOW（自动执行）/ APPROVAL_REQUIRED（人工审批）/ DENY（拒绝）。Shadow 模式下 ALLOW 决策只记录不真执行，便于上线前 2 周观察期。

**Architecture:** YAML 配置驱动的结构化条件引擎（**不用 eval**，避免注入风险）；workflow 在 resolve runbook 之后、等审批之前调用 policy；主机分级走 .env 列表；Shadow 模式所有 ALLOW 都退化为 APPROVAL_REQUIRED 但飞书卡片做对比标注。

**Tech Stack:** Python 3.14, PyYAML, Pydantic, Temporal, pytest

**Spec:** `docs/生产级 AIOps 架构设计.md` §9.1（Policy 策略设计）+ §8（Shadow Mode 上线策略）

---

## 背景与设计权衡

### 为什么不用 `eval()` 评估表达式

架构文档示例的 `eval_condition` 用 Python eval 跑表达式（`target.tier == "production"`）。即便给了受限 globals，**仍然有风险**：YAML 配置错（如 `__import__`）会让运维误删服务。我们用**结构化条件**替代：

```yaml
# ❌ 不用：表达式形式
deny: 'runbook_id == "disk_cleanup" and params.path in ["/", "/etc"]'

# ✅ 用：结构化形式
effect: deny
conditions:
  - runbook_id: disk_cleanup
  - params.path: { in: ["/", "/etc"] }
```

引擎只支持 8 个固定 operator（eq/ne/in/not_in/gte/lte/gt/lt），无法表达任意代码。

### 决策算法

```
input: rules, ctx
1. 遍历 rules 找 effect=deny 的，全部 conditions 都匹配 → 返回 DENY
2. 遍历 rules 找 effect=require_approval 的，全部 conditions 都匹配 → 返回 APPROVAL_REQUIRED
3. 遍历 rules 找 effect=allow 的，全部 conditions 都匹配 → 返回 ALLOW
4. 都没匹配 → 默认 APPROVAL_REQUIRED（保守）
```

第 1/2/3 步都是顺序遍历——第一个匹配上的规则就 win。

### Shadow vs Live 行为差异

| Decision | `aiops_mode=shadow` | `aiops_mode=live` |
|---|---|---|
| ALLOW | 退化为 APPROVAL_REQUIRED，飞书卡片标 "🌓 Shadow: 本应自动" | 直接执行，飞书事后通知 "🤖 已自动处理" |
| APPROVAL_REQUIRED | 走人工审批（原逻辑） | 同左 |
| DENY | 飞书 "🚫 Policy 拒绝执行"，写审计 | 同左 |

**用户决策：默认 `live`**。企业内自用直接进入自动执行模式。`shadow` 仍保留作为**调试新规则**用——上线一条没把握的 yaml 规则前先切 shadow 看决策结果但不真执行。

### 测试效果验证（用户要求）

为了让用户能直观看到 policy 工作正常，Task 11（端到端验证）必须覆盖三种决策路径：
1. **ALLOW + live → 自动执行** — fill-disk 触发，预期不需点按钮自己跑完
2. **DENY → 早返回** — 用 deny 路径的告警（如清 `/etc`），预期飞书显示拒绝
3. **APPROVAL_REQUIRED → 人工审批** — 低置信度告警，预期飞书带审批按钮

### Workflow 流程改动点

```
旧:  agent_diagnose → 飞书卡片 → wait_condition → resolve_runbook → execute_runbook
新:  agent_diagnose → resolve_runbook → evaluate_policy → 三分支
                                                         ├─ DENY:  通知 + 审计 + 返回 denied
                                                         ├─ ALLOW(live): 执行 + 事后通知
                                                         └─ APPROVAL_REQUIRED 或 ALLOW(shadow):
                                                              飞书审批卡片 → wait_condition → execute
```

注意：`resolve_runbook` 提到 wait_condition 之前，因为 policy 评估需要知道 runbook_id 和 params。

---

## File Map

| 文件 | 操作 | 职责 |
|---|---|---|
| `src/policy/__init__.py` | 新建 | 导出 `Decision`, `PolicyResult`, `evaluate_policy` |
| `src/policy/engine.py` | 新建 | 规则引擎（条件匹配 + 三段式评估） |
| `src/policy/host_tiers.py` | 新建 | 从 settings 查主机 tier |
| `src/policy/policies.yaml` | 新建 | 默认规则配置 |
| `src/activities/policy.py` | 新建 | Temporal activity 封装 |
| `src/models.py` | 修改 | 新增 `Decision` enum + `PolicyResult` |
| `src/config.py` | 修改 | 新增 `aiops_mode`, `production_hosts`, `staging_hosts`, `policy_config_path` |
| `src/workflows/alert_workflow.py` | 修改 | 集成 policy 评估 + 三分支 |
| `src/activities/feishu.py` | 修改 | 卡片支持 AUTO/DENY/SHADOW 状态 |
| `src/main.py` | 修改 | 注册 evaluate_policy activity |
| `pyproject.toml` | 修改 | 加 `pyyaml>=6` |
| `.env.example` | 修改 | 加 `AIOPS_MODE` / `PRODUCTION_HOSTS` 等 |
| `tests/test_policy_engine.py` | 新建 | 条件匹配 + 决策路径单测 |
| `tests/test_policy_activity.py` | 新建 | activity 测试 |
| `tests/test_workflow.py` | 修改 | 加 deny / allow-live / allow-shadow 三种路径 |
| `tests/test_feishu.py` | 修改 | 加 AUTO/DENY/SHADOW 卡片测试 |
| `docs/policy-mode.md` | 新建 | 运维操作手册（如何加规则、切模式） |
| `docs/optional-improvements.md` | 修改 | 标注 Phase 3 完成 |

---

## Task 1: 添加 PyYAML 依赖 + Decision/PolicyResult model

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: 加依赖**

`pyproject.toml` 在 dependencies 列表里添加：

```toml
"pyyaml>=6.0",
```

- [ ] **Step 2: 写 PolicyResult 测试**

`tests/test_models.py` 末尾追加：

```python
from src.models import Decision, PolicyResult


def test_decision_enum_values():
    assert Decision.ALLOW.value == "allow"
    assert Decision.APPROVAL_REQUIRED.value == "approval_required"
    assert Decision.DENY.value == "deny"


def test_policy_result_serialization():
    result = PolicyResult(
        decision=Decision.ALLOW,
        matched_policy="low_risk_disk_cleanup_tmp",
        reason="/tmp 清理 + 高置信度 → 自动",
    )
    j = result.model_dump_json()
    restored = PolicyResult.model_validate_json(j)
    assert restored.decision == Decision.ALLOW
    assert restored.matched_policy == "low_risk_disk_cleanup_tmp"


def test_policy_result_default_decision_is_approval():
    """默认 decision 不能是 ALLOW，避免误开"""
    # 确认没有默认值，必须显式指定
    import pytest
    with pytest.raises(Exception):  # ValidationError
        PolicyResult(matched_policy="x", reason="y")
```

- [ ] **Step 3: 跑测试确认失败**

```bash
pytest tests/test_models.py::test_decision_enum_values -v
```

预期：FAIL（`Decision` 没定义）

- [ ] **Step 4: 实现 Decision + PolicyResult**

`src/models.py` 末尾追加：

```python
from enum import Enum


class Decision(str, Enum):
    """Policy 评估决策"""

    ALLOW = "allow"  # 自动执行
    APPROVAL_REQUIRED = "approval_required"  # 转人工审批
    DENY = "deny"  # 拒绝执行


class PolicyResult(BaseModel):
    """Policy 评估结果"""

    decision: Decision
    matched_policy: str  # 命中的 rule name；默认决策时为 "default"
    reason: str  # 命中理由（来自 rule description）
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_models.py::test_decision_enum_values tests/test_models.py::test_policy_result_serialization tests/test_models.py::test_policy_result_default_decision_is_approval -v
```

预期：3 PASS

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml src/models.py tests/test_models.py
git commit -m "feat(policy): add Decision enum + PolicyResult model"
```

---

## Task 2: 主机分级配置 + 查询 helper

**Files:**
- Modify: `src/config.py`
- Create: `src/policy/__init__.py`
- Create: `src/policy/host_tiers.py`
- Test: `tests/test_policy_engine.py`

- [ ] **Step 1: 写测试**

新建 `tests/test_policy_engine.py`：

```python
from unittest.mock import patch

import pytest

from src.config import settings
from src.policy.host_tiers import lookup_tier


@pytest.fixture
def mock_tiers(monkeypatch):
    monkeypatch.setattr(settings, "production_hosts", "192.168.1.10,192.168.1.11")
    monkeypatch.setattr(settings, "staging_hosts", "192.168.1.20")


def test_lookup_tier_production(mock_tiers):
    assert lookup_tier("192.168.1.10") == "production"
    assert lookup_tier("192.168.1.11") == "production"


def test_lookup_tier_staging(mock_tiers):
    assert lookup_tier("192.168.1.20") == "staging"


def test_lookup_tier_unknown_defaults_to_dev(mock_tiers):
    assert lookup_tier("10.0.0.99") == "dev"


def test_lookup_tier_handles_whitespace_in_settings(monkeypatch):
    monkeypatch.setattr(settings, "production_hosts", " 1.1.1.1 ,2.2.2.2 ")
    monkeypatch.setattr(settings, "staging_hosts", "")
    assert lookup_tier("1.1.1.1") == "production"
    assert lookup_tier("2.2.2.2") == "production"


def test_lookup_tier_none_or_empty_input():
    assert lookup_tier(None) == "dev"
    assert lookup_tier("") == "dev"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_policy_engine.py -v
```

预期：FAIL（模块不存在）

- [ ] **Step 3: 改 config.py 加配置**

`src/config.py` 在 `Settings` class 里添加（任意位置，建议放在 `audit_log_path` 之后）：

```python
    # AIOps 模式：live=按 policy 决策执行（默认）；shadow=只观察不真自动执行（调试新规则用）
    aiops_mode: str = "live"
    # 主机分级（逗号分隔的 IP 列表）；不在 production/staging 列表里的默认 dev
    # VM3 (192.168.198.130) 是测试机，留空使其归 dev
    production_hosts: str = ""
    staging_hosts: str = ""
    # Policy 配置文件路径（容器内绝对路径）
    policy_config_path: str = "/app/src/policy/policies.yaml"
```

- [ ] **Step 4: 实现 host_tiers**

新建 `src/policy/__init__.py`：

```python
"""Policy 策略层：基于 YAML 规则的执行决策

参见 docs/生产级 AIOps 架构设计.md §9.1。
"""

from src.models import Decision, PolicyResult
from src.policy.engine import evaluate_policy

__all__ = ["Decision", "PolicyResult", "evaluate_policy"]
```

新建 `src/policy/host_tiers.py`：

```python
"""主机分级：从 settings.production_hosts / staging_hosts 配置查询

production_hosts 和 staging_hosts 是逗号分隔的 IP 字符串，
不在两者中的主机默认归 'dev'。
"""

from src.config import settings


def _parse_list(raw: str) -> set[str]:
    """逗号分隔字符串 → set，去空白"""
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def lookup_tier(host_ip: str | None) -> str:
    """查询主机所属 tier

    Returns:
        "production" / "staging" / "dev"
    """
    if not host_ip:
        return "dev"
    if host_ip in _parse_list(settings.production_hosts):
        return "production"
    if host_ip in _parse_list(settings.staging_hosts):
        return "staging"
    return "dev"
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_policy_engine.py -v
```

预期：5 PASS

- [ ] **Step 6: 提交**

```bash
git add src/config.py src/policy/__init__.py src/policy/host_tiers.py tests/test_policy_engine.py
git commit -m "feat(policy): add host tier lookup based on settings"
```

---

## Task 3: 条件评估器（结构化条件 → bool）

**Files:**
- Create: `src/policy/engine.py`（先实现 `_match_condition` 等 helper）
- Modify: `tests/test_policy_engine.py`

- [ ] **Step 1: 写测试**

`tests/test_policy_engine.py` 末尾追加：

```python
from src.policy.engine import _get_value, _match_condition


# ---- _get_value ----

def test_get_value_simple_key():
    assert _get_value({"a": 1}, "a") == 1


def test_get_value_dotted_path():
    ctx = {"params": {"path": "/tmp", "min_age_days": 7}}
    assert _get_value(ctx, "params.path") == "/tmp"
    assert _get_value(ctx, "params.min_age_days") == 7


def test_get_value_missing_returns_none():
    assert _get_value({"a": 1}, "b") is None
    assert _get_value({"a": 1}, "a.b.c") is None


def test_get_value_intermediate_not_dict_returns_none():
    """中间路径不是 dict 时不应崩"""
    assert _get_value({"a": "string"}, "a.b") is None


# ---- _match_condition ----

def test_match_condition_equality_default():
    """`field: value` 默认是 eq"""
    assert _match_condition({"runbook_id": "disk_cleanup"}, {"runbook_id": "disk_cleanup"}) is True
    assert _match_condition({"runbook_id": "disk_cleanup"}, {"runbook_id": "service_restart"}) is False


def test_match_condition_in_operator():
    ctx = {"params": {"path": "/tmp"}}
    cond = {"params.path": {"in": ["/tmp", "/var/log"]}}
    assert _match_condition(cond, ctx) is True

    cond_neg = {"params.path": {"in": ["/etc", "/usr"]}}
    assert _match_condition(cond_neg, ctx) is False


def test_match_condition_not_in():
    ctx = {"runbook_id": "disk_cleanup"}
    cond = {"runbook_id": {"not_in": ["service_restart", "exotic_runbook"]}}
    assert _match_condition(cond, ctx) is True


def test_match_condition_gte_lte():
    ctx = {"confidence": 0.9}
    assert _match_condition({"confidence": {"gte": 0.85}}, ctx) is True
    assert _match_condition({"confidence": {"gte": 0.95}}, ctx) is False
    assert _match_condition({"confidence": {"lte": 0.95}}, ctx) is True
    assert _match_condition({"confidence": {"lte": 0.5}}, ctx) is False


def test_match_condition_gt_lt():
    ctx = {"confidence": 0.9}
    assert _match_condition({"confidence": {"gt": 0.85}}, ctx) is True
    assert _match_condition({"confidence": {"gt": 0.9}}, ctx) is False
    assert _match_condition({"confidence": {"lt": 1.0}}, ctx) is True


def test_match_condition_ne():
    ctx = {"risk_level": "low"}
    assert _match_condition({"risk_level": {"ne": "high"}}, ctx) is True
    assert _match_condition({"risk_level": {"ne": "low"}}, ctx) is False


def test_match_condition_unknown_operator_raises():
    """避免 yaml 写错 operator 静默放过"""
    import pytest
    with pytest.raises(ValueError, match="unknown operator"):
        _match_condition({"x": {"weird_op": 1}}, {"x": 1})


def test_match_condition_missing_field_is_false():
    """字段不存在 → 条件不匹配（不抛错，避免规则因可选字段崩）"""
    assert _match_condition({"params.foo": "bar"}, {"params": {}}) is False
    assert _match_condition({"params.path": {"in": ["/tmp"]}}, {}) is False


def test_match_condition_multiple_keys_all_must_match():
    """单个 condition dict 里多个 key 是 AND"""
    ctx = {"runbook_id": "disk_cleanup", "risk_level": "low"}
    assert _match_condition({"runbook_id": "disk_cleanup", "risk_level": "low"}, ctx) is True
    assert _match_condition({"runbook_id": "disk_cleanup", "risk_level": "high"}, ctx) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_policy_engine.py -v
```

预期：新增 13 个测试 FAIL（`_get_value` / `_match_condition` 不存在）

- [ ] **Step 3: 实现 helper**

新建 `src/policy/engine.py`（部分，先到 helper）：

```python
"""Policy 规则引擎

YAML schema:
  policies:
    - name: <规则名>
      description: <人类可读描述，会写到决策 reason>
      effect: <allow | require_approval | deny>
      conditions:
        - <field>: <value>          # 默认 eq
        - <field>: { in: [...] }    # 显式 operator
        - ...

field 支持点分路径（如 params.path），多个 condition 之间是 AND。
"""

from typing import Any

# 支持的 operator 表
_OPERATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "gte": lambda a, b: a is not None and a >= b,
    "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b,
    "lt": lambda a, b: a is not None and a < b,
}


def _get_value(ctx: dict, path: str) -> Any:
    """按点分路径取值。中间不是 dict 或 key 不存在 → None"""
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match_condition(condition: dict, ctx: dict) -> bool:
    """评估一个 condition dict（多 key 之间 AND）"""
    for field, expected in condition.items():
        actual = _get_value(ctx, field)

        if isinstance(expected, dict):
            # operator 形式: {op: value}
            if len(expected) != 1:
                raise ValueError(f"condition for {field!r} must have exactly one operator")
            op_name, op_value = next(iter(expected.items()))
            if op_name not in _OPERATORS:
                raise ValueError(f"unknown operator {op_name!r}")
            if not _OPERATORS[op_name](actual, op_value):
                return False
        else:
            # 默认 eq
            if actual != expected:
                return False
    return True
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_policy_engine.py -v
```

预期：所有 PASS

- [ ] **Step 5: 提交**

```bash
git add src/policy/engine.py tests/test_policy_engine.py
git commit -m "feat(policy): add structured condition evaluator"
```

---

## Task 4: evaluate_policy 主流程 + 规则加载

**Files:**
- Modify: `src/policy/engine.py`
- Modify: `tests/test_policy_engine.py`

- [ ] **Step 1: 写测试**

`tests/test_policy_engine.py` 末尾追加：

```python
import textwrap

from src.policy.engine import evaluate_policy
from src.models import Decision


def _write_policy(tmp_path, yaml_content: str):
    p = tmp_path / "policies.yaml"
    p.write_text(textwrap.dedent(yaml_content), encoding="utf-8")
    return p


def test_evaluate_policy_default_is_approval_required(tmp_path, monkeypatch):
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
    p = tmp_path / "bad.yaml"
    p.write_text("not: yaml: [broken", encoding="utf-8")
    monkeypatch.setattr(settings, "policy_config_path", str(p))

    import pytest
    with pytest.raises(Exception):
        evaluate_policy(runbook_id="x", params={}, alert={}, plan={})


def test_evaluate_policy_missing_file_raises(monkeypatch):
    monkeypatch.setattr(settings, "policy_config_path", "/nonexistent/policies.yaml")
    import pytest
    with pytest.raises(FileNotFoundError):
        evaluate_policy(runbook_id="x", params={}, alert={}, plan={})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_policy_engine.py -v
```

预期：8 个新测试 FAIL（`evaluate_policy` 未实现）

- [ ] **Step 3: 实现 evaluate_policy**

`src/policy/engine.py` 末尾追加：

```python
import yaml

from src.config import settings
from src.models import Decision, PolicyResult
from src.policy.host_tiers import lookup_tier


def _build_context(*, runbook_id: str, params: dict, alert: dict, plan: dict) -> dict:
    """组装 condition 评估时的扁平 ctx"""
    return {
        "runbook_id": runbook_id,
        "params": params or {},
        "alert": alert or {},
        "plan": plan or {},
        # 顶层快捷字段（用得多的）
        "risk_level": (plan or {}).get("risk_level"),
        "confidence": (plan or {}).get("confidence", 0),
        "host_ip": (params or {}).get("target_host"),
        "host_tier": lookup_tier((params or {}).get("target_host")),
    }


def _load_policies() -> list[dict]:
    """读 yaml 配置，返回 policies 列表"""
    with open(settings.policy_config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("policies") or []


def _match_all_conditions(conditions: list[dict], ctx: dict) -> bool:
    """conditions 列表里每条都要匹配（AND）"""
    return all(_match_condition(c, ctx) for c in conditions)


def evaluate_policy(*, runbook_id: str, params: dict, alert: dict, plan: dict) -> PolicyResult:
    """三段式评估：deny → require_approval → allow → default(approval)

    任何阶段第一个匹配的规则即返回。
    """
    policies = _load_policies()
    ctx = _build_context(runbook_id=runbook_id, params=params, alert=alert, plan=plan)

    # 阶段 1: DENY 优先
    for p in policies:
        if p.get("effect") == "deny" and _match_all_conditions(p.get("conditions") or [], ctx):
            return PolicyResult(
                decision=Decision.DENY,
                matched_policy=p.get("name", "unnamed"),
                reason=p.get("description", ""),
            )

    # 阶段 2: APPROVAL_REQUIRED
    for p in policies:
        if p.get("effect") == "require_approval" and _match_all_conditions(p.get("conditions") or [], ctx):
            return PolicyResult(
                decision=Decision.APPROVAL_REQUIRED,
                matched_policy=p.get("name", "unnamed"),
                reason=p.get("description", ""),
            )

    # 阶段 3: ALLOW
    for p in policies:
        if p.get("effect") == "allow" and _match_all_conditions(p.get("conditions") or [], ctx):
            return PolicyResult(
                decision=Decision.ALLOW,
                matched_policy=p.get("name", "unnamed"),
                reason=p.get("description", ""),
            )

    # 默认：保守审批
    return PolicyResult(
        decision=Decision.APPROVAL_REQUIRED,
        matched_policy="default",
        reason="no allow rule matched, falling back to manual approval",
    )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_policy_engine.py -v
```

预期：所有 PASS

- [ ] **Step 5: 提交**

```bash
git add src/policy/engine.py tests/test_policy_engine.py
git commit -m "feat(policy): implement evaluate_policy with 3-stage decision flow"
```

---

## Task 5: 默认 policies.yaml + 配置 schema 文档

**Files:**
- Create: `src/policy/policies.yaml`

- [ ] **Step 1: 写默认规则**

新建 `src/policy/policies.yaml`：

```yaml
# Policy 规则配置
#
# Schema:
#   policies:
#     - name: <唯一规则名>
#       description: <人类可读，会写到飞书卡片 / 审计>
#       effect: allow | require_approval | deny
#       conditions:
#         - <field>: <value>           # 默认 eq
#         - <field>: { in: [...] }     # 显式 operator
#         - <field>: { gte: 0.85 }
#
# 评估顺序：所有 deny → 所有 require_approval → 所有 allow → 默认 approval
# 同阶段内顺序遍历，第一个匹配的规则即返回。
#
# 可用 ctx 字段：
#   runbook_id          str      "disk_cleanup" | "service_restart"
#   params              dict     runbook 参数
#   params.path         str      disk_cleanup 专用
#   params.service_name str      service_restart 专用
#   params.target_host  str      目标 IP
#   risk_level          str      "low" | "medium" | "high"
#   confidence          float    0.0~1.0
#   host_tier           str      "production" | "staging" | "dev"
#   host_ip             str      与 params.target_host 相同
#   alert.severity      str      告警级别
#
# 可用 operator: eq(默认) / ne / in / not_in / gte / lte / gt / lt

policies:
  # ========== DENY: 永远不能自动执行的红线 ==========

  - name: deny_root_path_cleanup
    description: 禁止清理根目录或系统关键路径
    effect: deny
    conditions:
      - runbook_id: disk_cleanup
      - params.path: { in: ["/", "/etc", "/usr", "/bin", "/boot", "/lib"] }

  - name: deny_database_restart
    description: 数据库服务禁止自动重启（必须人工确认主从状态）
    effect: deny
    conditions:
      - runbook_id: service_restart
      - params.service_name: { in: ["mysql", "mysqld", "mariadb", "postgresql", "postgres", "mongodb", "redis"] }

  # ========== APPROVAL_REQUIRED: 必须人工审批的场景 ==========

  - name: production_requires_approval
    description: 生产主机所有变更都必须人工审批
    effect: require_approval
    conditions:
      - host_tier: production

  - name: high_risk_requires_approval
    description: agent 自评 risk=high 的强制人工审批
    effect: require_approval
    conditions:
      - risk_level: high

  - name: low_confidence_requires_approval
    description: agent 置信度 < 0.9 转人工
    effect: require_approval
    conditions:
      - confidence: { lt: 0.9 }

  # ========== ALLOW: 可自动执行的低风险场景 ==========

  - name: low_risk_disk_cleanup
    description: 临时目录/日志/缓存清理 + 低风险 + 高置信度 → 自动
    effect: allow
    conditions:
      - runbook_id: disk_cleanup
      - params.path: { in: ["/tmp", "/var/tmp", "/var/log", "/var/cache", "/opt/cache"] }
      - risk_level: low
      - confidence: { gte: 0.9 }

  - name: low_risk_stateless_service_restart
    description: 无状态服务（nginx / redis-server）重启 + 低风险 + 高置信度 → 自动
    effect: allow
    conditions:
      - runbook_id: service_restart
      - params.service_name: { in: ["nginx", "redis-server"] }
      - risk_level: low
      - confidence: { gte: 0.9 }

  # 不在以上任何规则匹配的告警，默认走人工审批
```

> 服务白名单**只保留 nginx 和 redis-server**。apache2/docker/sshd 这些重启失败有较大影响（sshd 直接断 SSH，docker 影响所有容器），暂时不开放自动执行，全走人工审批。后续观察自动执行 nginx/redis 跑稳了再扩展。

- [ ] **Step 2: 验证 yaml 合法**

```bash
python -c "import yaml; yaml.safe_load(open('src/policy/policies.yaml', encoding='utf-8'))"
```

预期：无输出（成功解析）

- [ ] **Step 3: 提交**

```bash
git add src/policy/policies.yaml
git commit -m "feat(policy): add default policies.yaml with sane safety defaults"
```

---

## Task 6: Policy Activity 封装

**Files:**
- Create: `src/activities/policy.py`
- Create: `tests/test_policy_activity.py`

- [ ] **Step 1: 写测试**

新建 `tests/test_policy_activity.py`：

```python
import json
from unittest.mock import patch

import pytest

from src.activities.policy import evaluate_policy_activity
from src.models import Decision, PolicyResult


@pytest.mark.asyncio
async def test_evaluate_policy_activity_returns_json():
    """activity 接收 json 输入，返回 json 输出（Temporal 友好）"""
    fake_result = PolicyResult(
        decision=Decision.ALLOW,
        matched_policy="test_rule",
        reason="test",
    )
    with patch("src.activities.policy.evaluate_policy", return_value=fake_result):
        result_json = await evaluate_policy_activity(
            runbook_id="disk_cleanup",
            runbook_params_json='{"target_host": "1.1.1.1", "path": "/tmp"}',
            alert_json='{"severity": "high"}',
            plan_json='{"risk_level": "low", "confidence": 0.9}',
        )

    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.ALLOW
    assert decoded.matched_policy == "test_rule"


@pytest.mark.asyncio
async def test_evaluate_policy_activity_handles_missing_plan():
    """plan_json 可以是 'null'（agent 失败时），应返回默认 APPROVAL_REQUIRED"""
    with patch("src.activities.policy._load_policies", return_value=[]):
        result_json = await evaluate_policy_activity(
            runbook_id="disk_cleanup",
            runbook_params_json='{"target_host": "1.1.1.1"}',
            alert_json='{"severity": "high"}',
            plan_json="null",
        )
    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.APPROVAL_REQUIRED


@pytest.mark.asyncio
async def test_evaluate_policy_activity_safe_on_yaml_failure(monkeypatch):
    """yaml 损坏时不抛错，降级返回 APPROVAL_REQUIRED 让 workflow 走人工"""
    from src.config import settings
    monkeypatch.setattr(settings, "policy_config_path", "/nonexistent/policies.yaml")

    result_json = await evaluate_policy_activity(
        runbook_id="disk_cleanup",
        runbook_params_json='{"target_host": "1.1.1.1"}',
        alert_json="{}",
        plan_json='{"risk_level": "low", "confidence": 0.9}',
    )
    decoded = PolicyResult.model_validate_json(result_json)
    assert decoded.decision == Decision.APPROVAL_REQUIRED
    assert "policy evaluation error" in decoded.reason.lower()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_policy_activity.py -v
```

预期：FAIL（模块不存在）

- [ ] **Step 3: 实现 activity**

新建 `src/activities/policy.py`：

```python
"""Policy 评估 Activity

把 evaluate_policy 包装成 Temporal Activity：
  - 输入/输出全用 JSON 字符串（Temporal 友好）
  - YAML 损坏 / 文件不存在等异常一律降级为 APPROVAL_REQUIRED
    （绝不能因为 policy 出问题就把告警丢了）
"""

import json
import logging

from temporalio import activity

from src.models import Decision, PolicyResult
from src.policy.engine import _load_policies, evaluate_policy

logger = logging.getLogger(__name__)


@activity.defn
async def evaluate_policy_activity(
    runbook_id: str,
    runbook_params_json: str,
    alert_json: str,
    plan_json: str,
) -> str:
    """Policy 评估，返回 PolicyResult JSON

    plan_json 可以是 "null"（agent 失败时），此时按空 plan 评估，
    通常会落到 default APPROVAL_REQUIRED。
    """
    try:
        params = json.loads(runbook_params_json) if runbook_params_json else {}
        alert = json.loads(alert_json) if alert_json else {}
        plan_raw = json.loads(plan_json) if plan_json else {}
        plan = plan_raw if isinstance(plan_raw, dict) else {}

        result = evaluate_policy(runbook_id=runbook_id, params=params, alert=alert, plan=plan)
        return result.model_dump_json()
    except Exception as exc:
        logger.exception("policy evaluation failed, falling back to APPROVAL_REQUIRED")
        fallback = PolicyResult(
            decision=Decision.APPROVAL_REQUIRED,
            matched_policy="default",
            reason=f"policy evaluation error, falling back to manual approval: {exc}",
        )
        return fallback.model_dump_json()


# 让 _load_policies 也对外可见，方便测试 mock
__all__ = ["evaluate_policy_activity", "_load_policies", "evaluate_policy"]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_policy_activity.py -v
```

预期：3 PASS

- [ ] **Step 5: 提交**

```bash
git add src/activities/policy.py tests/test_policy_activity.py
git commit -m "feat(policy): add evaluate_policy temporal activity with safe fallback"
```

---

## Task 7: Workflow 集成 - 调用 policy 决策

**Files:**
- Modify: `src/workflows/alert_workflow.py`

注意：本 task 只接通 policy 评估调用，不动飞书卡片和审批分支。下一个 task 才做三分支。

- [ ] **Step 1: 看一下当前 workflow 流程**

```bash
grep -n "execute_activity\|wait_condition\|_resolve_runbook" src/workflows/alert_workflow.py | head -20
```

确认你看到的步骤顺序：agent_diagnose → 飞书卡片 → wait_condition → resolve_runbook → execute_runbook

我们要把 resolve_runbook 提到 wait_condition 之前。

- [ ] **Step 2: 改 workflow.run**

在 `src/workflows/alert_workflow.py` 的 `run()` 方法里，**重排**步骤 4-6：

旧顺序：
```python
# 4. 推送飞书卡片
feishu_msg_id = await workflow.execute_activity("send_feishu_alert_with_agent", ...)
# 5. 等审批
await workflow.wait_condition(...)
# 6. 决定 runbook
runbook_id, runbook_params = self._resolve_runbook(...)
```

新顺序：
```python
# 4. 决定 runbook（提前到这里，因为 policy 需要）
runbook_id, runbook_params = self._resolve_runbook(plan_dict, alert)

# 4b. 不支持的 runbook 早返回
if runbook_id is None:
    # ... unsupported 分支 ...

# 4c. Policy 评估
policy_result_json = await workflow.execute_activity(
    "evaluate_policy_activity",
    args=[runbook_id, runbook_params, alert_json, json.dumps(plan_dict) if plan_dict else "null"],
    start_to_close_timeout=timedelta(seconds=10),
    retry_policy=_NOTIFY_RETRY,
)

# 5. 推送飞书卡片（带 policy 决策标签）
feishu_msg_id = await workflow.execute_activity(
    "send_feishu_alert_with_agent",
    args=[alert_json, workflow_id, agent_output_json, policy_result_json],
    ...
)

# 6. 等审批（暂时所有 decision 都走这里，下个 task 加分支）
await workflow.wait_condition(...)

# 7. 执行 runbook
exec_result_json = await workflow.execute_activity("execute_runbook", ...)
```

完整修改后的 `run()` 方法：

```python
@workflow.run
async def run(self, alert_json: str) -> str:
    alert = json.loads(alert_json)
    event_id = alert["event_id"]
    workflow_id = workflow.info().workflow_id

    # 1. ReAct agent 诊断
    agent_output_json = await self._safe_agent_call(alert_json)
    plan_dict, _trace = self._parse_agent_output(agent_output_json)

    # 2. 决定 runbook + 参数
    runbook_id, runbook_params = self._resolve_runbook(plan_dict, alert)

    # 3. 不支持的 runbook 早返回
    if runbook_id is None:
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "unsupported", None, None, None, None],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        await workflow.execute_activity(
            "send_feishu_result",
            args=[f"Unsupported alert {event_id}: no matching runbook"],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        return "unsupported"

    # 4. Policy 评估
    plan_json_for_policy = json.dumps(plan_dict) if plan_dict else "null"
    policy_result_json = await workflow.execute_activity(
        "evaluate_policy_activity",
        args=[runbook_id, runbook_params, alert_json, plan_json_for_policy],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )

    # 5. 推送飞书卡片（含 policy 决策结果）
    if plan_dict is not None:
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert_with_agent",
            args=[alert_json, workflow_id, agent_output_json, policy_result_json],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_NOTIFY_RETRY,
        )
    else:
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert",
            args=[alert_json, workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_NOTIFY_RETRY,
        )

    # 6. 等审批信号（30 分钟超时）—— 下个 task 在这里加 policy 三分支
    try:
        await workflow.wait_condition(
            lambda: self._approval_received,
            timeout=timedelta(minutes=30),
        )
    except TimeoutError:
        # ... timeout 分支不变 ...
        return "timeout"

    if not self._approved:
        # ... rejected 分支不变 ...
        return "rejected"

    # 7. 执行 Runbook
    exec_result_json = await workflow.execute_activity(
        "execute_runbook",
        args=[runbook_id, runbook_params],
        start_to_close_timeout=timedelta(minutes=10),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )

    # 8. 写审计 + 飞书通知（不变）
    # ...

    return "approved"
```

> 注意：写审计、超时、拒绝、结果通知这几段保留原样，只是位置往后挪了。整段的完整代码我下面给。

完整 `run()` 方法（粘贴替换原来的 `run`）：

```python
@workflow.run
async def run(self, alert_json: str) -> str:
    alert = json.loads(alert_json)
    event_id = alert["event_id"]
    workflow_id = workflow.info().workflow_id

    # 1. ReAct agent 诊断
    agent_output_json = await self._safe_agent_call(alert_json)
    plan_dict, _trace = self._parse_agent_output(agent_output_json)

    # 2. 决定 runbook + 参数
    runbook_id, runbook_params = self._resolve_runbook(plan_dict, alert)

    # 3. 不支持的 runbook → 早返回
    if runbook_id is None:
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "unsupported", None, None, None, None],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        await workflow.execute_activity(
            "send_feishu_result",
            args=[f"Unsupported alert {event_id}: no matching runbook"],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        return "unsupported"

    # 4. Policy 评估
    plan_json_for_policy = json.dumps(plan_dict) if plan_dict else "null"
    policy_result_json = await workflow.execute_activity(
        "evaluate_policy_activity",
        args=[runbook_id, runbook_params, alert_json, plan_json_for_policy],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )

    # 5. 推送飞书卡片
    if plan_dict is not None:
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert_with_agent",
            args=[alert_json, workflow_id, agent_output_json, policy_result_json],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_NOTIFY_RETRY,
        )
    else:
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert",
            args=[alert_json, workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_NOTIFY_RETRY,
        )

    # 6. 等审批
    try:
        await workflow.wait_condition(
            lambda: self._approval_received,
            timeout=timedelta(minutes=30),
        )
    except TimeoutError:
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "timeout", None, None, None, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        await workflow.execute_activity(
            "send_feishu_result",
            args=[f"⏰ 告警 {event_id} 审批超时（30分钟），已跳过"],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        return "timeout"

    if not self._approved:
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "rejected", None, None, None, feishu_msg_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        await workflow.execute_activity(
            "send_feishu_result",
            args=[f"❌ 告警 {event_id} 已被拒绝"],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        return "rejected"

    # 7. 执行 Runbook
    exec_result_json = await workflow.execute_activity(
        "execute_runbook",
        args=[runbook_id, runbook_params],
        start_to_close_timeout=timedelta(minutes=10),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )

    # 8. 写审计
    await workflow.execute_activity(
        "write_audit",
        args=[alert_json, workflow_id, "approved", runbook_id, runbook_params, exec_result_json, feishu_msg_id],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )

    # 9. 飞书结果通知
    exec_result = json.loads(exec_result_json)
    if exec_result.get("verify"):
        msg = f"✅ 告警 {event_id} 处理成功（Runbook: {runbook_id}）"
    elif not exec_result.get("dry_run", {}).get("success"):
        msg = f"⚠️ 告警 {event_id} Runbook 预检失败，未执行实际操作"
    elif not exec_result.get("execute", {}).get("success"):
        msg = f"⚠️ 告警 {event_id} Runbook 执行失败"
    else:
        msg = f"⚠️ 告警 {event_id} 执行完成但验证未通过，可能需要人工介入"

    await workflow.execute_activity(
        "send_feishu_result",
        args=[msg],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )

    return "approved"
```

- [ ] **Step 3: 改 send_feishu_alert_with_agent 签名（新增 policy_result_json 参数）**

下个 task 改 feishu.py 时再处理签名。**这个 task 暂时让它接受多余的参数但忽略**——为了 workflow 测试能跑通：

`src/activities/feishu.py` 的 `send_feishu_alert_with_agent` 临时签名：

```python
@activity.defn
async def send_feishu_alert_with_agent(
    alert_json: str,
    workflow_id: str,
    agent_output_json: str,
    policy_result_json: str = "{}",  # 新增，下个 task 实装
) -> str:
    # 现有实现暂不变
    ...
```

- [ ] **Step 4: 跑现有 workflow 测试看不破**

```bash
pytest tests/test_workflow.py -v
```

预期：测试需要 mock `evaluate_policy_activity`，否则会失败。先看看具体 fail 信息。

- [ ] **Step 5: 在 test_workflow.py 加 mock**

`tests/test_workflow.py` 顶部加：

```python
@activity.defn(name="evaluate_policy_activity")
async def mock_evaluate_policy(
    runbook_id: str, runbook_params_json: str, alert_json: str, plan_json: str
) -> str:
    # 默认返回 APPROVAL_REQUIRED，让现有测试都走原审批路径
    return '{"decision":"approval_required","matched_policy":"default","reason":""}'
```

把它加到 `ALL_ACTIVITIES` 列表里，以及每个独立指定 activities 的 worker（test_workflow_falls_back_when_agent_fails / test_workflow_unsupported_when_no_runbook_match / test_workflow_handles_agent_choosing_none）。

- [ ] **Step 6: 跑测试确认通过**

```bash
pytest tests/test_workflow.py -v
```

预期：所有原测试 PASS

- [ ] **Step 7: 提交**

```bash
git add src/workflows/alert_workflow.py src/activities/feishu.py tests/test_workflow.py
git commit -m "feat(policy): wire policy evaluation into workflow (no branching yet)"
```

---

## Task 8: Workflow 三分支 - DENY / ALLOW(live) / 走审批

**Files:**
- Modify: `src/workflows/alert_workflow.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: 写测试 - DENY 路径**

`tests/test_workflow.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_workflow_denied_by_policy():
    """policy 返回 deny → workflow 不等审批，直接 denied 返回"""

    @activity.defn(name="evaluate_policy_activity")
    async def deny_policy(runbook_id, runbook_params_json, alert_json, plan_json):
        return '{"decision":"deny","matched_policy":"deny_critical","reason":"禁止操作"}'

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[AlertWorkflow],
            activities=[
                mock_agent_diagnose, deny_policy,
                mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
                mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
            ],
        ):
            handle = await env.client.start_workflow(
                AlertWorkflow.run,
                _alert_json(),
                id="test-denied",
                task_queue=TASK_QUEUE,
            )
            # 故意不发审批信号 —— deny 应该直接返回，不阻塞
            result = await handle.result()
            assert result == "denied"


@pytest.mark.asyncio
async def test_workflow_auto_executes_in_live_mode():
    """policy=allow 且 aiops_mode=live → 跳过审批直接执行"""
    from src.config import settings

    @activity.defn(name="evaluate_policy_activity")
    async def allow_policy(runbook_id, runbook_params_json, alert_json, plan_json):
        return '{"decision":"allow","matched_policy":"low_risk","reason":"auto"}'

    async with await WorkflowEnvironment.start_time_skipping() as env:
        original_mode = settings.aiops_mode
        settings.aiops_mode = "live"
        try:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[AlertWorkflow],
                activities=[
                    mock_agent_diagnose, allow_policy,
                    mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
                    mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
                ],
            ):
                handle = await env.client.start_workflow(
                    AlertWorkflow.run,
                    _alert_json(),
                    id="test-auto-live",
                    task_queue=TASK_QUEUE,
                )
                # 故意不发审批信号 —— live + allow 应直接执行
                result = await handle.result()
                assert result == "auto_approved"
        finally:
            settings.aiops_mode = original_mode


@pytest.mark.asyncio
async def test_workflow_shadow_mode_does_not_auto_execute():
    """policy=allow 但 aiops_mode=shadow → 仍走人工审批"""
    from src.config import settings

    @activity.defn(name="evaluate_policy_activity")
    async def allow_policy(runbook_id, runbook_params_json, alert_json, plan_json):
        return '{"decision":"allow","matched_policy":"low_risk","reason":"auto"}'

    async with await WorkflowEnvironment.start_time_skipping() as env:
        original_mode = settings.aiops_mode
        settings.aiops_mode = "shadow"
        try:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[AlertWorkflow],
                activities=[
                    mock_agent_diagnose, allow_policy,
                    mock_send_feishu_alert_with_agent, mock_send_feishu_alert,
                    mock_send_feishu_result, mock_write_audit, mock_execute_runbook,
                ],
            ):
                handle = await env.client.start_workflow(
                    AlertWorkflow.run,
                    _alert_json(),
                    id="test-shadow",
                    task_queue=TASK_QUEUE,
                )
                # shadow 模式下仍要等审批
                await handle.signal(AlertWorkflow.approve, ApprovalDecision(approved=True))
                result = await handle.result()
                assert result == "approved"  # 经过人工审批的 approved，不是 auto_approved
        finally:
            settings.aiops_mode = original_mode
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_workflow.py::test_workflow_denied_by_policy tests/test_workflow.py::test_workflow_auto_executes_in_live_mode tests/test_workflow.py::test_workflow_shadow_mode_does_not_auto_execute -v
```

预期：3 FAIL（workflow 还没实现这些分支）

- [ ] **Step 3: 实现三分支**

`src/workflows/alert_workflow.py` 在 `run()` 方法中找到 `# 4. Policy 评估` 块，**之后**插入分支处理。完整改动如下（替换从 "# 4. Policy 评估" 到 "# 6. 等审批" 之前的内容）：

```python
    # 4. Policy 评估
    plan_json_for_policy = json.dumps(plan_dict) if plan_dict else "null"
    policy_result_json = await workflow.execute_activity(
        "evaluate_policy_activity",
        args=[runbook_id, runbook_params, alert_json, plan_json_for_policy],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )
    policy_result = json.loads(policy_result_json)
    policy_decision = policy_result.get("decision", "approval_required")

    # 5. DENY 分支：拒绝执行 + 通知 + 早返回
    if policy_decision == "deny":
        await workflow.execute_activity(
            "write_audit",
            args=[alert_json, workflow_id, "denied", runbook_id, runbook_params, None, None],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        await workflow.execute_activity(
            "send_feishu_result",
            args=[
                f"🚫 告警 {event_id} 被 Policy 拒绝执行（规则 `{policy_result.get('matched_policy')}`）："
                f"{policy_result.get('reason', '')}"
            ],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=_NOTIFY_RETRY,
        )
        return "denied"

    # 6. 推送飞书卡片
    if plan_dict is not None:
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert_with_agent",
            args=[alert_json, workflow_id, agent_output_json, policy_result_json],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_NOTIFY_RETRY,
        )
    else:
        feishu_msg_id = await workflow.execute_activity(
            "send_feishu_alert",
            args=[alert_json, workflow_id],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_NOTIFY_RETRY,
        )

    # 7. ALLOW + live 模式：跳过审批直接执行
    auto_execute = policy_decision == "allow" and settings.aiops_mode == "live"

    if not auto_execute:
        # 7a. 等审批信号（30 分钟超时）
        try:
            await workflow.wait_condition(
                lambda: self._approval_received,
                timeout=timedelta(minutes=30),
            )
        except TimeoutError:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "timeout", None, None, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"⏰ 告警 {event_id} 审批超时（30分钟），已跳过"],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            return "timeout"

        if not self._approved:
            await workflow.execute_activity(
                "write_audit",
                args=[alert_json, workflow_id, "rejected", None, None, None, feishu_msg_id],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            await workflow.execute_activity(
                "send_feishu_result",
                args=[f"❌ 告警 {event_id} 已被拒绝"],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=_NOTIFY_RETRY,
            )
            return "rejected"

    # 8. 执行 Runbook
    exec_result_json = await workflow.execute_activity(
        "execute_runbook",
        args=[runbook_id, runbook_params],
        start_to_close_timeout=timedelta(minutes=10),
        retry_policy=RetryPolicy(maximum_attempts=1),
    )

    # 9. 写审计
    decision_label = "auto_approved" if auto_execute else "approved"
    await workflow.execute_activity(
        "write_audit",
        args=[alert_json, workflow_id, decision_label, runbook_id, runbook_params, exec_result_json, feishu_msg_id],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )

    # 10. 飞书结果通知
    exec_result = json.loads(exec_result_json)
    auto_prefix = "🤖 自动" if auto_execute else "✅"
    if exec_result.get("verify"):
        msg = f"{auto_prefix} 告警 {event_id} 处理成功（Runbook: {runbook_id}）"
    elif not exec_result.get("dry_run", {}).get("success"):
        msg = f"⚠️ 告警 {event_id} Runbook 预检失败"
    elif not exec_result.get("execute", {}).get("success"):
        msg = f"⚠️ 告警 {event_id} Runbook 执行失败"
    else:
        msg = f"⚠️ 告警 {event_id} 执行完成但验证未通过"

    await workflow.execute_activity(
        "send_feishu_result",
        args=[msg],
        start_to_close_timeout=timedelta(seconds=10),
        retry_policy=_NOTIFY_RETRY,
    )

    return decision_label
```

- [ ] **Step 4: 跑全部 workflow 测试确认通过**

```bash
pytest tests/test_workflow.py -v
```

预期：所有 PASS（包括新增的 3 个 + 原有的）

- [ ] **Step 5: 提交**

```bash
git add src/workflows/alert_workflow.py tests/test_workflow.py
git commit -m "feat(policy): add DENY / ALLOW(live) / approval-required branches to workflow"
```

---

## Task 9: 飞书卡片 - Policy 决策标签 + AUTO/DENY/SHADOW 状态

**Files:**
- Modify: `src/activities/feishu.py`
- Modify: `tests/test_feishu.py`

- [ ] **Step 1: 写测试**

`tests/test_feishu.py` 末尾追加：

```python
def test_card_with_agent_shows_policy_label_for_allow_live(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "aiops_mode", "live")

    plan = {"runbook_id": "disk_cleanup", "params": {}, "risk_level": "low",
            "reasoning": "x", "confidence": 0.9}
    policy = {"decision": "allow", "matched_policy": "low_risk", "reason": "low risk auto"}

    card = build_feishu_card_with_agent(_make_alert(), "wf-1", plan, [], policy)
    s = str(card)
    assert "🤖 自动执行" in s or "auto" in s.lower()


def test_card_with_agent_shows_shadow_label(monkeypatch):
    """shadow 模式下 allow 决策卡片要明确标 Shadow"""
    from src.config import settings
    monkeypatch.setattr(settings, "aiops_mode", "shadow")

    plan = {"runbook_id": "disk_cleanup", "params": {}, "risk_level": "low",
            "reasoning": "x", "confidence": 0.9}
    policy = {"decision": "allow", "matched_policy": "low_risk", "reason": "auto in live"}

    card = build_feishu_card_with_agent(_make_alert(), "wf-1", plan, [], policy)
    s = str(card)
    assert "Shadow" in s or "🌓" in s


def test_card_with_agent_shows_approval_required(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "aiops_mode", "live")

    plan = {"runbook_id": "service_restart", "params": {}, "risk_level": "medium",
            "reasoning": "x", "confidence": 0.6}
    policy = {"decision": "approval_required", "matched_policy": "low_confidence",
              "reason": "需要人工"}

    card = build_feishu_card_with_agent(_make_alert(), "wf-1", plan, [], policy)
    s = str(card)
    assert "需要人工" in s or "审批" in s
```

`tests/test_feishu.py` 中已存在的 `test_build_feishu_card_with_agent_low_risk` 等测试调用 `build_feishu_card_with_agent` 时缺第 5 个参数 `policy`，需要补：

```python
def test_build_feishu_card_with_agent_low_risk():
    plan = {...}
    trace = [...]
    policy = {"decision": "approval_required", "matched_policy": "default", "reason": ""}
    card = build_feishu_card_with_agent(_make_alert(), "wf-1", plan, trace, policy)
    ...
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_feishu.py -v
```

- [ ] **Step 3: 改 feishu.py 加 policy 区块**

`src/activities/feishu.py` 修改 `build_feishu_card_with_agent` 签名：

```python
def build_feishu_card_with_agent(
    alert: Alert, workflow_id: str, plan: dict, trace: list[dict], policy: dict | None = None,
) -> dict:
    """带 agent 诊断 + policy 决策的卡片"""
    from src.config import settings  # 延迟导入避免循环

    # ... 原有 confidence_pct / risk_label 等 ...

    # 新增：policy 标签区块
    policy = policy or {}
    decision = policy.get("decision", "approval_required")
    policy_label = _format_policy_label(decision, settings.aiops_mode, policy)

    # ai_section 修改：加上 policy_label 行
    ai_section = {
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"**🤖 AI 诊断：**\n"
                f"结论：{reasoning}\n"
                f"置信度：{confidence_pct} | 风险：{risk_label}\n"
                f"建议 Runbook：`{runbook_id}`\n"
                f"参数：`{params}`\n"
                f"**Policy：**{policy_label}"
            ),
        },
    }
    # ... 其余不变 ...
```

新增 helper 函数：

```python
def _format_policy_label(decision: str, mode: str, policy: dict) -> str:
    """根据 decision + mode 渲染 policy 标签"""
    rule = policy.get("matched_policy", "default")
    reason = policy.get("reason", "")

    if decision == "deny":
        return f"🚫 拒绝执行（规则 `{rule}`：{reason}）"
    if decision == "allow":
        if mode == "live":
            return f"🤖 自动执行（规则 `{rule}`）"
        else:  # shadow
            return f"🌓 Shadow 模式：本应自动执行（规则 `{rule}`）但仍需审批"
    # approval_required 或其它
    return f"👤 需要人工审批（规则 `{rule}`：{reason or '默认审批'}）"
```

修改 actions 渲染，shadow + allow 时也保留按钮（仍走人工审批），auto + live 时不要按钮：

```python
    actions = []
    if decision == "deny":
        # 拒绝时不展示任何按钮
        pass
    elif decision == "allow" and settings.aiops_mode == "live":
        # 自动执行：不展示审批按钮
        pass
    else:
        # approval_required 或 shadow + allow
        actions = [
            _action_button("按建议执行", "primary", workflow_id, "approve", alert.event_id),
            _action_button("拒绝", "danger", workflow_id, "reject", alert.event_id),
        ]
        if risk_level == "high":
            actions.insert(1, _action_button("⚠️ 高风险 - 人工处理", "default", workflow_id, "reject", alert.event_id))

    elements = [
        *_alert_header_elements(alert),
        {"tag": "hr"},
        ai_section,
        {"tag": "hr"},
        trace_section,
    ]
    if actions:
        elements += [{"tag": "hr"}, {"tag": "action", "actions": actions}]

    return {
        "header": {...},
        "elements": elements,
    }
```

- [ ] **Step 4: 改 `send_feishu_alert_with_agent` 接收 policy_result_json**

```python
@activity.defn
async def send_feishu_alert_with_agent(
    alert_json: str, workflow_id: str, agent_output_json: str, policy_result_json: str = "{}",
) -> str:
    alert = Alert.model_validate_json(alert_json)
    agent_output = json.loads(agent_output_json)
    plan = agent_output.get("plan") or {}
    trace = agent_output.get("trace") or []
    policy = json.loads(policy_result_json) if policy_result_json else {}
    card = build_feishu_card_with_agent(alert, workflow_id, plan, trace, policy)
    return await _post_im_message(msg_type="interactive", content=card)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_feishu.py -v
```

- [ ] **Step 6: 提交**

```bash
git add src/activities/feishu.py tests/test_feishu.py
git commit -m "feat(policy): show policy decision label and AUTO/SHADOW/DENY states on feishu card"
```

---

## Task 10: main.py 注册 + .env.example + 文档

**Files:**
- Modify: `src/main.py`
- Modify: `.env.example`
- Create: `docs/policy-mode.md`
- Modify: `docs/optional-improvements.md`

- [ ] **Step 1: main.py 注册 evaluate_policy_activity**

`src/main.py` 修改：

```python
with workflow.unsafe.imports_passed_through():
    from src.activities.audit import write_audit
    from src.activities.feishu import send_feishu_alert, send_feishu_alert_with_agent, send_feishu_result
    from src.activities.llm import agent_diagnose
    from src.activities.policy import evaluate_policy_activity   # 新增
    from src.activities.runbook import execute_runbook
    from src.llm import create_llm_router
    from src.workflows.alert_workflow import AlertWorkflow
```

worker 的 activities 列表加：

```python
    worker = Worker(
        temporal_client,
        task_queue=settings.temporal_task_queue,
        workflows=[AlertWorkflow],
        activities=[
            send_feishu_alert, send_feishu_alert_with_agent, send_feishu_result,
            execute_runbook, write_audit,
            agent_diagnose,
            evaluate_policy_activity,   # 新增
        ],
    )
```

- [ ] **Step 2: .env.example 加新配置**

`.env.example` 末尾追加：

```bash
# AIOps 执行模式
# live:   按 policy 决策自动执行（默认）
# shadow: 所有 ALLOW 决策只记录不真自动执行（调试新规则用）
AIOPS_MODE=live

# 主机分级（逗号分隔的 IP）
# VM3 (192.168.198.130) 是测试机，留空使其归 dev；不在两者中的主机也默认归 dev
PRODUCTION_HOSTS=
STAGING_HOSTS=

# Policy 配置文件路径（容器内绝对路径）
POLICY_CONFIG_PATH=/app/src/policy/policies.yaml
```

- [ ] **Step 3: 写 docs/policy-mode.md**

新建 `docs/policy-mode.md`：

````markdown
# Policy 层与 Shadow Mode 操作手册

> 配套架构文档：`docs/生产级 AIOps 架构设计.md` §9.1 + §8

## 一图速览

```
告警 → agent_diagnose → resolve_runbook → evaluate_policy → 三分支
                                                            ├─ DENY:           飞书通知拒绝 + 写审计
                                                            ├─ ALLOW + live:   直接执行 + 事后通知
                                                            └─ APPROVAL_REQUIRED 或 ALLOW + shadow:
                                                                                飞书审批卡片 → 等待 → 执行
```

## 模式开关

`.env` 里：

```bash
AIOPS_MODE=shadow   # 推荐刚部署时
AIOPS_MODE=live     # Shadow 跑稳后切换
```

切换 live 之前，按架构文档 §8 要求至少满足：

- Shadow 跑 ≥ 2 周
- Agent 决策准确率 ≥ 90%
- 误判率 < 5%
- 覆盖典型故障类型 ≥ 10 种

切完后**仍要观察**，按 §8.3 灰度策略推：先 10% 自动 → 100% 自动 → 中风险带审批 → 高风险永久人工。

## 主机分级

`.env` 里：

```bash
PRODUCTION_HOSTS=192.168.198.130,192.168.198.131
STAGING_HOSTS=192.168.198.140
# 其它主机自动归 dev
```

policy 规则里就能用：

```yaml
- name: production_requires_approval
  effect: require_approval
  conditions:
    - host_tier: production
```

## 添加新规则

编辑 `src/policy/policies.yaml`，**重启容器**生效：

```bash
sudo docker compose -f ./docker-compose.yml restart aiops
```

### 字段速查

| ctx 字段 | 含义 |
|---|---|
| `runbook_id` | `disk_cleanup` / `service_restart` |
| `params.path` | disk_cleanup 的清理路径 |
| `params.service_name` | service_restart 的服务名 |
| `params.target_host` | 目标 IP |
| `risk_level` | agent 评估的风险 `low` / `medium` / `high` |
| `confidence` | agent 置信度 0.0~1.0 |
| `host_tier` | `production` / `staging` / `dev` |
| `host_ip` | 同 `params.target_host` |
| `alert.severity` | 告警级别 |

### 操作符速查

| op | 用法 | 例子 |
|---|---|---|
| `eq`（默认） | `field: value` | `risk_level: low` |
| `ne` | `field: { ne: value }` | `host_tier: { ne: production }` |
| `in` | `field: { in: [...] }` | `params.path: { in: ["/tmp", "/var/log"] }` |
| `not_in` | `field: { not_in: [...] }` | |
| `gte` / `lte` | `field: { gte: 0.85 }` | `confidence: { gte: 0.85 }` |
| `gt` / `lt` | `field: { lt: 0.5 }` | |

## 排错

### 一条告警预期自动执行但实际走了人工审批

1. 看飞书卡片底部的 **Policy** 行，里面会写 `规则 default：默认审批` 这种
2. 没命中任何 allow 规则 → 检查 conditions 是否完全匹配
3. 命中了 require_approval / deny → 那是设计如此

### 怎么测试一条规则不发真告警

```bash
sudo docker compose -f ./docker-compose.yml exec aiops python -c "
from src.policy.engine import evaluate_policy
print(evaluate_policy(
    runbook_id='disk_cleanup',
    params={'target_host': '192.168.198.130', 'path': '/tmp'},
    alert={'severity': 'high'},
    plan={'risk_level': 'low', 'confidence': 0.9},
).model_dump_json())
"
```

### YAML 写错了

容器启动不会报错（lazy load），但**第一条告警**会被 activity 降级为 APPROVAL_REQUIRED 并在日志里打 `policy evaluation failed`。修完 yaml 后下一条告警就好。
````

- [ ] **Step 4: 更新 optional-improvements.md**

把 `docs/optional-improvements.md` 里 Phase 3 相关的 TODO 标完成（如果有的话），或者加一段：

```markdown
## ✅ 已完成（Phase 3）

- Policy 规则引擎 + YAML 配置：见 [docs/policy-mode.md](policy-mode.md)
- Shadow / Live 模式开关
- 飞书卡片支持 AUTO / DENY / SHADOW 状态
```

- [ ] **Step 5: 跑全套测试确保没破坏**

```bash
pytest -q
```

预期：全 PASS

- [ ] **Step 6: 提交**

```bash
git add src/main.py .env.example docs/policy-mode.md docs/optional-improvements.md
git commit -m "docs(policy): operations manual + register evaluate_policy activity"
```

---

## Task 11: 端到端验证（手动）

**Files:** 无代码修改，纯手动测试

**目标**：覆盖所有三种 policy 决策路径（ALLOW / APPROVAL_REQUIRED / DENY）以及 shadow 模式切换。每条路径有可见的、对应飞书卡片差异的输出，让用户直观看到 policy 工作正常。

- [ ] **Step 1: 同步代码到 VM1**

scp / rsync 以下文件到 VM1 的 `~/anq-aiops/`：
- `src/policy/` 整个目录（新增）
- `src/models.py`、`src/config.py`、`src/workflows/alert_workflow.py`、`src/activities/feishu.py`、`src/activities/policy.py`（新增）、`src/main.py`
- `pyproject.toml`、`.env.example`

- [ ] **Step 2: VM1 上更新 .env**

```bash
cd ~/anq-aiops
cat >> .env <<'EOF'

AIOPS_MODE=live
PRODUCTION_HOSTS=
STAGING_HOSTS=
POLICY_CONFIG_PATH=/app/src/policy/policies.yaml
EOF
```

- [ ] **Step 3: 重建镜像**

```bash
# pyproject.toml 改了（加了 pyyaml），必须 build 重装依赖
sudo docker compose -f ./docker-compose.yml build aiops
sudo docker compose -f ./docker-compose.yml up -d --force-recreate aiops

# 等服务起来
sleep 5
sudo docker compose -f ./docker-compose.yml logs aiops --tail 20 | grep -i "Started\|listener\|Uvicorn"
```

- [ ] **Step 4: 离线验证 policy 引擎（不发真告警）**

跑三个调试命令，分别验证 ALLOW / DENY / APPROVAL_REQUIRED 三种路径。

**4a. ALLOW 路径**（dev 主机 + /tmp + low risk + 高 confidence）：

```bash
sudo docker compose -f ./docker-compose.yml exec aiops python -c "
from src.policy.engine import evaluate_policy
r = evaluate_policy(
    runbook_id='disk_cleanup',
    params={'target_host': '192.168.198.130', 'path': '/tmp'},
    alert={'severity': 'high'},
    plan={'risk_level': 'low', 'confidence': 0.95},
)
print(r.model_dump_json(indent=2))
"
```

预期：
```json
{
  "decision": "allow",
  "matched_policy": "low_risk_disk_cleanup",
  "reason": "临时目录/日志/缓存清理 + 低风险 + 高置信度 → 自动"
}
```

**4b. DENY 路径**（清根目录）：

```bash
sudo docker compose -f ./docker-compose.yml exec aiops python -c "
from src.policy.engine import evaluate_policy
r = evaluate_policy(
    runbook_id='disk_cleanup',
    params={'target_host': '192.168.198.130', 'path': '/etc'},
    alert={},
    plan={'risk_level': 'low', 'confidence': 0.99},
)
print(r.model_dump_json(indent=2))
"
```

预期：
```json
{
  "decision": "deny",
  "matched_policy": "deny_root_path_cleanup",
  "reason": "禁止清理根目录或系统关键路径"
}
```

**4c. APPROVAL_REQUIRED 路径**（confidence 不够）：

```bash
sudo docker compose -f ./docker-compose.yml exec aiops python -c "
from src.policy.engine import evaluate_policy
r = evaluate_policy(
    runbook_id='disk_cleanup',
    params={'target_host': '192.168.198.130', 'path': '/tmp'},
    alert={},
    plan={'risk_level': 'low', 'confidence': 0.7},
)
print(r.model_dump_json(indent=2))
"
```

预期：
```json
{
  "decision": "approval_required",
  "matched_policy": "low_confidence_requires_approval",
  "reason": "agent 置信度 < 0.9 转人工"
}
```

- [ ] **Step 5: 端到端真告警 - ALLOW 自动执行**

```bash
# VM3 上制造磁盘满
ssh lijianqiao@192.168.198.130 -i /opt/aiops/ssh-keys/id_ed25519 \
    "sudo bash /opt/demo-scripts/fill-disk.sh 3500"
```

预期飞书行为（live 模式 + ALLOW）：

1. 第一条卡片：包含 🤖 AI 诊断 + 🔍 诊断步骤 + **Policy: 🤖 自动执行（规则 `low_risk_disk_cleanup`）** + **没有审批按钮**
2. 几秒到几十秒后第二条文本：**"🤖 自动 告警 X 处理成功（Runbook: disk_cleanup）"**
3. VM3 上 `df -h /` 使用率回落

如果 agent 给的 confidence < 0.9 → 会变成 approval_required 路径，那是符合 policy 的正常表现。

- [ ] **Step 6: 端到端真告警 - DENY 拦截**

构造一条会让 agent 选 `path=/etc` 的告警很难（agent 不会这么做）——所以这一步用**直接 webhook 注入**绕过 agent，伪造一条 alert，让 workflow 走到 policy 评估时 path 是 /etc。

这步**用代码直接验证 workflow 的 deny 分支**（绕开 agent，让 workflow 确实跑到 policy_decision == "deny" 那条路）：

```bash
sudo docker compose -f ./docker-compose.yml exec aiops python <<'PY'
import asyncio
import json
from temporalio.client import Client
from src.config import settings
from src.workflows.alert_workflow import AlertWorkflow

async def main():
    c = await Client.connect(settings.temporal_address)
    # 用一个 event_name 让 fallback 关键词匹配选 disk_cleanup
    # 用 message 让 _resolve_runbook 抽出 path=/etc
    # 这样 agent 即使失败，runbook_params 也是 /etc → policy DENY
    alert_json = json.dumps({
        "event_id": f"deny-test-{int(__import__('time').time())}",
        "event_name": "Disk usage > 90%",
        "severity": "high",
        "hostname": "aiops-target",
        "host_ip": "192.168.198.130",
        "trigger_id": "deny-1",
        "message": "Disk usage 95% on /etc",  # 关键：path=/etc 触发 deny
        "timestamp": "2026-05-08T10:00:00Z",
        "status": "problem",
    })
    handle = await c.start_workflow(
        AlertWorkflow.run, alert_json,
        id=f"alert-deny-test-{int(__import__('time').time())}",
        task_queue=settings.temporal_task_queue,
    )
    print("workflow result:", await handle.result())

asyncio.run(main())
PY
```

预期：
- 终端打印 `workflow result: denied`
- 飞书收到一条文本：**"🚫 告警 X 被 Policy 拒绝执行（规则 `deny_root_path_cleanup`）：禁止清理根目录或系统关键路径"**
- **没有审批卡片**（DENY 直接早返回）
- 审计日志 `decision=denied`

- [ ] **Step 7: 端到端真告警 - APPROVAL_REQUIRED 走人工**

如果 Step 5 跑出来已经是 approval_required（confidence < 0.9），这步可以跳过。否则手工降低置信度——最简单的办法是临时改 `policies.yaml` 把 confidence 阈值调到 `0.99` 让真告警走不到 ALLOW：

```bash
# 临时改阈值
sudo sed -i 's/gte: 0.9$/gte: 0.99/g' src/policy/policies.yaml
sudo docker compose -f ./docker-compose.yml restart aiops

# 触发告警
ssh lijianqiao@192.168.198.130 -i /opt/aiops/ssh-keys/id_ed25519 \
    "sudo bash /opt/demo-scripts/fill-disk.sh 3500"
```

预期飞书行为：
- 卡片 **Policy** 行显示：`👤 需要人工审批（规则 `low_confidence_requires_approval` 或 `default`）`
- **有审批按钮**
- 点"按建议执行" → 才执行

测完恢复阈值：
```bash
sudo sed -i 's/gte: 0.99$/gte: 0.9/g' src/policy/policies.yaml
sudo docker compose -f ./docker-compose.yml restart aiops
```

- [ ] **Step 8: 验证 Shadow 模式（可选，调试用）**

```bash
sed -i 's/^AIOPS_MODE=live/AIOPS_MODE=shadow/' .env
sudo docker compose -f ./docker-compose.yml restart aiops

ssh lijianqiao@192.168.198.130 -i /opt/aiops/ssh-keys/id_ed25519 \
    "sudo rm -f /tmp/aiops-test-fill && sudo bash /opt/demo-scripts/fill-disk.sh 3500"
```

预期：本来 ALLOW 的卡片现在标 **`🌓 Shadow 模式：本应自动执行（规则 `low_risk_disk_cleanup`）但仍需审批`**，并且**有审批按钮**。

测完切回 live：
```bash
sed -i 's/^AIOPS_MODE=shadow/AIOPS_MODE=live/' .env
sudo docker compose -f ./docker-compose.yml restart aiops
```

- [ ] **Step 9: 清场**

```bash
ssh lijianqiao@192.168.198.130 -i /opt/aiops/ssh-keys/id_ed25519 \
    "sudo rm -f /tmp/aiops-test-fill && df -h /"
```

---

## 用户已确认决策

| # | 问题 | 决策 |
|---|---|---|
| 1 | VM3 (192.168.198.130) 的 tier | **dev**（测试机；`PRODUCTION_HOSTS` 留空使其归 dev） |
| 2 | 服务白名单 | `nginx / redis-server`（apache2 / docker / sshd 等暂不开放，需要时单独审批） |
| 3 | 数据库黑名单 | 默认 `mysql / mariadb / mysqld / postgresql / postgres / mongodb / redis` |
| 4 | 磁盘 path 白名单 | `/tmp / /var/tmp / /var/log / /var/cache / /opt/cache` |
| 5 | 置信度阈值 | `0.9`（require_approval 的下限和 allow 的下限统一用 0.9） |
| 6 | Shadow Mode | **默认 live**（直接进入自动执行模式）；shadow 保留作为调试新规则用 |

这些决策已经写进 Task 5（policies.yaml）、Task 2（aiops_mode 默认值）、Task 10（.env.example）。Task 11 端到端验证扩展为 9 步，覆盖三种决策路径 + shadow 切换，让用户能直观看到 policy 工作正常（满足"需要有测试效果"的要求）。

---

## Done definition

- [ ] 所有 Task 1-10 测试通过
- [ ] `pytest -q` 全过
- [ ] Task 11 端到端验证 6 步全过
- [ ] `docs/policy-mode.md` 写完
- [ ] PR 提交
