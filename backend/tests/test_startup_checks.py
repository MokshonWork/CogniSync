"""Regression tests for production startup configuration checks.

Guards against the ImportError class of failure: startup must not import
configured values that do not exist in config.py, and the production guards
(JWT secret strength, DATABASE_URL presence) must actually fire when the
startup event runs.
"""

import pytest

from backend.main import INSECURE_JWT_SECRET, startup_config_checks

STRONG_SECRET = "unit-test-secret-strong-enough-0123456789"


def _prod_env(monkeypatch, **overrides):
    monkeypatch.setenv("RENDER", "1")
    monkeypatch.delenv("COGNISYNC_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    secret = overrides.pop("secret", STRONG_SECRET)
    monkeypatch.setattr("backend.config.JWT_SECRET", secret)
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)
        if value is None:
            monkeypatch.delenv(key, raising=False)


def test_startup_import_mismatch_regression(monkeypatch):
    """startup_config_checks must execute cleanly (catches the ImportError
    from importing DATABASE_URL out of backend.config)."""
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("COGNISYNC_ENV", raising=False)
    startup_config_checks()


def test_production_startup_passes_with_valid_config(monkeypatch):
    _prod_env(monkeypatch, DATABASE_URL="postgresql://u:p@h/db?sslmode=require")
    startup_config_checks()


def test_production_rejects_insecure_jwt_secret(monkeypatch):
    _prod_env(monkeypatch, secret=INSECURE_JWT_SECRET)
    with pytest.raises(RuntimeError, match="COGNISYNC_JWT_SECRET"):
        startup_config_checks()


def test_production_rejects_missing_database_url(monkeypatch):
    _prod_env(monkeypatch)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        startup_config_checks()


def test_dev_uses_sqlite_fallback_with_warning(monkeypatch):
    """Local dev: insecure JWT warns but starts, sqlite fallback allowed."""
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("COGNISYNC_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("backend.config.JWT_SECRET", INSECURE_JWT_SECRET)
    with pytest.warns(UserWarning, match="JWT secret"):
        startup_config_checks()
