"""Tests for main.py — CLI parsing for reporte-mensual and wiring."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("MICROSIP_API_KEY", "test-dummy-key")
os.environ.setdefault("GCP_PROJECT_ID", "test-dummy-project")

import main as main_mod
from main import _default_mes, _parse_args, _parse_mes


class TestReporteArgs:
    def test_defaults(self):
        args = _parse_args(["reporte-mensual"])
        assert args.target == "reporte-mensual"
        assert args.mes is None  # resolved to previous month at run time
        assert args.corte == "formato"
        assert args.salida == "reports"
        assert args.sin_correo is False

    def test_default_mes_is_previous_month(self):
        assert _default_mes(date(2026, 9, 2)) == date(2026, 8, 1)
        assert _default_mes(date(2026, 1, 15)) == date(2025, 12, 1)

    def test_explicit_args(self):
        args = _parse_args(["reporte-mensual", "--mes", "2026-07", "--corte", "zona",
                            "--salida", "/tmp/x", "--sin-correo"])
        assert args.mes == date(2026, 7, 1)
        assert args.corte == "zona"
        assert args.salida == "/tmp/x"
        assert args.sin_correo is True

    def test_parse_mes(self):
        assert _parse_mes("2026-02") == date(2026, 2, 1)
        with pytest.raises(Exception):
            _parse_mes("2026-13")

    def test_bad_corte_rejected(self):
        with pytest.raises(SystemExit):
            _parse_args(["reporte-mensual", "--corte", "cliente"])

    def test_bad_mes_rejected(self):
        with pytest.raises(SystemExit):
            _parse_args(["reporte-mensual", "--mes", "agosto"])


class TestReporteRun:
    def _settings(self, **overrides):
        s = MagicMock()
        s.log_level = "INFO"
        s.gcp_project_id = "proj"
        s.bq_dataset = "ds"
        s.bq_location = "us"
        s.microsip_empresa = "ALMACENES PACHECO"
        s.smtp_configured = False
        s.smtp_host = ""
        s.smtp_port = 587
        s.smtp_user = ""
        s.smtp_password = ""
        s.smtp_from = ""
        s.reporte_iscam_to_list = []
        for k, v in overrides.items():
            setattr(s, k, v)
        return s

    def test_runs_report_without_api_client_and_skips_mail(self, tmp_path):
        settings = self._settings()
        with patch.object(main_mod, "settings", settings), \
             patch.object(main_mod, "BigQueryLoader") as loader_cls, \
             patch.object(main_mod, "MicrosipClient") as api_cls, \
             patch.object(main_mod, "generar_reporte", return_value=tmp_path / "r.xlsx") as gen, \
             patch.object(main_mod, "enviar_por_correo") as mail, \
             patch.object(main_mod, "_default_mes", return_value=date(2026, 8, 1)):
            rc = main_mod.main(["reporte-mensual", "--salida", str(tmp_path)])

        assert rc == 0
        api_cls.assert_not_called()
        loader = loader_cls.return_value.__enter__.return_value
        gen.assert_called_once()
        assert gen.call_args.args[:3] == (loader, "ds", date(2026, 8, 1))
        assert gen.call_args.kwargs["corte"] == "formato"
        assert gen.call_args.kwargs["empresa"] == "ALMACENES PACHECO"
        assert str(gen.call_args.kwargs["salida"]) == str(tmp_path)
        mail.assert_not_called()

    def test_sends_mail_when_configured(self, tmp_path):
        settings = self._settings(
            smtp_configured=True, smtp_host="mail", smtp_from="etl@x.mx",
            smtp_user="u", smtp_password="p", reporte_iscam_to_list=["a@x.mx"],
        )
        with patch.object(main_mod, "settings", settings), \
             patch.object(main_mod, "BigQueryLoader"), \
             patch.object(main_mod, "generar_reporte", return_value=tmp_path / "r.xlsx"), \
             patch.object(main_mod, "enviar_por_correo") as mail:
            rc = main_mod.main(["reporte-mensual", "--mes", "2026-07"])
        assert rc == 0
        mail.assert_called_once()
        assert mail.call_args.args == (tmp_path / "r.xlsx", ["a@x.mx"])
        assert mail.call_args.kwargs["host"] == "mail"
        assert mail.call_args.kwargs["password"] == "p"
        assert "2026-07" in mail.call_args.kwargs["subject"]

    def test_sin_correo_flag_wins(self, tmp_path):
        settings = self._settings(smtp_configured=True, smtp_host="mail", smtp_from="f",
                                  reporte_iscam_to_list=["a@x.mx"])
        with patch.object(main_mod, "settings", settings), \
             patch.object(main_mod, "BigQueryLoader"), \
             patch.object(main_mod, "generar_reporte", return_value=tmp_path / "r.xlsx"), \
             patch.object(main_mod, "enviar_por_correo") as mail:
            rc = main_mod.main(["reporte-mensual", "--sin-correo"])
        assert rc == 0
        mail.assert_not_called()

    def test_query_failure_returns_1(self):
        from google.api_core.exceptions import GoogleAPIError

        with patch.object(main_mod, "settings", self._settings()), \
             patch.object(main_mod, "BigQueryLoader"), \
             patch.object(main_mod, "generar_reporte", side_effect=GoogleAPIError("boom")):
            assert main_mod.main(["reporte-mensual"]) == 1
