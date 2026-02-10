import pytest


def test_settings_guardrail_prod_rejects_default_jwt_secret() -> None:
    from app.config import Settings

    with pytest.raises(RuntimeError, match=r"JWT_SECRET"):
        Settings(
            ENV="prod",
            JWT_SECRET=Settings.DEFAULT_JWT_SECRET,
            ADMIN_TOKEN="some-non-default-admin-token",
        )


def test_settings_guardrail_prod_rejects_default_admin_token() -> None:
    from app.config import Settings

    with pytest.raises(RuntimeError, match=r"ADMIN_TOKEN"):
        Settings(
            ENV="prod",
            JWT_SECRET="some-non-default-jwt-secret-32chars-min",
            ADMIN_TOKEN=Settings.DEFAULT_ADMIN_TOKEN,
        )


def test_settings_guardrail_dev_allows_default_secrets() -> None:
    from app.config import Settings

    Settings(
        ENV="dev",
        JWT_SECRET=Settings.DEFAULT_JWT_SECRET,
        ADMIN_TOKEN=Settings.DEFAULT_ADMIN_TOKEN,
    )


def test_settings_guardrail_test_allows_default_secrets() -> None:
    from app.config import Settings

    Settings(
        ENV="test",
        JWT_SECRET=Settings.DEFAULT_JWT_SECRET,
        ADMIN_TOKEN=Settings.DEFAULT_ADMIN_TOKEN,
    )

