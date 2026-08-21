import os

import pytest
from pydantic import ValidationError


_SECURE_NON_DEV_SETTINGS = {
    "JWT_SECRET": "jwt-7f12a6c90de34b58a1c772dc3405f011",
    "ADMIN_TOKEN": "admin-92cbe2405db74a09baf03a9086e110d8",
    "SIMULATOR_SESSION_SECRET": "session-287be5604a1f45fd889014d0016a5c71",
    "SIMULATOR_CSRF_ORIGIN_ALLOWLIST": "https://simulator.example.com",
}


@pytest.fixture(autouse=True)
def _isolate_legacy_environment(monkeypatch) -> None:
    """Let each Settings instance control whether the legacy alias is present."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)


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


def test_default_database_uses_ignored_runtime_root(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    configured = Settings(_env_file=None, ENV="dev")

    assert configured.DATABASE_URL == "sqlite+aiosqlite:///./.local-run/geov0.db"


def test_explicit_legacy_root_database_override_is_preserved(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./geov0.db")
    configured = Settings(_env_file=None, ENV="dev")

    assert configured.DATABASE_URL == "sqlite+aiosqlite:///./geov0.db"


def test_default_database_parent_is_created_on_clean_bootstrap(
    tmp_path, monkeypatch
) -> None:
    from app.config import Settings
    from app.db.session import _ensure_default_sqlite_parent

    monkeypatch.chdir(tmp_path)
    _ensure_default_sqlite_parent(Settings.DEFAULT_SQLITE_DATABASE_URL)

    assert (tmp_path / ".local-run").is_dir()


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("dev", "dev"),
        ("development", "dev"),
        ("test", "test"),
        ("testing", "test"),
        ("stage", "staging"),
        ("staging", "staging"),
        ("prod", "prod"),
        ("production", "prod"),
    ],
)
def test_settings_normalizes_supported_environment_aliases(
    alias: str, canonical: str
) -> None:
    from app.config import Settings

    values = {} if canonical in {"dev", "test"} else _SECURE_NON_DEV_SETTINGS
    configured = Settings(_env_file=None, ENV=alias, **values)

    assert configured.ENV == canonical


@pytest.mark.parametrize("value", ["productionn", "local", "", "   "])
def test_settings_rejects_unknown_or_empty_environment(value: str) -> None:
    from app.config import Settings

    with pytest.raises(ValidationError, match="unsupported environment"):
        Settings(_env_file=None, ENV=value, **_SECURE_NON_DEV_SETTINGS)


def test_settings_requires_explicit_environment(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(RuntimeError, match="ENV must be explicitly set"):
        Settings(_env_file=None, **_SECURE_NON_DEV_SETTINGS)


@pytest.mark.parametrize("misspelled_name", ["env", "ENVIORNMENT"])
def test_misspelled_constructor_environment_cannot_enable_dev_defaults(
    monkeypatch, misspelled_name: str
) -> None:
    from app.config import Settings

    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(RuntimeError, match="ENV must be explicitly set"):
        Settings(_env_file=None, **{misspelled_name: "prod"})


@pytest.mark.skipif(os.name == "nt", reason="Windows environment keys are case-insensitive")
def test_lowercase_process_environment_is_ignored_but_fails_closed(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("env", "prod")

    with pytest.raises(RuntimeError, match="ENV must be explicitly set"):
        Settings(_env_file=None)


def test_legacy_environment_key_is_honored_and_guarded(monkeypatch) -> None:
    from app.config import Settings

    monkeypatch.delenv("ENV", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            JWT_SECRET=Settings.DEFAULT_JWT_SECRET,
            ADMIN_TOKEN="real-admin-token",
            SIMULATOR_SESSION_SECRET="real-session-secret",
            SIMULATOR_CSRF_ORIGIN_ALLOWLIST="https://simulator.example.com",
        )


def test_conflicting_environment_keys_fail_startup() -> None:
    from app.config import Settings

    with pytest.raises(RuntimeError, match="ENV and legacy ENVIRONMENT"):
        Settings(_env_file=None, ENV="dev", ENVIRONMENT="prod")


def test_unrelated_ambient_legacy_environment_does_not_override_canonical_env(
    monkeypatch,
) -> None:
    from app.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "qa")

    configured = Settings(_env_file=None, ENV="prod", **_SECURE_NON_DEV_SETTINGS)

    assert configured.ENV == "prod"
    assert configured.LEGACY_ENVIRONMENT == "qa"


def test_unsupported_explicit_legacy_environment_does_not_override_canonical_env() -> None:
    from app.config import Settings

    configured = Settings(
        _env_file=None,
        ENV="prod",
        ENVIRONMENT="qa",
        **_SECURE_NON_DEV_SETTINGS,
    )

    assert configured.ENV == "prod"
    assert configured.LEGACY_ENVIRONMENT == "qa"


@pytest.mark.parametrize("legacy_value", ["qa", "", "   "])
def test_unsupported_legacy_environment_without_canonical_env_is_precise(
    monkeypatch,
    legacy_value: str,
) -> None:
    from app.config import Settings

    monkeypatch.delenv("ENV", raising=False)

    with pytest.raises(RuntimeError, match="ENV.*legacy ENVIRONMENT.*unsupported"):
        Settings(
            _env_file=None,
            ENVIRONMENT=legacy_value,
            **_SECURE_NON_DEV_SETTINGS,
        )


def test_supported_ambient_legacy_environment_still_conflicts_with_canonical_env(
    monkeypatch,
) -> None:
    from app.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError, match="ENV and legacy ENVIRONMENT"):
        Settings(_env_file=None, ENV="dev")


@pytest.mark.parametrize(
    ("field", "placeholder"),
    [
        ("JWT_SECRET", ""),
        ("JWT_SECRET", "your-secret-key-change-in-production"),
        ("ADMIN_TOKEN", "   "),
        ("ADMIN_TOKEN", "dev-admin-token-change-me"),
        ("SIMULATOR_SESSION_SECRET", ""),
        ("SIMULATOR_SESSION_SECRET", "change-me-in-production"),
        ("SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "   "),
        ("SIMULATOR_CSRF_ORIGIN_ALLOWLIST", "change-me"),
    ],
)
def test_non_dev_rejects_every_current_empty_or_placeholder_security_value(
    field: str, placeholder: str
) -> None:
    from app.config import Settings

    values = {**_SECURE_NON_DEV_SETTINGS, field: placeholder}
    with pytest.raises(RuntimeError, match=field):
        Settings(_env_file=None, ENV="staging", **values)


def test_non_dev_accepts_configured_security_values() -> None:
    from app.config import Settings

    configured = Settings(_env_file=None, ENV="prod", **_SECURE_NON_DEV_SETTINGS)

    assert configured.ENV == "prod"


@pytest.mark.parametrize(
    "field",
    ["JWT_SECRET", "ADMIN_TOKEN", "SIMULATOR_SESSION_SECRET"],
)
def test_non_dev_rejects_secrets_shorter_than_32_characters(field: str) -> None:
    from app.config import Settings

    values = {**_SECURE_NON_DEV_SETTINGS, field: "x" * 31}
    with pytest.raises(RuntimeError, match=field):
        Settings(_env_file=None, ENV="prod", **values)


@pytest.mark.parametrize(
    ("field", "placeholder"),
    [
        ("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION_PLEASE_123456789"),
        ("ADMIN_TOKEN", "replace_me_with_a_real_admin_token_1234"),
        ("SIMULATOR_SESSION_SECRET", "todo_generate_a_session_secret_123456"),
    ],
)
def test_non_dev_rejects_anchored_long_placeholders(
    field: str, placeholder: str
) -> None:
    from app.config import Settings

    values = {**_SECURE_NON_DEV_SETTINGS, field: placeholder}
    with pytest.raises(RuntimeError, match=field):
        Settings(_env_file=None, ENV="prod", **values)


def test_placeholder_words_inside_strong_secret_are_not_false_positive() -> None:
    from app.config import Settings

    values = {
        **_SECURE_NON_DEV_SETTINGS,
        "JWT_SECRET": "random-prefix-your-secretary-key-938475-abcd",
    }

    configured = Settings(_env_file=None, ENV="prod", **values)

    assert configured.JWT_SECRET == values["JWT_SECRET"]


@pytest.mark.parametrize(
    "allowlist",
    [
        "*",
        "simulator.example.com",
        "https://",
        "https://user@simulator.example.com",
        "https://simulator.example.com/",
        "https://simulator.example.com/path",
        "https://simulator.example.com?query=1",
        "https://simulator.example.com#fragment",
    ],
)
def test_non_dev_rejects_non_origin_csrf_allowlist_entries(allowlist: str) -> None:
    from app.config import Settings

    values = {**_SECURE_NON_DEV_SETTINGS, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST": allowlist}
    with pytest.raises(RuntimeError, match="SIMULATOR_CSRF_ORIGIN_ALLOWLIST"):
        Settings(_env_file=None, ENV="prod", **values)


@pytest.mark.parametrize("environment", ["dev", "prod"])
def test_settings_reports_the_precise_invalid_csrf_allowlist_entry_without_broadening(
    environment: str,
) -> None:
    from app.config import Settings

    values = {} if environment == "dev" else {**_SECURE_NON_DEV_SETTINGS}
    values["SIMULATOR_CSRF_ORIGIN_ALLOWLIST"] = (
        "https://simulator.example.com,http://localhost:5176/"
    )

    with pytest.raises(
        RuntimeError,
        match=r"entry 2 is invalid.*trailing slash",
    ) as exc_info:
        Settings(_env_file=None, ENV=environment, **values)

    assert "must be set" not in str(exc_info.value)


def test_non_dev_empty_csrf_allowlist_remains_a_missing_configuration_error() -> None:
    from app.config import Settings

    values = {**_SECURE_NON_DEV_SETTINGS, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST": "  "}

    with pytest.raises(RuntimeError, match="must be set in non-dev environment"):
        Settings(_env_file=None, ENV="prod", **values)


def test_non_dev_accepts_comma_separated_exact_http_origins() -> None:
    from app.config import Settings

    allowlist = "https://simulator.example.com,http://localhost:5176"
    values = {**_SECURE_NON_DEV_SETTINGS, "SIMULATOR_CSRF_ORIGIN_ALLOWLIST": allowlist}

    configured = Settings(_env_file=None, ENV="prod", **values)

    assert configured.SIMULATOR_CSRF_ORIGIN_ALLOWLIST == allowlist


@pytest.mark.parametrize(
    ("configured", "canonical"),
    [
        ("https://Simulator.Example.com:443", "https://simulator.example.com"),
        ("HTTP://LOCALHOST:80", "http://localhost"),
        ("https://Simulator.Example.com:8443", "https://simulator.example.com:8443"),
        ("HTTP://[2001:DB8::1]:80", "http://[2001:db8::1]"),
        ("https://[2001:DB8::1]:8443", "https://[2001:db8::1]:8443"),
    ],
)
def test_settings_canonicalizes_browser_origin_serialization(
    configured: str, canonical: str
) -> None:
    from app.config import Settings

    values = {
        **_SECURE_NON_DEV_SETTINGS,
        "SIMULATOR_CSRF_ORIGIN_ALLOWLIST": configured,
    }

    settings = Settings(_env_file=None, ENV="prod", **values)

    assert settings.SIMULATOR_CSRF_ORIGIN_ALLOWLIST == canonical


@pytest.mark.parametrize("repeated", ["x" * 32, "ab" * 16, "abcd" * 8])
def test_non_dev_rejects_obviously_repeated_secrets(repeated: str) -> None:
    from app.config import Settings

    values = {**_SECURE_NON_DEV_SETTINGS, "JWT_SECRET": repeated}

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(_env_file=None, ENV="prod", **values)


@pytest.mark.parametrize(
    "field",
    ["JWT_SECRET", "ADMIN_TOKEN", "SIMULATOR_SESSION_SECRET"],
)
def test_non_dev_accepts_nonrepeating_secret_at_32_character_boundary(field: str) -> None:
    from app.config import Settings

    boundary_secret = "0123456789abcdef0123456789abcdeg"
    assert len(boundary_secret) == 32
    values = {**_SECURE_NON_DEV_SETTINGS, field: boundary_secret}

    configured = Settings(_env_file=None, ENV="prod", **values)

    assert getattr(configured, field) == boundary_secret

