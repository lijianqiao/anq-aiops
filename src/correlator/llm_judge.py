"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: llm_judge.py
@DateTime: 2026-05-08 22:45:00
@Docs: 使用 LLM 兜底判断新告警是否属于已有关联组
"""

import logging
from typing import Any, Protocol, cast

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_CACHE_MAX = 128
_CACHE: dict[tuple[str, str], tuple[bool, str]] = {}


class _CacheClearable(Protocol):
    """带 cache_clear 属性的异步判定函数协议。"""

    async def __call__(self, alert_summary: str, group_summary: str) -> tuple[bool, str]:
        """执行关联判断。"""

    def cache_clear(self) -> None:
        """清空缓存。"""


class _JudgeResult(BaseModel):
    """LLM 关联判断结构化输出。"""

    related: bool
    reason: str = ""


def _cache_put(key: tuple[str, str], value: tuple[bool, str]) -> None:
    """写入简单 LRU 缓存。"""
    if len(_CACHE) >= _CACHE_MAX:
        first_key = next(iter(_CACHE))
        del _CACHE[first_key]
    _CACHE[key] = value


def _cache_clear() -> None:
    """清空 LLM 判定缓存。"""
    _CACHE.clear()


async def _call_llm(prompt: str) -> dict[str, Any]:
    """调用 LLM Router，返回关联判断字典。"""
    import src.activities.llm as llm_activities

    if llm_activities.llm_router is None:
        raise RuntimeError("LLM Router 未初始化")

    result = cast(_JudgeResult, await llm_activities.llm_router.invoke(prompt, _JudgeResult))
    return {"related": result.related, "reason": result.reason}


async def llm_judge(alert_summary: str, group_summary: str) -> tuple[bool, str]:
    """判断新告警是否与已有告警组属于同一根因。"""
    cache_key = (alert_summary, group_summary)
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached

    prompt = f"""判断下面新告警是否与已有告警组属于同一根因（30 秒时间窗口内）。

已有告警组：
{group_summary}

新告警：
{alert_summary}

请只返回 JSON：{{"related": true/false, "reason": "中文原因"}}
"""
    try:
        data = await _call_llm(prompt)
        result = (bool(data.get("related")), str(data.get("reason", ""))[:200])
    except Exception as exc:
        logger.warning(f"LLM 关联判断失败，按不关联处理：{exc}")
        result = (False, f"LLM 判断失败，按不关联处理：{exc}")

    _cache_put(cache_key, result)
    return result


llm_judge.cache_clear = _cache_clear  # type: ignore[attr-defined]
