"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: test_runbooks.py
@DateTime: 2026-05-08 16:28:00
@Docs: 测试 Runbook 参数、执行封装和验证逻辑
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.models import RunbookResult
from src.runbooks.base import run_ansible
from src.runbooks.disk_cleanup import DiskCleanupParams, DiskCleanupRunbook
from src.runbooks.service_restart import ServiceRestartParams, ServiceRestartRunbook


def test_run_ansible_uses_isolated_private_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每次 runner 执行都应使用干净目录，避免 env/cmdline 的 --check 污染 execute。"""
    source_dir = tmp_path / "ansible"
    source_dir.mkdir()
    (source_dir / "inventory.ini").write_text("target ansible_host=127.0.0.1\n", encoding="utf-8")
    (source_dir / "disk_cleanup.yml").write_text("---\n- hosts: all\n  tasks: []\n", encoding="utf-8")
    env_dir = source_dir / "env"
    env_dir.mkdir()
    (env_dir / "cmdline").write_text("--check", encoding="utf-8")

    called: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> Any:
        called.update(kwargs)
        isolated_dir = Path(kwargs["private_data_dir"])
        assert isolated_dir != source_dir
        assert not (isolated_dir / "env" / "cmdline").exists()
        return SimpleNamespace(
            status="successful",
            stdout=SimpleNamespace(read=lambda: "ok"),
            stderr=SimpleNamespace(read=lambda: ""),
        )

    monkeypatch.setattr(settings, "ansible_private_data_dir", str(source_dir))
    monkeypatch.setattr(settings, "ansible_inventory", str(source_dir / "inventory.ini"))
    monkeypatch.setitem(sys.modules, "ansible_runner", SimpleNamespace(run=fake_run))

    result = run_ansible("disk_cleanup.yml", {"target_host": "target"}, check=False)

    assert result.success is True
    assert called["playbook"] == "disk_cleanup.yml"
    assert called["inventory"] == "inventory.ini"
    assert "cmdline" not in called


class TestDiskCleanupRunbook:
    def test_params_schema(self) -> None:
        rb = DiskCleanupRunbook()
        assert rb.params_schema() is DiskCleanupParams

    def test_params_defaults(self) -> None:
        params = DiskCleanupParams(target_host="10.0.0.1")
        assert params.path == "/tmp"
        assert params.min_age_days == 7

    @patch("src.runbooks.disk_cleanup.run_ansible")
    def test_dry_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(success=True, stdout="would delete 5 files", stderr="", duration_sec=1.0)
        rb = DiskCleanupRunbook()
        params = DiskCleanupParams(target_host="10.0.0.1")
        result = rb.dry_run(params)
        assert result.success
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["check"] is True

    @patch("src.runbooks.disk_cleanup.run_ansible")
    def test_verify(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(success=True, stdout="disk_usage=45", stderr="", duration_sec=0.5)
        rb = DiskCleanupRunbook()
        params = DiskCleanupParams(target_host="10.0.0.1")
        assert rb.verify(params) is True

    @patch("src.runbooks.disk_cleanup.run_ansible")
    def test_verify_parses_ansible_debug_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(
            success=True,
            stdout='ok: [host] => {"msg": "disk_usage=45"}',
            stderr="",
            duration_sec=0.5,
        )
        rb = DiskCleanupRunbook()
        params = DiskCleanupParams(target_host="10.0.0.1")
        assert rb.verify(params) is True


class TestServiceRestartRunbook:
    def test_params_schema(self) -> None:
        rb = ServiceRestartRunbook()
        assert rb.params_schema() is ServiceRestartParams

    @patch("src.runbooks.service_restart.run_ansible")
    def test_dry_run(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(success=True, stdout="service nginx is active", stderr="", duration_sec=0.5)
        rb = ServiceRestartRunbook()
        params = ServiceRestartParams(target_host="10.0.0.1", service_name="nginx")
        result = rb.dry_run(params)
        assert result.success

    @patch("src.runbooks.service_restart.run_ansible")
    def test_verify_active(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(success=True, stdout="service_state=active", stderr="", duration_sec=0.5)
        rb = ServiceRestartRunbook()
        params = ServiceRestartParams(target_host="10.0.0.1", service_name="nginx")
        assert rb.verify(params) is True

    @patch("src.runbooks.service_restart.run_ansible")
    def test_verify_inactive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = RunbookResult(success=True, stdout="service_state=inactive", stderr="", duration_sec=0.5)
        rb = ServiceRestartRunbook()
        params = ServiceRestartParams(target_host="10.0.0.1", service_name="nginx")
        assert rb.verify(params) is False
