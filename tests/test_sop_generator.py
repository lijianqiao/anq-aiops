"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_sop_generator.py
@DateTime: 2026-05-08 23:52:00
@Docs: 测试 SOP 模板渲染和 Git PR 封装降级
"""

import pytest

from src.activities.sop_generator import _slugify
from src.sop.git_pr import open_sop_pr
from src.sop.skill_template import SkillCandidate, render_skill_md


def test_render_skill_md_includes_all_sections() -> None:
    """SOP 模板应包含 Hermes 风格五段式结构。"""
    candidate = SkillCandidate(
        name="disk-cleanup-test",
        description="磁盘清理测试 SOP",
        category="disk",
        when_to_use=["磁盘使用率超过 90%"],
        quick_ref=["df -h"],
        procedure=["确认挂载点", "执行 disk_cleanup"],
        pitfalls=["不要清理根目录"],
        verification=["磁盘使用率低于 80%"],
    )

    markdown = render_skill_md(candidate, sample_count=3)

    for section in ["When to use", "Quick reference", "Procedure", "Pitfalls", "Verification"]:
        assert section in markdown
    assert "auto_generated: true" in markdown
    assert "sample_count: 3" in markdown


def test_open_sop_pr_skips_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置 GITHUB_TOKEN 时应跳过 PR 创建。"""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert open_sop_pr(["docs/sop/test.md"], "20260508") is None


def test_sop_slugify_blocks_path_escape() -> None:
    """LLM 输出文件名不应逃逸 docs/sop。"""
    assert _slugify("../../etc/passwd", "sop") == "etc-passwd"
    assert _slugify("Disk Cleanup!", "sop") == "disk-cleanup"
