from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def _strip_bom(s: str) -> str:
    # UTF-8 BOM can sneak into JSON fixtures on Windows editors.
    return s.lstrip("\ufeff")


def _iter_fixture_json_files(workspace_root: Path) -> Iterable[Path]:
    bases = [
        workspace_root / "admin-fixtures" / "v1" / "datasets",
        workspace_root / "admin-ui" / "public" / "admin-fixtures" / "v1" / "datasets",
    ]
    for base in bases:
        if not base.exists():
            continue
        yield from sorted(base.glob("*.json"))


def _parse_first_json_value(text: str) -> tuple[Any, int]:
    dec = json.JSONDecoder()
    s = _strip_bom(text)

    # skip leading whitespace
    i = 0
    n = len(s)
    while i < n and s[i].isspace():
        i += 1

    value, end = dec.raw_decode(s, idx=i)
    return value, end


def _has_trailing_non_ws(text: str, end: int) -> bool:
    s = _strip_bom(text)
    return any(not ch.isspace() for ch in s[end:])


def fix_file(path: Path) -> bool:
    raw = path.read_text(encoding="utf-8")

    # If it already parses as a single JSON value, leave it untouched.
    try:
        json.loads(_strip_bom(raw))
        return False
    except json.JSONDecodeError as e:
        parse_error = e

    value, end = _parse_first_json_value(raw)
    if not _has_trailing_non_ws(raw, end):
        # It failed json.loads but appears to have no trailing content; rethrow the
        # original parse error (this is likely a genuine JSON issue, not concatenation).
        raise parse_error

    # Truncate to the first JSON document and normalize formatting.
    normalized = json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(normalized + "\n", encoding="utf-8")
    return True


def main() -> None:
    root = Path(__file__).resolve().parents[1]

    fixed: list[Path] = []
    skipped: list[Path] = []

    for p in _iter_fixture_json_files(root):
        try:
            changed = fix_file(p)
        except Exception as e:  # pragma: no cover
            raise SystemExit(f"Failed to fix {p}: {e}") from e

        if changed:
            fixed.append(p)
        else:
            skipped.append(p)

    if fixed:
        print("Fixed concatenated JSON in:")
        for p in fixed:
            print(" -", p.relative_to(root))
    else:
        print("No concatenated JSON files found.")


if __name__ == "__main__":
    main()
