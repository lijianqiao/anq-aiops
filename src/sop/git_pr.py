"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: git_pr.py
@DateTime: 2026-05-08 23:49:00
@Docs: 提供 SOP Markdown 自动提交并创建 GitHub PR 的封装
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)
_COMMAND_TIMEOUT = 60


def open_sop_pr(file_paths: list[str], branch_suffix: str, repo_dir: str = "/app") -> str | None:
    """将 SOP 文件提交到新分支并创建 PR；失败返回 None。"""
    if not file_paths:
        return None

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.warning("未配置 GITHUB_TOKEN，跳过 SOP PR 创建")
        return None

    branch = f"sop/auto-{branch_suffix}"
    env = {**os.environ, "GH_TOKEN": token}
    try:
        _run(["git", "config", "user.email", "aiops-bot@local"], repo_dir, env)
        _run(["git", "config", "user.name", "aiops-bot"], repo_dir, env)
        _run(["git", "checkout", "-B", branch], repo_dir, env)
        _run(["git", "add", *file_paths], repo_dir, env)
        status = _run(["git", "status", "--short", "--", *file_paths], repo_dir, env)
        if not status.stdout.strip():
            logger.info("SOP 文件没有变化，跳过 PR 创建")
            return None
        _run(["git", "commit", "-m", f"chore(sop): auto-generate SOPs {branch_suffix}"], repo_dir, env)
        _run(["git", "push", "-u", "origin", branch], repo_dir, env)
        body = "自动生成的 SOP，请运维 review：\n\n" + "\n".join(f"- `{path}`" for path in file_paths)
        result = _run(
            ["gh", "pr", "create", "--title", f"auto-SOP {branch_suffix}", "--body", body],
            repo_dir,
            env,
        )
        url = result.stdout.strip()
        logger.info(f"SOP PR 已创建：{url}")
        return url
    except subprocess.CalledProcessError as exc:
        logger.warning(f"SOP PR 创建失败：{exc.stderr or exc}")
        return None


def _run(args: list[str], cwd: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """运行 git/gh 命令。"""
    return subprocess.run(args, cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT)
