"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: sop_generator.py
@DateTime: 2026-05-08 23:50:00
@Docs: 根据 Hermes 历史成功案例生成 SOP Markdown 候选
"""

import logging
import re
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
from temporalio import activity

import src.activities.llm as llm_activities
from src.hermes.repository import AuditRepository
from src.sop.skill_template import SkillCandidate, render_skill_md

logger = logging.getLogger(__name__)
_repo: AuditRepository | None = None
_MAX_CANDIDATES_PER_DAY = 5
SOP_ROOT = Path("skills")
_SAFE_SLUG = re.compile(r"[^a-z0-9_-]+")


class _SkillOut(BaseModel):
    """LLM 生成的 SOP 候选输出。"""

    name: str
    description: str
    category: str = "devops"
    when_to_use: list[str] = []
    quick_ref: list[str] = []
    procedure: list[str] = []
    pitfalls: list[str] = []
    verification: list[str] = []
    triggers: dict[str, list[str]] | None = None


def set_repo(repo: AuditRepository | None) -> None:
    """设置 Hermes repository；None 表示禁用 SOP 生成。"""
    global _repo
    _repo = repo


async def _fetch_candidates() -> list[dict[str, Any]]:
    """从 sop_candidates 视图读取待生成候选。"""
    if _repo is None:
        return []
    async with _repo.pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT host_ip, runbook_id, event_keyword, success_count, sample_ids
            FROM "{_repo.schema}".sop_candidates
            ORDER BY success_count DESC
            LIMIT $1
            """,
            _MAX_CANDIDATES_PER_DAY,
        )
    return [dict(row) for row in rows]


async def _generate_skill_with_llm(
    host_ip: str,
    runbook_id: str,
    event_keyword: str,
    samples: list[dict[str, Any]],
) -> SkillCandidate:
    """调用 LLM 生成 SOP 候选。"""
    if llm_activities.llm_router is None:
        raise RuntimeError("LLM Router 未初始化，无法生成 SOP")

    samples_text = "\n".join(
        f"- {sample.get('event_name', '')}: {sample.get('agent_reasoning') or '无'}" for sample in samples[:5]
    )
    prompt = f"""根据以下 AIOps 历史成功案例，生成一个标准操作流程 SOP，按 Hermes SKILL.md 五段式结构。

主机 IP: {host_ip}
告警关键词: {event_keyword}
Runbook: {runbook_id}
成功样本:
{samples_text}

返回 JSON，字段为 name、description、category、when_to_use、quick_ref、procedure、pitfalls、verification、triggers。
triggers 是一个 dict，包含：
- event_patterns: 用于匹配告警的关键词/正则列表（从 event_keyword 和样本中提炼）
- severity: 匹配的严重级别列表（如 ["high", "disaster"]，根据案例推断）
"""
    result = cast(_SkillOut, await llm_activities.llm_router.invoke(prompt, _SkillOut))
    return SkillCandidate(**result.model_dump())


@activity.defn
async def generate_sop_candidates() -> list[str]:
    """生成 SOP Markdown 候选文件，返回写入路径列表。"""
    if _repo is None:
        return []

    candidates = await _fetch_candidates()
    written: list[str] = []
    for candidate in candidates:
        try:
            sample_ids = list(candidate["sample_ids"])[:5]
            async with _repo.pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT event_name, agent_reasoning
                    FROM "{_repo.schema}".audit_records
                    WHERE id = ANY($1::bigint[])
                    """,
                    sample_ids,
                )
            skill = await _generate_skill_with_llm(
                str(candidate["host_ip"]),
                str(candidate["runbook_id"]),
                str(candidate["event_keyword"]),
                [dict(row) for row in rows],
            )
            # 确保 triggers 始终有值，LLM 未返回时用 event_keyword 兜底
            if not skill.triggers:
                skill.triggers = {"event_patterns": [str(candidate["event_keyword"])]}
            markdown = render_skill_md(skill, sample_count=int(candidate["success_count"]))
            category = _slugify(skill.category, fallback="devops")
            name = _slugify(skill.name, fallback="sop")
            output_dir = SOP_ROOT / category
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{name}.md"
            path.write_text(markdown, encoding="utf-8")
            written.append(str(path))
            logger.info(f"SOP 候选已写入：{path}")
        except Exception as exc:
            logger.warning(f"SOP 候选生成失败：{candidate}，原因：{exc}")
    return written


def _slugify(value: str, fallback: str) -> str:
    """把 LLM 输出限制为安全文件名片段。"""
    cleaned = _SAFE_SLUG.sub("-", value.strip().lower()).strip("-_")
    return cleaned or fallback
