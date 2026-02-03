# Pydantic Alias Serialization — Критическая ошибка и её решение

**Версия:** 1.0  
**Дата:** 2026-02-03  
**Статус:** решено  
**Категория:** backend, Pydantic, SSE events, simulator

---

## Краткое описание проблемы

Визуализация клиринга в Simulator UI не работала, несмотря на наличие кода для FX-эффектов. События `clearing.plan` и `clearing.done` отправлялись с backend, но фронтенд не мог распарсить edge-референсы.

**Причина:** Pydantic v2 сериализовал поле `from_` как `"from_"` вместо alias `"from"`, потому что `model_dump(mode="json")` по умолчанию НЕ использует alias.

---

## Техническая суть

### Модель с alias

```python
# app/schemas/simulator.py
class SimulatorEventEdgeRef(BaseModel):
    from_: str = Field(alias="from")  # Python: from_, JSON alias: from
    to: str
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
```

### Неправильная сериализация (было)

```python
# app/core/simulator/real_runner.py
plan_evt = SimulatorClearingPlanEvent(...).model_dump(mode="json")
# Результат: {"highlight_edges": [{"from_": "A", "to": "B"}]}
#                                   ^^^^^^ НЕПРАВИЛЬНО
```

### Правильная сериализация (стало)

```python
plan_evt = SimulatorClearingPlanEvent(...).model_dump(mode="json", by_alias=True)
# Результат: {"highlight_edges": [{"from": "A", "to": "B"}]}
#                                   ^^^^ ПРАВИЛЬНО
```

---

## Почему frontend не работал

В `simulator-ui/v2/src/api/normalizeSimulatorEvent.ts`:

```typescript
const from = asString(e.from)   // ищет ключ "from"
const to = asString(e.to)
if (!from || !to) continue       // from == null → edge отбрасывается!
```

Поскольку backend отправлял `"from_"`, а frontend искал `"from"`, все edge-референсы отбрасывались как невалидные.

---

## Особенности Pydantic v2

| Метод | `by_alias` по умолчанию | `serialize_by_alias` в config |
|-------|-------------------------|-------------------------------|
| `model_dump()` | `False` | **НЕ влияет** |
| `model_dump(mode="json")` | `False` | **НЕ влияет** |
| `model_dump_json()` | Зависит от config | **Влияет** |

**Важно:** `serialize_by_alias=True` в `model_config` влияет ТОЛЬКО на `model_dump_json()`, но НЕ на `model_dump()`.

### Проверка (тест)

```python
from pydantic import BaseModel, Field, ConfigDict

class EdgeRef(BaseModel):
    from_: str = Field(alias="from")
    to: str
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

edge = EdgeRef(**{"from": "A", "to": "B"})

print(edge.model_dump())                      # {'from_': 'A', 'to': 'B'} ❌
print(edge.model_dump(by_alias=True))         # {'from': 'A', 'to': 'B'}  ✅
print(edge.model_dump(mode="json"))           # {'from_': 'A', 'to': 'B'} ❌
print(edge.model_dump(mode="json", by_alias=True))  # {'from': 'A', 'to': 'B'}  ✅
print(edge.model_dump_json())                 # {"from":"A","to":"B"}     ✅
```

---

## Применённое исправление

### 1. Добавить `by_alias=True` во все вызовы `model_dump()`

Файлы:
- `app/core/simulator/real_runner.py` — clearing.plan, clearing.done
- `app/core/simulator/fixtures_runner.py` — tx.updated, clearing.plan, clearing.done

```python
# Было:
.model_dump(mode="json")

# Стало:
.model_dump(mode="json", by_alias=True)
```

### 2. Добавить `serialize_by_alias=True` в model_config (для защиты в будущем)

```python
class SimulatorEventEdgeRef(BaseModel):
    from_: str = Field(alias="from")
    to: str
    model_config = ConfigDict(
        extra="forbid", 
        populate_by_name=True, 
        serialize_by_alias=True  # ← добавлено
    )
```

---

## Чек-лист для code review

При работе с Pydantic моделями, содержащими `Field(alias=...)`:

- [ ] При сериализации через `model_dump()` ВСЕГДА указывать `by_alias=True`
- [ ] В `model_config` добавлять `serialize_by_alias=True` как страховку
- [ ] Проверить, что frontend ожидает alias-имена (не Python-имена)
- [ ] Написать unit-тест на сериализацию критичных событий

---

## Затронутые модели

| Модель | Поле с alias | Файл |
|--------|--------------|------|
| `SimulatorEventEdgeRef` | `from_` → `"from"` | `app/schemas/simulator.py` |
| `SimulatorTxUpdatedEventEdge` | `from_` → `"from"` | `app/schemas/simulator.py` |

---

## Как воспроизвести и проверить исправление

### 1. Запустить backend и проверить сериализацию

```powershell
.\.venv\Scripts\python.exe -c "
from app.schemas.simulator import SimulatorClearingPlanEvent
import json

evt = SimulatorClearingPlanEvent(
    event_id='1',
    ts='2025-01-01T00:00:00Z',
    type='clearing.plan',
    equivalent='UAH',
    plan_id='p1',
    steps=[{'at_ms': 0, 'highlight_edges': [{'from': 'A', 'to': 'B'}]}]
)

result = evt.model_dump(mode='json', by_alias=True)
print(json.dumps(result, indent=2))
"
```

Ожидаемый результат:
```json
{
  "highlight_edges": [
    {
      "from": "A",
      "to": "B"
    }
  ]
}
```

### 2. Запустить Simulator UI и проверить визуализацию

```
http://localhost:5176/?mode=real
```

При запуске симуляции каждые 25 тиков должен происходить клиринг с визуальными эффектами:
- Подсветка рёбер цикла (edge pulses)
- Искры (sparks) вдоль рёбер
- Всплески на узлах (node bursts)

---

## Связанные документы

- Pydantic v2 Model Config: https://docs.pydantic.dev/latest/api/config/
- Simulator Events Schema: `app/schemas/simulator.py`
- Frontend Event Normalization: `simulator-ui/v2/src/api/normalizeSimulatorEvent.ts`
- FX Rendering: `simulator-ui/v2/src/composables/useSimulatorApp.ts`
