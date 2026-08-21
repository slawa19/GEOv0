# Simulator super-smoke

Super-smoke — дорогой backend milestone для сценария Simulator через fixtures,
deterministic real logic и real-mode HTTP startup. Он не предназначен для
внутреннего debug-цикла.

## Канонический запуск

```powershell
.\scripts\verify_local.ps1 -TaskSlug simulator_super_smoke -BackendOnly `
  -BackendSelector tests/integration/test_simulator_super_smoke.py -IncludeExpensive
```

Команда изолирует SQLite, pytest basetemp/cache и postmortem output под
`.local-run/test-runs/simulator_super_smoke/`. Для параллельного запуска назначьте
другой `TaskSlug`; shared DB/output запрещены.

## Когда запускать

- после изменения simulator runtime или SSE lifecycle;
- после изменения payments/clearing, потребляемого simulator;
- после изменения schema/serialization UI-facing events;
- перед merge связного simulator milestone.

Успех super-smoke не доказывает браузерный UX, Postgres concurrency или полный
required local gate. Их результаты сообщаются отдельно.

## Failure artifacts

При падении тест пишет диагностические файлы в task-local `artifacts/`. Эти файлы
ignored, не являются fixtures и не коммитятся. Канонические demo fixtures меняются
через generator/sync scripts с проверкой детерминированного diff.
