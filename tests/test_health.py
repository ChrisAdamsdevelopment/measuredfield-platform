"""Focused HTTP tests for public health/readiness endpoints."""
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from app.main import app


def test_healthz_is_public_and_database_free():
    with patch("app.main.get_engine") as engine:
        for host in ("testserver", "customer.example"):
            response = TestClient(app).get("/healthz", headers={"Host": host})
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
        engine.assert_not_called()


def test_readyz_checks_database_but_not_rls_catalog_on_each_request():
    connection = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    with patch("app.main.get_engine", return_value=engine), patch(
        "app.main.isolation_self_check", side_effect=AssertionError("per-request catalog check")
    ) as check:
        response = TestClient(app).get("/readyz", headers={"Host": "testserver"})
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
        assert "schema" not in response.text
        assert "isolation" not in response.text
        assert connection.execute.call_count == 1
        sql = str(connection.execute.call_args.args[0])
        assert "SELECT 1" in sql
        check.assert_not_called()


def test_readyz_database_unavailable():
    with patch("app.main.get_engine", side_effect=RuntimeError("private details")):
        response = TestClient(app).get("/readyz", headers={"Host": "other-domain.test"})
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert "private details" not in response.text
