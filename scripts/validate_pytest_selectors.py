"""Validate positional pytest selectors accepted by the canonical wrapper."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafePytestSelectorError(ValueError):
    """Raised when a selector could escape tests/ or be parsed as an option."""


def _path_parts(selector_path: str) -> tuple[str, ...]:
    return PurePosixPath(selector_path.replace("\\", "/")).parts


def assert_safe_pytest_selectors(
    selectors: list[str] | tuple[str, ...],
    *,
    repo_root: Path,
) -> None:
    """Require existing repo-local test paths, optionally followed by node IDs."""

    resolved_repo_root = repo_root.resolve()
    tests_root = (resolved_repo_root / "tests").resolve()

    for selector in selectors:
        if not selector or not selector.strip():
            raise UnsafePytestSelectorError("Backend selectors cannot be empty.")
        if selector.lstrip().startswith("-"):
            raise UnsafePytestSelectorError(
                f"Backend selector cannot be a pytest option: {selector!r}."
            )

        selector_path, separator, node_id = selector.partition("::")
        if separator and not node_id:
            raise UnsafePytestSelectorError(
                f"Backend selector has an empty pytest node ID: {selector!r}."
            )
        if not selector_path:
            raise UnsafePytestSelectorError(
                f"Backend selector has no test path: {selector!r}."
            )

        if (
            Path(selector_path).is_absolute()
            or PureWindowsPath(selector_path).is_absolute()
        ):
            raise UnsafePytestSelectorError(
                f"Backend selector must be relative to the repository: {selector!r}."
            )

        parts = _path_parts(selector_path)
        if ".." in parts:
            raise UnsafePytestSelectorError(
                f"Backend selector cannot contain '..': {selector!r}."
            )

        candidate = (resolved_repo_root / Path(*parts)).resolve()
        try:
            candidate.relative_to(tests_root)
        except ValueError as exc:
            raise UnsafePytestSelectorError(
                f"Backend selector must stay under tests/: {selector!r}."
            ) from exc

        if not candidate.exists():
            raise UnsafePytestSelectorError(
                f"Backend selector path does not exist: {selector!r}."
            )
        if candidate.is_file() and candidate.suffix != ".py":
            raise UnsafePytestSelectorError(
                f"Backend selector file must be a Python test file: {selector!r}."
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("selectors", nargs="*")
    args = parser.parse_args(argv)

    try:
        assert_safe_pytest_selectors(args.selectors, repo_root=args.repo_root)
    except UnsafePytestSelectorError as exc:
        print(f"Unsafe pytest selector: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
