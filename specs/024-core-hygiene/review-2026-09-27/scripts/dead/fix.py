p="impl-dead-code.md"
s=open(p,encoding="utf-8").read()
pairs=[("**реально мёртвых в продакшене — 16 символов**, ещё 9 живут **только ради тестов**","**реально мёртвых в продакшене — 19 символов** (15 функций/классов/методов + 4 журнальные константы), ещё 8 живут **только ради тестов**"),
("После ручной проверки: **мёртвых в продакшене 16** (из них покрыты `T1519` — 4: `validate_idempotency_key`, `PaymentService.get_payment`, `PaymentDetail`, `_apply_inject_event`; покрыты 021 — 1: `safe_decimal_env` косвенно через owner surface), **только-тесты 9**, **живые через динамический вход/хуки 6** (pydantic `model_post_init`, TypeDecorator `process_bind_param`, `SimulatorEvent` — контракт OpenAPI, и т. п.).","После ручной проверки: **мёртвых в продакшене 19** (из них покрыты `T1519` — 4: `validate_idempotency_key`, `PaymentService.get_payment`, `PaymentDetail`, `_apply_inject_event`; в owner surface 021 без упоминания — 2: `safe_decimal_env`, `_require_run_accepts_actions_or_error`), **только-тесты 8**, остальное — живые через динамический вход (pydantic `model_post_init`, TypeDecorator `process_bind_param`, SQLAlchemy `@validates`, `SimulatorEvent` — контракт OpenAPI, 155 маршрутов/обработчиков с декораторами)."),
("Остатки 017–019: ~240 совпадений","Остатки 017–019: 324 совпадения")]
for a,b in pairs:
    assert a in s, a[:40]
    s=s.replace(a,b)
open(p,"w",encoding="utf-8").write(s); print("ok")
