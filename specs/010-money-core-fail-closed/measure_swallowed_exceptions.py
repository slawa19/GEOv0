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
  rollback_swallow  отказ `rollback()`/`commit()` проглочен обработчиком того
                    `try`, в теле которого вызов и стоит. Ищется НЕЗАВИСИМО от
                    типа внешнего обработчика.

ЧТО ИСПРАВЛЕНО 2026-08-21 ПОСЛЕ ВНЕШНЕГО РЕВЬЮ CODEX (все три дефекта его):

1. Двойной счёт. Прежняя версия брала вызовы через `ast.walk(try_node)`, то есть
   захватывала вложенные `try` и тела обработчиков. Один `await session.rollback()`
   в `app/core/recovery.py` попадал в список дважды — как `:245` и как `:250`.
   Теперь вызовы ищутся ТОЛЬКО в непосредственном теле `try` (`node.body`), а сайт
   ключуется строкой самого вызова, а не строкой обработчика.
2. `break` после первого подходящего обработчика терял остальные, а `calls[0]`
   терял остальные вызовы. Обе усечки сняты.
3. `has_raise` шёл неограниченным `ast.walk` и считал «пробрасывающим» обработчик,
   у которого `raise` лежит во вложенной функции. Спуск в определения функций
   прекращён.

ОСТАВШЕЕСЯ ОГРАНИЧЕНИЕ, НАЗВАННОЕ ЯВНО: `has_raise` по-прежнему засчитывает
`raise`, находящийся внутри вложенного `try`, чей собственный обработчик его
перехватывает. Такой обработчик будет ошибочно отнесён к «пробрасывающим», то
есть доля swallow — НИЖНЯЯ граница. Исправление требует моделирования потока, а
не обхода дерева, и в задачу замера не входит.

ВАЖНО: список rollback_swallow является ВХОДОМ В РУЧНОЙ РАЗБОР, а не списком
дефектов. Проверено 2026-08-21: из пяти уникальных сайтов денежного ядра два
fail-open (`payments/engine.py:539`, `clearing/service.py:218` через вызывающего),
три fail-closed. Автоматика этого различия не видит.
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


def walk_no_nested_funcs(node):
    """ast.walk, не спускающийся в определения вложенных функций и классов."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            stack.append(child)


def has_raise(body) -> bool:
    return any(
        isinstance(sub, ast.Raise) for stmt in body for sub in walk_no_nested_funcs(stmt)
    )


def direct_txn_calls(try_node: ast.Try):
    """rollback()/commit() в НЕПОСРЕДСТВЕННОМ теле try, без вложенных try и handlers."""
    found = []
    for stmt in try_node.body:
        for sub in walk_no_nested_funcs(stmt):
            if isinstance(sub, ast.Try):
                continue
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("rollback", "commit")
            ):
                found.append(sub)
    return found


def is_money(posix: str) -> bool:
    return posix.startswith(MONEY_CORE)


def collect(root: pathlib.Path):
    handlers = []
    sites = {}  # (file, call_lineno) -> row; ключ по вызову исключает двойной счёт
    suppress_uses = []

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

            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", None) or getattr(func, "id", None)
                if name == "suppress":
                    suppress_uses.append("%s:%d" % (posix, node.lineno))

            if isinstance(node, ast.Try):
                calls = direct_txn_calls(node)
                if not calls:
                    continue
                swallowing = [
                    h for h in node.handlers if handler_kind(h) != "narrow" and not has_raise(h.body)
                ]
                if not swallowing:
                    continue
                for call in calls:
                    sites[(posix, call.lineno)] = {
                        "file": posix,
                        "line": call.lineno,
                        "call": call.func.attr,
                        "handlers": sorted(h.lineno for h in swallowing),
                        "money": is_money(posix),
                    }

    return handlers, list(sites.values()), suppress_uses


def main() -> int:
    root = pathlib.Path("app")
    if not root.is_dir():
        print("Run from the repository root: app/ not found", file=sys.stderr)
        return 2

    handlers, sites, suppress_uses = collect(root)
    swallow = [h for h in handlers if h["swallow"]]
    pass_only = [h for h in handlers if h["pass_only"]]
    bare = [h for h in handlers if h["kind"] == "bare"]

    def money(rows):
        return [r for r in rows if r["money"]]

    def pct(part, whole):
        return (100.0 * len(part) / len(whole)) if whole else 0.0

    print("broad+bare handlers in app/ : %d   (truly bare `except:`: %d)" % (len(handlers), len(bare)))
    print("  swallow (no raise in body): %d   money/recovery core: %d (%.2f%%)   [LOWER BOUND]"
          % (len(swallow), len(money(swallow)), pct(money(swallow), swallow)))
    print("  body is exactly `pass`    : %d   money/recovery core: %d (%.2f%%)   <- BACKLOG denominator"
          % (len(pass_only), len(money(pass_only)), pct(money(pass_only), pass_only)))
    print("  swallowed rollback/commit : %d   money/recovery core: %d   [unique call sites]"
          % (len(sites), len(money(sites))))
    print("  contextlib.suppress uses  : %d" % len(suppress_uses))
    print()
    print("`except <broad>: pass` by file (top 10):")
    for name, count in collections.Counter(h["file"] for h in pass_only).most_common(10):
        print("  %3d  %s" % (count, name))
    print()
    print("FULL LIST - swallowed rollback()/commit() (input for MANUAL triage, not a defect list):")
    for row in sorted(sites, key=lambda r: (r["file"], r["line"])):
        mark = "   <-- MONEY CORE" if row["money"] else ""
        print("  %s:%d  %s()  swallowed by handler(s) %s%s"
              % (row["file"], row["line"], row["call"],
                 ",".join(str(x) for x in row["handlers"]), mark))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
