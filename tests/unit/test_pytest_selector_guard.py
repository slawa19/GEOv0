from pathlib import Path

import pytest

from scripts.validate_pytest_selectors import (
    UnsafePytestSelectorError,
    assert_safe_pytest_selectors,
)


def _make_repo(tmp_path: Path) -> Path:
    tests_root = tmp_path / "tests"
    (tests_root / "unit").mkdir(parents=True)
    (tests_root / "integration").mkdir()
    (tests_root / "unit" / "test_one.py").write_text("def test_one(): pass\n")
    (tests_root / "integration" / "test_two.py").write_text("def test_two(): pass\n")
    return tmp_path


def test_accepts_multiple_repo_local_paths_and_node_ids(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)

    assert_safe_pytest_selectors(
        [
            "tests/unit/test_one.py::test_one",
            "tests/integration/test_two.py",
        ],
        repo_root=repo_root,
    )


@pytest.mark.parametrize(
    "selectors",
    [
        ["--basetemp=D:/valuable"],
        ["-m", "slow"],
        ["-p", "plugin_name"],
    ],
)
def test_rejects_pytest_option_injection(tmp_path: Path, selectors: list[str]) -> None:
    repo_root = _make_repo(tmp_path)

    with pytest.raises(UnsafePytestSelectorError, match="pytest option"):
        assert_safe_pytest_selectors(selectors, repo_root=repo_root)


@pytest.mark.parametrize(
    "selector",
    [
        "../tests/unit/test_one.py",
        "tests/../tests/unit/test_one.py",
        "scripts/test_one.py",
        "D:/outside/test_one.py",
        "/outside/test_one.py",
    ],
)
def test_rejects_absolute_parent_and_outside_paths(
    tmp_path: Path, selector: str
) -> None:
    repo_root = _make_repo(tmp_path)

    with pytest.raises(UnsafePytestSelectorError):
        assert_safe_pytest_selectors([selector], repo_root=repo_root)
