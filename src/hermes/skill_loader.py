"""Skills 加载器：扫描 skills/ 目录，按 triggers 匹配告警，注入 Agent prompt。

Skills 是 SOP 的运行时形态：
  - 存储在 skills/ 目录，按 category 子目录组织
  - YAML frontmatter 包含 triggers 字段（event_patterns / host_groups / severity）
  - 匹配逻辑：event_patterns 用 regex 匹配 alert.event_name + alert.message，
    severity 匹配 alert.severity，三者 AND 关系（缺省字段视为匹配）。
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_ROOT = Path("skills")


@dataclass
class Skill:
    """一个已加载的 Skill。"""

    name: str
    description: str
    triggers: dict[str, list[str]]
    body: str  # frontmatter 之后的 markdown 正文


@dataclass
class _TriggerPatterns:
    """预编译的触发条件。"""

    event_patterns: list[re.Pattern[str]] = field(default_factory=list)
    host_groups: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)


def load_skills(root: Path = SKILLS_ROOT) -> list[Skill]:
    """扫描 skills/ 目录，解析所有 .md 文件的 frontmatter + body。"""
    if not root.is_dir():
        return []

    skills: list[Skill] = []
    for md_path in root.rglob("*.md"):
        try:
            text = md_path.read_text(encoding="utf-8")
            skill = _parse_skill_md(text, md_path)
            if skill is not None:
                skills.append(skill)
        except Exception as exc:
            logger.warning(f"Failed to load skill {md_path}: {exc}")
    return skills


def match_skills(alert: dict, skills: list[Skill]) -> list[Skill]:
    """返回与告警匹配的 Skills 列表（按 triggers 条件 AND 匹配）。"""
    if not skills:
        return []

    event_text = f"{alert.get('event_name', '')} {alert.get('message', '')}".lower()
    severity = str(alert.get("severity", "")).lower()
    host_ip = str(alert.get("host_ip", "")).lower()

    matched: list[Skill] = []
    for skill in skills:
        triggers = skill.triggers
        if _triggers_match(triggers, event_text, severity, host_ip):
            matched.append(skill)
    return matched


def format_skills_for_prompt(skills: list[Skill]) -> str:
    """将匹配的 Skills 渲染为可注入 Agent system prompt 的文本。"""
    if not skills:
        return ""

    sections: list[str] = []
    for skill in skills:
        header = f"### {skill.name}"
        if skill.description:
            header += f"\n{skill.description}"
        sections.append(f"{header}\n\n{skill.body.strip()}")

    return "\n\n---\n\n".join(sections)


# ---------- internal ----------


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_md(text: str, path: Path) -> Skill | None:
    """解析一个 skill markdown 文件，返回 Skill 或 None（格式不对时跳过）。"""
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return None

    frontmatter_text = m.group(1)
    body = text[m.end() :]

    # 简易 YAML 解析：只取 name / description / triggers 顶层字段
    # 不引入完整 YAML 解析器，因为 frontmatter 结构简单且固定
    name = _extract_scalar(frontmatter_text, "name") or path.stem
    description = _extract_scalar(frontmatter_text, "description") or ""
    triggers = _extract_triggers(frontmatter_text)

    return Skill(name=name, description=description, triggers=triggers, body=body)


def _extract_scalar(text: str, key: str) -> str | None:
    """从简易 YAML 文本中提取顶层标量值。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            value = stripped[len(key) + 1 :].strip()
            # 去掉引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            return value or None
    return None


def _extract_triggers(text: str) -> dict[str, list[str]]:
    """从 frontmatter 文本中提取 triggers 块（event_patterns / host_groups / severity）。"""
    triggers: dict[str, list[str]] = {}
    in_triggers = False
    current_key: str | None = None

    for line in text.splitlines():
        stripped = line.strip()

        if stripped == "triggers:":
            in_triggers = True
            continue
        if not in_triggers:
            continue

        # 遇到新的顶层 key（非缩进）则退出 triggers 块
        if line and not line.startswith(" ") and not line.startswith("\t") and ":" in line:
            break

        # 子 key（2 空格缩进）
        if stripped.startswith("- "):
            # list item
            if current_key is not None:
                item = stripped[2:].strip().strip("\"'")
                if item:
                    triggers.setdefault(current_key, []).append(item)
        elif ":" in stripped:
            key_part, _, val_part = stripped.partition(":")
            key_part = key_part.strip()
            val_part = val_part.strip()
            current_key = key_part
            if val_part and val_part != "[]":
                # 单行 list 值，如: event_patterns: ["disk", "space"]
                triggers[current_key] = _parse_inline_list(val_part)

    return triggers


def _parse_inline_list(val: str) -> list[str]:
    """解析 YAML 内联列表：["a", "b"] 或 [a, b]。"""
    val = val.strip()
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1]
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip().strip("\"'")]
    return [val] if val else []


def _triggers_match(
    triggers: dict[str, list[str]],
    event_text: str,
    severity: str,
    host_ip: str,
) -> bool:
    """判断 triggers 是否匹配告警。所有指定的条件都必须满足（AND）。"""
    # event_patterns：任一 pattern 匹配即通过
    patterns = triggers.get("event_patterns", [])
    if patterns and not any(re.search(p, event_text, re.IGNORECASE) for p in patterns):
        return False

    # host_groups：任一 glob 匹配 host_ip
    hosts = triggers.get("host_groups", [])
    if hosts:
        import fnmatch

        if not any(fnmatch.fnmatch(host_ip, h.lower()) for h in hosts):
            return False

    # severity：精确匹配
    sevs = triggers.get("severity", [])
    return not (sevs and severity not in [s.lower() for s in sevs])
