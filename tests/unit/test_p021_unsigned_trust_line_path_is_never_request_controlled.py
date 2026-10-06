"""Architecture guard (programme 021, stage 1, `T2102`): the unsigned trust-line path is never request-controlled.

WHAT IT PINS (spec, "Решения" item 4 and "Запрещено"). `TrustLineService.execute_create/update/close` take a
keyword-only `require_signature`; `begin_internal_batch` is the seeder's and
drift's internal entrance. The rule has three parts, each checked on the source of `app/`:

1. every call of `execute_*` passes `require_signature=` as a LITERAL `True` or `False` - never a variable, an
   attribute or an expression, which is the only way a request could reach it;
2. a literal `False` and `begin_internal_batch` appear only in the NAMED trusted
   modules below (stage 1: the seeder and drift; stage 2: the inject executor and the Interact handlers);
3. no request schema (`app/schemas/`) mentions `require_signature` at all, and an HTTP module (`app/api/`) names
   it ONLY as the literal keyword of an `execute_*` call - and only if it is a trusted module (stage 2: the
   Interact handlers of `app/api/v1/simulator.py`). A field, an annotation, a parameter, a variable, a string or
   any other use there is refused: there is no field a client could set and no value a request could flow into.

WHAT IT DOES NOT SEE. It reads call syntax: a call made through `getattr(service, name)`, a re-bound method, or
`functools.partial` is invisible to it; rule 3 reads the syntax tree, so a comment naming the flag is not a use;
and it says nothing about what the unsigned path does - that is the
behaviour tests' job (`tests/unit/test_p021_public_trust_line_operations_require_a_signature.py` for the public
side, R-021-2/3/4 for the internal one). A green run here means the syntax above holds, not that the path is
safe. The counter-checks at the bottom feed the checker sources it must refuse, so a checker that stopped
looking cannot pass.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "app"

EXECUTE = {"execute_create", "execute_update", "execute_close"}
INTERNAL_ENTRANCES = {"begin_internal_batch"}  # 030 S3b: `import_initial_trustlines` is gone

#: The named trusted callers of the unsigned path, by module (spec, "Решения" item 4). Stage 1: the seeder and
#: drift; stage 2: the inject executor and the Interact actions' handlers.
TRUSTED_UNSIGNED_MODULES = {
    "app/core/simulator/real_scenario_seeder.py",
    "app/core/simulator/trust_drift_engine.py",
    "app/core/simulator/inject_executor.py",
    "app/api/v1/simulator.py",
}
SERVICE_MODULE = "app/core/trustlines/service.py"


def violations_in(source: str, module: str) -> list[str]:
    """Every breach of rules 1-2 in one module's source. `module` is its repo-relative posix path."""

    out: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in EXECUTE:
            kw = [k for k in node.keywords if k.arg == "require_signature"]
            if len(kw) != 1:
                out.append(f"{module}:{node.lineno}: {name}() without an explicit require_signature=")
                continue
            value = kw[0].value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, bool)):
                out.append(f"{module}:{node.lineno}: {name}(require_signature=<not a literal bool>)")
                continue
            if value.value is False and module not in TRUSTED_UNSIGNED_MODULES:
                out.append(f"{module}:{node.lineno}: {name}(require_signature=False) outside the trusted modules")
        elif name in INTERNAL_ENTRANCES and module not in TRUSTED_UNSIGNED_MODULES | {SERVICE_MODULE}:
            out.append(f"{module}:{node.lineno}: {name}() outside the trusted modules")
    return out


def _app_modules() -> list[tuple[str, str]]:
    return [
        (path.relative_to(REPO).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(APP.rglob("*.py"))
    ]


def test_every_call_of_the_internal_path_is_literal_and_trusted() -> None:
    found: list[str] = []
    unsigned_calls = 0
    for module, source in _app_modules():
        found += violations_in(source, module)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE:
                for k in node.keywords:
                    if k.arg == "require_signature" and isinstance(k.value, ast.Constant) and k.value.value is False:
                        unsigned_calls += 1
    assert not found, "\n".join(found)
    # Anti-vacuum: the scan does see the drift engine's unsigned call.
    assert unsigned_calls >= 1, "the scan found no unsigned call at all - it is not looking where the code is"


def test_the_public_operations_always_require_a_signature() -> None:
    tree = ast.parse((REPO / SERVICE_MODULE).read_text(encoding="utf-8"))
    service = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TrustLineService")
    seen: dict[str, list[bool]] = {}
    for method in service.body:
        if not isinstance(method, ast.AsyncFunctionDef) or method.name not in {"create", "update", "close"}:
            continue
        for node in ast.walk(method):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE:
                kw = next(k for k in node.keywords if k.arg == "require_signature")
                seen.setdefault(method.name, []).append(kw.value.value)
    assert seen == {"create": [True], "update": [True], "close": [True]}, seen


FLAG = "require_signature"


def _flag_uses_outside_execute_keywords(source: str) -> list[int]:
    """Line numbers of every syntactic use of the flag in `source` other than `execute_*(..., require_signature=<bool>)`."""

    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE:
            for k in node.keywords:
                if k.arg == FLAG and isinstance(k.value, ast.Constant) and isinstance(k.value.value, bool):
                    allowed.add(id(k))
    uses: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == FLAG and id(node) not in allowed:
            uses.append(node.value.lineno)
        elif isinstance(node, ast.Name) and node.id == FLAG:
            uses.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == FLAG:
            uses.append(node.lineno)
        elif isinstance(node, ast.arg) and node.arg == FLAG:
            uses.append(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and FLAG in node.value:
            uses.append(node.lineno)
    return uses


def request_surface_offenders(modules: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    for module, source in modules:
        if module.startswith("app/schemas/"):
            if FLAG in source:
                out.append(module)
        elif module.startswith("app/api/"):
            if module not in TRUSTED_UNSIGNED_MODULES:
                if FLAG in source:
                    out.append(module)
            elif _flag_uses_outside_execute_keywords(source):
                out.append(module)
    return out


def test_no_request_schema_or_http_module_can_carry_the_flag() -> None:
    modules = _app_modules()
    assert any(m.startswith("app/schemas/") for m, _ in modules), "premise: the scan reaches app/schemas"
    offenders = request_surface_offenders(modules)
    assert not offenders, offenders
    # Anti-vacuum: the trusted HTTP module is scanned and does carry the literal flag (stage 2).
    interact = dict(modules)["app/api/v1/simulator.py"]
    assert FLAG in interact, "premise: the Interact handlers call the internal path"


# ---------------------------------------------------------------------------------------- counter-checks


def test_counter_check_a_request_derived_flag_is_refused() -> None:
    source = "async def f(svc, b, data):\n    await svc.execute_update(b, 1, 2, data, require_signature=data.unsigned)\n"
    assert violations_in(source, "app/core/simulator/trust_drift_engine.py"), "a non-literal flag passed"


def test_counter_check_an_unsigned_call_outside_the_trusted_modules_is_refused() -> None:
    source = "async def f(svc, b, d):\n    await svc.execute_close(b, 1, 2, d, require_signature=False)\n"
    assert violations_in(source, "app/api/v1/trustlines.py"), "an untrusted unsigned call passed"
    assert not violations_in(source, "app/core/simulator/trust_drift_engine.py"), "a trusted module was refused"


def test_counter_check_a_missing_flag_and_a_stray_import_are_refused() -> None:
    missing = "async def f(svc, b, d):\n    await svc.execute_create(b, 1, d)\n"
    assert violations_in(missing, "app/core/simulator/trust_drift_engine.py"), "a call without the flag passed"
    stray = "async def f(svc, b):\n    await svc.begin_internal_batch()\n"
    assert violations_in(stray, "app/core/simulator/real_payments_executor.py"), "an untrusted import call passed"


def test_counter_check_a_schema_field_is_seen() -> None:
    planted = ("app/schemas/trustline.py", "class TrustLineCreateRequest:\n    require_signature: bool = True\n")
    assert request_surface_offenders([planted]) == ["app/schemas/trustline.py"]
    assert request_surface_offenders([("app/core/payments/service.py", "require_signature=False")]) == []


def test_counter_check_the_trusted_http_module_may_only_pass_the_literal() -> None:
    module = "app/api/v1/simulator.py"
    literal = "async def h(svc, b, d):\n    await svc.execute_close(b, 1, 2, d, require_signature=False)\n"
    assert request_surface_offenders([(module, literal)]) == [], "the literal keyword of a trusted call was refused"
    planted = {
        "a request field": "class Req(BaseModel):\n    require_signature: bool = True\n",
        "a handler parameter": "async def h(require_signature: bool = True):\n    pass\n",
        "a request-derived value": "flag = req.require_signature\n",
        "a string key": "body = {'require_signature': False}\n",
        "a non-execute keyword": "make(require_signature=False)\n",
    }
    for what, source in planted.items():
        assert request_surface_offenders([(module, source)]) == [module], f"{what} in the Interact module passed"
    # An untrusted HTTP module may not name it at all, not even as the literal.
    assert request_surface_offenders([("app/api/v1/trustlines.py", literal)]) == ["app/api/v1/trustlines.py"]
