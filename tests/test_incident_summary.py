"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_incident_summary.py
@DateTime: 2026-05-08 23:47:00
@Docs: 测试故障简报渲染和失败标记
"""

from src.activities.incident_summary import _render_summary


def test_generate_summary_renders_markdown() -> None:
    """成功处置简报应包含关键字段和成功标记。"""
    markdown = _render_summary(
        event_id="e1",
        decision="auto_approved",
        runbook_id="disk_cleanup",
        verify=True,
        host_ip="1.1.1.1",
        event_name="Disk full",
        agent_reasoning="清理 /tmp 释放 5GB",
        llm_summary="本次磁盘满故障由 agent 自动诊断并清理 /tmp，影响较小。",
    )

    assert "✅" in markdown
    assert "1.1.1.1" in markdown
    assert "disk_cleanup" in markdown
    assert "本次磁盘满故障" in markdown


def test_summary_includes_failure_marker_for_failed_runs() -> None:
    """验证失败的简报应带失败标记。"""
    markdown = _render_summary(
        event_id="e1",
        decision="approved",
        runbook_id="disk_cleanup",
        verify=False,
        host_ip="1.1.1.1",
        event_name="Disk full",
        agent_reasoning="清理后仍高水位",
        llm_summary="清理后磁盘仍 91%，需人工介入。",
    )

    assert "❌" in markdown
    assert "未通过" in markdown
