import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_FIELDS = {
    "JWT_SECRET",
    "ADMIN_TOKEN",
    "SIMULATOR_SESSION_SECRET",
    "SIMULATOR_CSRF_ORIGIN_ALLOWLIST",
}


def _read_compose(name: str) -> dict:
    return yaml.safe_load((_ROOT / name).read_text(encoding="utf-8"))


def _read_text(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


def test_base_compose_is_production_like_and_fails_closed() -> None:
    environment = _read_compose("docker-compose.yml")["services"]["app"][
        "environment"
    ]

    assert environment["ENV"] == "${ENV:-prod}"
    assert "ENVIRONMENT" not in environment
    for field in _SECURITY_FIELDS:
        assert environment[field] == f"${{{field}:-}}"


def test_dev_compose_uses_canonical_env_and_explicit_dev_defaults() -> None:
    environment = _read_compose("docker-compose.dev.yml")["services"]["app"][
        "environment"
    ]

    assert environment["ENV"] == "dev"
    assert "ENVIRONMENT" not in environment
    assert "change-me" in environment["JWT_SECRET"]
    assert "change-me" in environment["ADMIN_TOKEN"]
    assert "change-me" in environment["SIMULATOR_SESSION_SECRET"]
    assert environment["SIMULATOR_CSRF_ORIGIN_ALLOWLIST"].endswith(":-}")


def test_dockerfiles_have_distinct_documented_roles() -> None:
    base = _read_compose("docker-compose.yml")["services"]["app"]["build"]
    dev = _read_compose("docker-compose.dev.yml")["services"]["app"]["build"]
    canonical_dockerfile = (_ROOT / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    dev_dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert base["dockerfile"] == "docker/Dockerfile"
    assert dev["dockerfile"] == "Dockerfile"
    assert "canonical base/production image" in canonical_dockerfile
    assert "development image" in dev_dockerfile
    assert 'ENTRYPOINT ["./docker-entrypoint.sh"]' in canonical_dockerfile
    assert 'ENTRYPOINT ["./docker/docker-entrypoint.sh"]' in dev_dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in canonical_dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in dev_dockerfile


def test_both_images_consume_readiness_health() -> None:
    canonical_dockerfile = _read_text("docker/Dockerfile")
    dev_dockerfile = _read_text("Dockerfile")

    for dockerfile in (canonical_dockerfile, dev_dockerfile):
        assert "HEALTHCHECK" in dockerfile
        assert "http://127.0.0.1:8000/health" in dockerfile
    assert "urllib.request.urlopen" in canonical_dockerfile


def test_dev_image_content_gate_excludes_runtime_and_dependency_trees() -> None:
    dockerignore = {
        line.strip()
        for line in _read_text(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        ".local-run",
        ".venv",
        "node_modules",
        "**/node_modules",
        "*.db",
        "**/*.db",
    } <= dockerignore

    workflow = yaml.safe_load(_read_text(".github/workflows/quality.yml"))
    job = workflow["jobs"]["dev-image-content"]
    assert "if" not in job, "Dev image content must be checked on every workflow trigger"

    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )
    assert "docker build --file Dockerfile --target runtime" in commands
    for sentinel in (
        ".local-run/t502-sentinel.db",
        ".venv/t502-sentinel",
        "node_modules/t502-sentinel",
        "admin-ui/node_modules/t502-sentinel",
        "t502-root-sentinel.db",
    ):
        assert sentinel in commands
    assert "find /app -type d -name node_modules" in commands
    assert 'find /app -type f -name "*.db"' in commands


def test_dev_image_runs_shared_migrations_before_its_reload_command() -> None:
    dev_service = _read_compose("docker-compose.dev.yml")["services"]["app"]
    entrypoint = (_ROOT / "docker" / "docker-entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert dev_service["build"]["dockerfile"] == "Dockerfile"
    assert dev_service["command"][:3] == ["uvicorn", "app.main:app", "--host"]
    assert "alembic -c migrations/alembic.ini upgrade head" in entrypoint
    assert entrypoint.index("alembic -c migrations/alembic.ini upgrade head") < entrypoint.index(
        'exec "$@"'
    )
    assert "exec uvicorn" not in entrypoint


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="entrypoint behavior test requires a POSIX shell",
)
def test_entrypoint_preserves_custom_command_after_migrations(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trace = tmp_path / "trace.log"
    stubs = {
        "python": "#!/bin/sh\ncat >/dev/null\nprintf 'preflight\\n' >> \"$TRACE\"\n",
        "alembic": "#!/bin/sh\nprintf 'alembic:%s\\n' \"$*\" >> \"$TRACE\"\n",
        "custom-command": (
            "#!/bin/sh\n"
            "printf 'command:%s|%s\\n' \"$1\" \"$2\" >> \"$TRACE\"\n"
        ),
    }
    for name, contents in stubs.items():
        executable = bin_dir / name
        executable.write_text(contents, encoding="utf-8", newline="\n")
        executable.chmod(0o755)

    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://unused:unused@db/unused",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "TRACE": str(trace),
    }
    subprocess.run(
        [
            "bash",
            str(_ROOT / "docker" / "docker-entrypoint.sh"),
            "custom-command",
            "alpha",
            "two words",
        ],
        cwd=_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert trace.read_text(encoding="utf-8").splitlines() == [
        "preflight",
        "alembic:-c migrations/alembic.ini upgrade head",
        "command:alpha|two words",
    ]


def test_env_example_has_no_runnable_non_dev_security_placeholders() -> None:
    values = {}
    for raw_line in (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    assert values["ENV"] == "prod"
    for field in _SECURITY_FIELDS:
        assert values[field] == ""


def test_active_local_callers_select_dev_configuration_explicitly() -> None:
    """Source-policy guard; runtime Compose behavior is verified separately."""
    overlay = "docker compose -f docker-compose.yml -f docker-compose.dev.yml"
    readme = _read_text("README.md")
    contributing = _read_text("docs/ru/06-contributing.md")
    contributing_en = _read_text("docs/en/06-contributing.md")
    contributing_pl = _read_text("docs/pl/06-contributing.md")
    real_mode_runbook = _read_text(
        "docs/ru/simulator/backend/real-mode-runbook.md"
    )
    debugging = _read_text("docs/ru/testing/quick-start-and-debugging.md")
    wsl_runbook = _read_text("docs/ru/runbook-dev-wsl2-docker-no-desktop.md")
    real_simulator_runner = _read_text("scripts/run_real_simulator.ps1")
    wsl_production = wsl_runbook.split("### 4.1", 1)[1].split("### 4.2", 1)[0]
    wsl_development = wsl_runbook.split("### 4.2", 1)[1].split("### 4.3", 1)[0]
    wsl_quick_summary = wsl_runbook.split("## 8)", 1)[1]

    assert readme.count(f"{overlay} up -d --build") >= 2
    assert "$env:ENV = 'dev'" in readme
    assert overlay in contributing
    assert '$env:ENV = "dev"' in contributing
    assert overlay in contributing_en
    assert '$env:ENV = "dev"' in contributing_en
    assert overlay in contributing_pl
    assert '$env:ENV = "dev"' in contributing_pl
    for local_guide in (
        readme,
        contributing,
        contributing_en,
        contributing_pl,
    ):
        assert "docker compose up -d --build" not in local_guide
        assert "docker compose exec app" not in local_guide
    assert f"{overlay} up --build" in real_mode_runbook
    assert "Production-like" in real_mode_runbook
    assert "заполнения `JWT_SECRET`, `ADMIN_TOKEN`" in real_mode_runbook
    assert "$env:ENV = 'dev'" in debugging
    assert "заполните `JWT_SECRET`" in wsl_production
    assert "docker compose up -d --build" in wsl_production
    assert "docker compose exec app" in wsl_production
    assert overlay not in wsl_production
    assert f"{overlay} up --build" in wsl_development
    assert f"{overlay} up -d --build" in wsl_quick_summary
    assert f"{overlay} exec app" in wsl_quick_summary
    assert (
        "$script:DevComposeFiles = @('-f', 'docker-compose.yml', "
        "'-f', 'docker-compose.dev.yml')"
    ) in real_simulator_runner
    assert (
        "$argsWithAnsi = @('--ansi','never') + $script:DevComposeFiles + $composeArgs"
    ) in real_simulator_runner
