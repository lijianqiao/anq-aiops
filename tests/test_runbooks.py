from unittest.mock import MagicMock, patch

from src.models import RunbookResult
from src.runbooks.disk_cleanup import DiskCleanupParams, DiskCleanupRunbook
from src.runbooks.service_restart import ServiceRestartParams, ServiceRestartRunbook


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
