#!/usr/bin/env python
"""Deterministic fixtures generator for GEO Hub Admin UI prototype.

Goal: produce sufficiently large, stable datasets for pagination and UI states.
No external dependencies.

Outputs:
- admin-fixtures/v1/datasets/*.json
- admin-fixtures/v1/api-snapshots/*.json (optional: precomputed pages)

Run:
  python admin-fixtures/tools/generate_admin_fixtures.py
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
V1_DIR = BASE_DIR / "v1"
DATASETS_DIR = V1_DIR / "datasets"
SNAPSHOTS_DIR = V1_DIR / "api-snapshots"

BASE_TS = datetime(2026, 1, 11, 0, 0, 0, tzinfo=timezone.utc)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _d(value: str | int | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q(value: Decimal, precision: int) -> str:
    quant = Decimal("1") if precision == 0 else Decimal("1").scaleb(-precision)
    return str(value.quantize(quant, rounding=ROUND_DOWN))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True)
class Participant:
    pid: str
    display_name: str
    type: str
    status: str


@dataclass(frozen=True)
class Equivalent:
    code: str
    precision: int
    description: str
    is_active: bool


@dataclass(frozen=True)
class Trustline:
    equivalent: str
    from_pid: str
    to_pid: str
    limit: str
    used: str
    available: str
    status: str
    created_at: str
    policy: dict[str, Any]
    from_display_name: str | None = None
    to_display_name: str | None = None


def generate_participants(total: int = 60) -> list[Participant]:
    # Keep the existing demo participants first (compatible with current repo seeds).
    base = [
        Participant("PID_ALICE_a1b2c3d4e5f6", "Alice (Test)", "person", "active"),
        Participant("PID_BOB_b2c3d4e5f6a1", "Bob (Test)", "person", "active"),
        Participant("PID_CAROL_c3d4e5f6a1b2", "Carol (Test)", "person", "active"),
        Participant("PID_DAVE_d4e5f6a1b2c3", "Dave (Test)", "person", "active"),
        Participant("PID_HUB_ADMIN_hub0admin1", "Hub Admin (Test)", "organization", "active"),
    ]

    statuses = ["active", "active", "active", "frozen", "banned"]
    types = ["person", "organization"]

    out = list(base)
    for i in range(len(base), total):
        idx = i - len(base) + 1
        pid = f"PID_U{idx:04d}_{(idx * 2654435761) % 2**32:08x}"
        t = types[idx % len(types)]
        status = statuses[idx % len(statuses)]
        name = f"{('Org' if t == 'organization' else 'User')} {idx:04d}"
        out.append(Participant(pid=pid, display_name=name, type=t, status=status))

    return out


def generate_equivalents() -> list[Equivalent]:
    return [
        Equivalent("UAH", 2, "Ukrainian Hryvnia", True),
        Equivalent("HOUR", 1, "Time-based unit (1 hour of work)", True),
        Equivalent("KWH", 2, "Kilowatt-hour (energy unit)", True),
        Equivalent("USD", 2, "US Dollar", True),
        Equivalent("EUR", 2, "Euro", True),
        Equivalent("POINT", 0, "Community points", True),
        Equivalent("TOK", 6, "Test token (6 decimals)", True),
        Equivalent("GAS", 3, "Fuel coupon", True),
        Equivalent("MEAL", 0, "Meal voucher", True),
        Equivalent("CO2", 3, "CO2 offset unit", False),
        Equivalent("MIN", 0, "Minutes (time)", False),
        Equivalent("SAT", 0, "Satoshi (display-only)", False),
    ]


def generate_trustlines(
    participants: list[Participant],
    equivalents: list[Equivalent],
    total: int = 140,
) -> list[Trustline]:
    # Deterministic graph-ish: edges over rolling windows of participants.
    active_equivs = [e for e in equivalents if e.is_active]

    pid_to_name = {p.pid: p.display_name for p in participants}

    out: list[Trustline] = []
    for i in range(total):
        eq = active_equivs[i % len(active_equivs)]
        a = participants[(i * 7) % len(participants)]
        b = participants[(i * 7 + 13) % len(participants)]
        if a.pid == b.pid:
            b = participants[(i * 7 + 17) % len(participants)]

        # Make a range of limits and usage. Include edge-cases:
        # - some limit==0
        # - some available/limit < 0.10
        base_limit = Decimal(100 + (i % 20) * 50)
        limit = base_limit
        if i % 37 == 0:
            limit = Decimal(0)

        # used varies; ensure used <= limit where limit>0
        if limit == 0:
            used = Decimal(0)
        else:
            ratio = Decimal((i % 23) + 1) / Decimal(30)
            used = (limit * ratio).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN)

        # Force "narrow bottleneck" cases (~8%)
        if limit != 0 and i % 12 == 0:
            used = (limit * Decimal("0.92")).quantize(Decimal("0.0000001"), rounding=ROUND_DOWN)

        available = (limit - used) if limit != 0 else Decimal(0)

        status = "active"
        if i % 11 == 0:
            status = "frozen"
        elif i % 29 == 0:
            status = "closed"

        created_at = _iso(BASE_TS - timedelta(days=(i % 90), minutes=i * 17))

        out.append(
            Trustline(
                equivalent=eq.code,
                from_pid=a.pid,
                to_pid=b.pid,
                limit=_q(limit, eq.precision),
                used=_q(used, eq.precision),
                available=_q(available, eq.precision),
                status=status,
                created_at=created_at,
                policy={
                    "auto_clearing": (i % 2 == 0),
                    "can_be_intermediate": (i % 3 != 0),
                },
                from_display_name=pid_to_name.get(a.pid),
                to_display_name=pid_to_name.get(b.pid),
            )
        )

    return out


def _paginate(items: list[dict[str, Any]], page: int, per_page: int) -> dict[str, Any]:
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "items": items[start:end],
        "page": page,
        "per_page": per_page,
        "total": total,
    }


def generate_audit_log(total: int = 180) -> list[dict[str, Any]]:
    actions = [
        ("CONFIG_PATCH", "config"),
        ("FEATURE_FLAG_SET", "feature_flag"),
        ("PARTICIPANT_FREEZE", "participant"),
        ("PARTICIPANT_UNFREEZE", "participant"),
        ("EQUIVALENT_UPSERT", "equivalent"),
        ("TX_ABORT", "transaction"),
    ]

    actors = [
        {"actor_id": "admin:root", "actor_role": "admin"},
        {"actor_id": "operator:ops-1", "actor_role": "operator"},
        {"actor_id": "auditor:audit-1", "actor_role": "auditor"},
    ]

    out: list[dict[str, Any]] = []
    for i in range(total):
        action, obj = actions[i % len(actions)]
        actor = actors[i % len(actors)]

        ts = BASE_TS - timedelta(minutes=i * 7)

        object_id = None
        if obj == "config":
            object_id = "routing.max_paths_per_payment"
        elif obj == "feature_flag":
            object_id = "feature_flags.multipath_enabled"
        elif obj == "participant":
            object_id = f"PID_U{(i % 55) + 1:04d}_{(((i % 55) + 1) * 2654435761) % 2**32:08x}"
        elif obj == "equivalent":
            object_id = ["UAH", "USD", "POINT", "TOK", "CO2"][i % 5]
        elif obj == "transaction":
            object_id = f"TX_{(i * 104729) % 10**8:08d}"

        before_state = None
        after_state = None
        reason = None

        if action == "CONFIG_PATCH":
            before_state = {"routing.max_paths_per_payment": 4}
            after_state = {"routing.max_paths_per_payment": 6 if i % 2 == 0 else 5}
        elif action == "FEATURE_FLAG_SET":
            before_state = {"multipath_enabled": (i % 2 != 0)}
            after_state = {"multipath_enabled": (i % 2 == 0)}
        elif action in ("PARTICIPANT_FREEZE", "PARTICIPANT_UNFREEZE"):
            reason = "операционное обслуживание" if action == "PARTICIPANT_FREEZE" else "восстановление"
            before_state = {"status": "active" if action == "PARTICIPANT_FREEZE" else "frozen"}
            after_state = {"status": "frozen" if action == "PARTICIPANT_FREEZE" else "active"}
        elif action == "EQUIVALENT_UPSERT":
            before_state = {"is_active": True}
            after_state = {"is_active": True, "description": "updated"}
        elif action == "TX_ABORT":
            reason = "stuck tx unblock"
            before_state = {"state": "PREPARE_IN_PROGRESS"}
            after_state = {"state": "ABORTED"}

        out.append(
            {
                "id": f"audit_{i+1:04d}",
                "timestamp": _iso(ts),
                **actor,
                "action": action,
                "object_type": obj,
                "object_id": object_id,
                "reason": reason,
                "before_state": before_state,
                "after_state": after_state,
                "request_id": f"req_{(i * 99991) % 10**8:08d}",
                "ip_address": f"10.0.{(i % 50) + 1}.{(i % 200) + 10}",
            }
        )

    return out


def generate_config() -> dict[str, Any]:
    return {
        "feature_flags.multipath_enabled": True,
        "feature_flags.full_multipath_enabled": False,
        "clearing.enabled": True,
        "routing.max_paths_per_payment": 6,
        "routing.multipath_mode": "auto",
        "clearing.trigger_cycles_max_length": 6,
        "limits.default_trustline_limit": "1000.00",
        "observability.log_level": "INFO",
    }


def generate_feature_flags() -> dict[str, Any]:
    return {
        "multipath_enabled": True,
        "full_multipath_enabled": False,
        "clearing_enabled": True,
    }


def generate_integrity_status() -> dict[str, Any]:
    checks = [
        {
            "name": "zero_sum_by_equivalent",
            "status": "ok",
            "last_check": _iso(BASE_TS - timedelta(minutes=18)),
            "details": {"checked_equivalents": 5},
        },
        {
            "name": "limits_consistency",
            "status": "warning",
            "last_check": _iso(BASE_TS - timedelta(minutes=33)),
            "details": {"warnings": 2},
        },
        {
            "name": "orphan_participants",
            "status": "ok",
            "last_check": _iso(BASE_TS - timedelta(minutes=45)),
            "details": {"count": 0},
        },
    ]

    last_checked_at = max((c.get("last_check") for c in checks if isinstance(c.get("last_check"), str)), default=_iso(BASE_TS))
    checks_failed = sum(1 for c in checks if c.get("status") in ("failed", "error", "critical"))
    checks_warn = any(c.get("status") == "warning" for c in checks)

    overall = "ok"
    if checks_failed > 0:
        overall = "failed"
    elif checks_warn:
        overall = "warning"

    return {
        "status": overall,
        "last_checked_at": last_checked_at,
        "checks_total": len(checks),
        "checks_failed": checks_failed,
        "checks": checks,
    }


def generate_incidents(total: int = 25) -> dict[str, Any]:
    # Minimal “stuck tx” representation suitable for UI lists.
    items: list[dict[str, Any]] = []
    for i in range(total):
        created_at = BASE_TS - timedelta(hours=(i % 72), minutes=i * 9)
        items.append(
            {
                "tx_id": f"TX_STUCK_{i+1:04d}",
                "state": "PREPARE_IN_PROGRESS",
                "initiator_pid": f"PID_U{(i % 55) + 1:04d}_{(((i % 55) + 1) * 2654435761) % 2**32:08x}",
                "equivalent": ["UAH", "USD", "POINT", "TOK"][i % 4],
                "age_seconds": int((BASE_TS - created_at).total_seconds()),
                "created_at": _iso(created_at),
                "sla_seconds": 1800,
            }
        )

    return {"items": items, "page": 1, "per_page": total, "total": total}


def generate_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "0.0.0-dev",
        "environment": "dev",
        "uptime_seconds": 123456,
        "timestamp": _iso(BASE_TS),
    }


def generate_health_db() -> dict[str, Any]:
    return {
        "status": "ok",
        "db": {"dialect": "postgresql", "reachable": True, "latency_ms": 12},
        "timestamp": _iso(BASE_TS),
    }


def generate_migrations() -> dict[str, Any]:
    return {
        "head": "0001_initial",
        "current": "0001_initial",
        "is_up_to_date": True,
        "timestamp": _iso(BASE_TS - timedelta(minutes=5)),
    }


def main() -> None:
    participants = generate_participants(total=60)
    equivalents = generate_equivalents()
    trustlines = generate_trustlines(participants, equivalents, total=140)
    audit_log = generate_audit_log(total=180)

    datasets = {
        "participants": [asdict(p) for p in participants],
        "equivalents": [asdict(e) for e in equivalents],
        "trustlines": [
            {
                "equivalent": t.equivalent,
                "from": t.from_pid,
                "to": t.to_pid,
                "from_display_name": t.from_display_name,
                "to_display_name": t.to_display_name,
                "limit": t.limit,
                "used": t.used,
                "available": t.available,
                "status": t.status,
                "created_at": t.created_at,
                "policy": t.policy,
            }
            for t in trustlines
        ],
        "audit_log": audit_log,
        "config": generate_config(),
        "feature_flags": generate_feature_flags(),
        "integrity_status": generate_integrity_status(),
        "incidents": generate_incidents(total=25),
        "health": generate_health(),
        "health_db": generate_health_db(),
        "migrations": generate_migrations(),
    }

    _write_json(DATASETS_DIR / "participants.json", datasets["participants"])
    _write_json(DATASETS_DIR / "equivalents.json", datasets["equivalents"])
    _write_json(DATASETS_DIR / "trustlines.json", datasets["trustlines"])
    _write_json(DATASETS_DIR / "audit-log.json", datasets["audit_log"])
    _write_json(DATASETS_DIR / "config.json", datasets["config"])
    _write_json(DATASETS_DIR / "feature-flags.json", datasets["feature_flags"])
    _write_json(DATASETS_DIR / "integrity-status.json", datasets["integrity_status"])
    _write_json(DATASETS_DIR / "incidents.json", datasets["incidents"])
    _write_json(DATASETS_DIR / "health.json", datasets["health"])
    _write_json(DATASETS_DIR / "health-db.json", datasets["health_db"])
    _write_json(DATASETS_DIR / "migrations.json", datasets["migrations"])

    # A few precomputed snapshots for quick prototypes.
    trust_items = datasets["trustlines"]
    audit_items = datasets["audit_log"]
    participant_items = datasets["participants"]

    _write_json(
        SNAPSHOTS_DIR / "health.get.json",
        {"success": True, "data": datasets["health"]},
    )
    _write_json(
        SNAPSHOTS_DIR / "health.db.get.json",
        {"success": True, "data": datasets["health_db"]},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.migrations.get.json",
        {"success": True, "data": datasets["migrations"]},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.config.get.json",
        {"success": True, "data": datasets["config"]},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.feature-flags.get.json",
        {"success": True, "data": datasets["feature_flags"]},
    )
    _write_json(
        SNAPSHOTS_DIR / "integrity.status.get.json",
        {"success": True, "data": datasets["integrity_status"]},
    )

    _write_json(
        SNAPSHOTS_DIR / "admin.participants.page1.per20.json",
        {"success": True, "data": _paginate(participant_items, page=1, per_page=20)},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.participants.page2.per20.json",
        {"success": True, "data": _paginate(participant_items, page=2, per_page=20)},
    )

    _write_json(
        SNAPSHOTS_DIR / "admin.trustlines.page1.per20.json",
        {"success": True, "data": _paginate(trust_items, page=1, per_page=20)},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.trustlines.page2.per20.json",
        {"success": True, "data": _paginate(trust_items, page=2, per_page=20)},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.audit-log.page1.per20.json",
        {"success": True, "data": _paginate(audit_items, page=1, per_page=20)},
    )
    _write_json(
        SNAPSHOTS_DIR / "admin.audit-log.page2.per20.json",
        {"success": True, "data": _paginate(audit_items, page=2, per_page=20)},
    )

    incident_items = datasets["incidents"]["items"]
    _write_json(
        SNAPSHOTS_DIR / "admin.incidents.page1.per20.json",
        {"success": True, "data": _paginate(incident_items, page=1, per_page=20)},
    )

    meta = {
        "version": "v1",
        "generated_at": _iso(BASE_TS),
        "counts": {
            "participants": len(participants),
            "equivalents": len(equivalents),
            "trustlines": len(trustlines),
            "audit_log": len(audit_log),
            "incidents": datasets["incidents"]["total"],
        },
        "notes": [
            "All amounts are decimal strings.",
            "Timestamps are fixed relative to 2026-01-11Z.",
        ],
    }
    _write_json(V1_DIR / "_meta.json", meta)

    print("Generated admin fixtures:")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
