"""Воспроизводимый замер класса «проглоченное исключение» в app/.

Существует потому, что программа 008 однажды уже сняла заявленное число как
невоспроизводимое: «производящие скрипты в репозитории отсутствуют». Знаменатель
программы 010 обязан пересчитываться одной командой, а не доверием к записи.

Запуск из корня репозитория:

    .venv/Scripts/python.exe specs/010-money-core-fail-closed/measure_swallowed_exceptions.py

Считаются обработчики (AST), а не строки (grep). Регекс здесь непригоден
принципиально: сам T809-B1 лежит внутри УЗКОГО `except DBAPIError`, поэтому
поиск по «голым except» его не находит.

Три состояния разделены, потому что для 010 они означают разную работу:

  swallow           тело обработчика не содержит `raise` — отказ не
                    распространяется дальше по стеку;
  pass_only         тело обработчика есть ровно `pass` — нет ни логирования,
                    ни сигнала наружу. Это определение исходного знаменателя
                    из `specs/BACKLOG.md`;
  rollback_swallow  внутри `try` вокруг `rollback()`/`commit()` отказ проглочен.
                    Ищется НЕЗАВИСИМО от типа внешнего обработчика.

ВАЖНО про rollback_swallow: список является входом в ручной разбор, а не
списком дефектов. Проверено 2026-08-21 — из шести попаданий в денежном ядре
только одно (`payments/engine.py:540`) молча продолжает работу; остальные
пробрасывают исключение, закрываются fail-closed или продолжают на отдельной
сессии. Автоматика отличить это не может.
"""

import ast
import collections
import pathlib
import sys

MONEY_CORE = ("app/core/payments/", "app/core/clearing/", "app/core/recovery.py")


def handler_kind(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "bare"
    if isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "BaseException"):
        return "broad"
    return "narrow"


def has_raise(body) -> bool:
    return any(isinstance(sub, ast.Raise) for node in body for sub in ast.walk(node))


def is_money(posix: str) -> bool:
    return posix.startswith(MONEY_CORE)


def collect(root: pathlib.Path):
    handlers, rollback_sites = [], []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        posix = path.as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print("SKIP (syntax): %s: %s" % (posix, exc), file=sys.stderr)
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and handler_kind(node) != "narrow":
                handlers.append(
                    {
                        "file": posix,
                        "line": node.lineno,
                        "kind": handler_kind(node),
                        "swallow": not has_raise(node.body),
                        "pass_only": len(node.body) == 1 and isinstance(node.body[0], ast.Pass),
                        "money": is_money(posix),
                    }
                )

            if isinstance(node, ast.Try):
                calls = [
                    call
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in ("rollback", "commit")
                ]
                if not calls:
                    continue
                for inner in node.handlers:
                    if handler_kind(inner) != "narrow" and not has_raise(inner.body):
                        rollback_sites.append(
                            {
                                "file": posix,
                                "line": inner.lineno,
                                "call": calls[0].func.attr,
                                "money": is_money(posix),
                            }
                        )
                        break
    return handlers, rollback_sites


def main() -> int:
    root = pathlib.Path("app")
    if not root.is_dir():
        print("Run from the repository root: app/ not found", file=sys.stderr)
        return 2

    handlers, rollback_sites = collect(root)
    swallow = [h for h in handlers if h["swallow"]]
    pass_only = [h for h in handlers if h["pass_only"]]
    bare = [h for h in handlers if h["kind"] == "bare"]

    def money(rows):
        return [r for r in rows if r["money"]]

    def pct(part, whole):
        return 100 * len(part) // max(1, len(whole))

    print("broad+bare handlers in app/ : %d   (truly bare `except:`: %d)" % (len(handlers), len(bare)))
    print("  swallow (no raise in body): %d   money/recovery core: %d (%d%%)"
          % (len(swallow), len(money(swallow)), pct(money(swallow), swallow)))
    print("  body is exactly `pass`    : %d   money/recovery core: %d (%d%%)   <- BACKLOG denominator"
          % (len(pass_only), len(money(pass_only)), pct(money(pass_only), pass_only)))
    print("  swallowed rollback/commit : %d   money/recovery core: %d"
          % (len(rollback_sites), len(money(rollback_sites))))
    print()
    print("`except <broad>: pass` by file (top 10):")
    for name, count in collections.Counter(h["file"] for h in pass_only).most_common(10):
        print("  %3d  %s" % (count, name))
    print()
    print("FULL LIST - swallowed rollback()/commit() failure (input for manual triage):")
    for row in sorted(rollback_sites, key=lambda r: (r["file"], r["line"])):
        mark = "   <-- MONEY CORE" if row["money"] else ""
        print("  %s:%d  (%s)%s" % (row["file"], row["line"], row["call"], mark))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
