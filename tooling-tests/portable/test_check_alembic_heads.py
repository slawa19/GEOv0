from __future__ import annotations

from pathlib import Path

import scripts.check_alembic_heads as head_check


def test_head_check_uses_only_absolute_script_location(monkeypatch, capsys) -> None:
    configured_options: dict[str, str] = {}

    class FilelessConfig:
        def __init__(self) -> None:
            pass

        def set_main_option(self, name: str, value: str) -> None:
            configured_options[name] = value

    class SingleHeadDirectory:
        @staticmethod
        def from_config(config: FilelessConfig) -> SingleHeadDirectory:
            assert isinstance(config, FilelessConfig)
            return SingleHeadDirectory()

        def get_heads(self) -> list[str]:
            return ["revision_head"]

    monkeypatch.setattr(head_check, "Config", FilelessConfig)
    monkeypatch.setattr(head_check, "ScriptDirectory", SingleHeadDirectory)

    assert head_check.main() == 0

    script_location = Path(configured_options["script_location"])
    assert script_location.is_absolute()
    assert script_location == Path(head_check.__file__).resolve().parents[1] / "migrations"
    assert capsys.readouterr().out == "Alembic head check passed: revision_head\n"
