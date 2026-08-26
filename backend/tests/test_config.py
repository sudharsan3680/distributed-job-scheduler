import pytest

from app.config import Settings, _INSECURE_DEFAULT_JWT_SECRET


def test_development_allows_default_secret():
    s = Settings(environment="development")
    assert s.jwt_secret == _INSECURE_DEFAULT_JWT_SECRET


def test_production_rejects_default_secret():
    with pytest.raises(ValueError, match="placeholder default"):
        Settings(environment="production")


def test_production_rejects_short_secret():
    with pytest.raises(ValueError, match="at least 32 characters"):
        Settings(environment="production", jwt_secret="short")


def test_production_accepts_strong_secret():
    s = Settings(environment="production", jwt_secret="x" * 32)
    assert s.environment == "production"
