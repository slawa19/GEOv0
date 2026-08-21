"""Shared deterministic generators for non-graph admin datasets.

These datasets back Admin UI pages like:
- /health, /health/db
- /admin/migrations
- /admin/integrity
- /admin/audit-log

They are intentionally simple and dependency-free.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from seedlib import Participant, iso as _iso, write_json as _write_json


def generate_health(*, base_ts: datetime) -> dict[str, Any]:
    return {
        "status": "ok",
        "version": "0.0.0-dev",
        "environment": "dev",
        "uptime_seconds": 123456,
        "timestamp": _iso(base_ts),
    }


def generate_health_db(*, base_ts: datetime) -> dict[str, Any]:
    return {
        "status": "ok",
        "db": {"dialect": "postgresql", "reachable": True, "latency_ms": 12},
        "timestamp": _iso(base_ts),
    }


def generate_migrations(*, base_ts: datetime) -> dict[str, Any]:
    return {
        "head_revision": "0001_initial",
        "current_revision": "0001_initial",
        "is_up_to_date": True,
        "timestamp": _iso(base_ts - timedelta(minutes=5)),
    }


def generate_feature_flags() -> dict[str, Any]:
    return {
        "multipath_enabled": True,
        "full_multipath_enabled": False,
        "clearing_enabled": True,
    }


def generate_config() -> dict[str, Any]:
    return {
        "LOG_LEVEL": "INFO",
        "RATE_LIMIT_ENABLED": True,
        "ROUTING_MAX_HOPS": 6,
        "ROUTING_MAX_PATHS": 3,
        "INTEGRITY_CHECKPOINT_ENABLED": True,
        "INTEGRITY_CHECKPOINT_INTERVAL_SECONDS": 300,
        "RECOVERY_ENABLED": True,
        "RECOVERY_INTERVAL_SECONDS": 60,
        "PAYMENT_TX_STUCK_TIMEOUT_SECONDS": 120,
        "FEATURE_FLAGS_MULTIPATH_ENABLED": True,
        "FEATURE_FLAGS_FULL_MULTIPATH_ENABLED": False,
        "CLEARING_ENABLED": True,
    }


def generate_integrity_status(*, base_ts: datetime) -> dict[str, Any]:
    # Align with live backend schema:
    # app/schemas/integrity.py::IntegrityStatusResponse
    # Fields: status, last_check, equivalents, alerts
    last_check = base_ts - timedelta(minutes=2)

    equivalents: dict[str, Any] = {}
    alerts: list[str] = []

    # Keep deterministic and compatible with canonical seeds.
    # NOTE: statuses are: healthy | warning | critical
    for code in ["UAH", "EUR", "HOUR"]:
        eq_status = "healthy"
        invariants: dict[str, Any] = {
            "zero_sum": {"passed": True, "value": "0"},
            "trust_limits": {"passed": True, "violations": 0},
            "debt_symmetry": {"passed": True, "violations": 0},
        }

        if code == "UAH":
            # One mild warning to let the UI show a non-healthy state.
            eq_status = "warning"
            invariants["debt_symmetry"] = {
                "passed": False,
                "violations": 2,
                "details": {"sample": [{"debtor": "PID_SAMPLE", "creditor": "PID_SAMPLE2", "amount": "1.00"}]},
            }
            alerts.append("Debt symmetry violations in UAH: 2")

        equivalents[code] = {
            "status": eq_status,
            "checksum": "",
            "last_verified": _iso(base_ts - timedelta(minutes=10)),
            "invariants": invariants,
        }

    overall_status = "healthy"
    if any(e.get("status") == "critical" for e in equivalents.values()):
        overall_status = "critical"
    elif any(e.get("status") == "warning" for e in equivalents.values()):
        overall_status = "warning"

    return {
        "status": overall_status,
        "last_check": _iso(last_check),
        "equivalents": equivalents,
        "alerts": alerts,
    }


def generate_audit_log(*, participants: list[Participant], base_ts: datetime, total: int = 180) -> list[dict[str, Any]]:
    actions = [
        ("admin.config.patch", "config"),
        ("admin.feature_flags.patch", "feature_flags"),
        ("admin.participants.freeze", "participant"),
        ("admin.participants.unfreeze", "participant"),
        ("admin.equivalents.patch", "equivalent"),
        ("admin.transactions.abort", "transaction"),
    ]

    actors = [
        {"actor_id": str(UUID(int=10_001, version=4)), "actor_role": "admin"},
        {"actor_id": str(UUID(int=10_002, version=4)), "actor_role": "operator"},
        {"actor_id": str(UUID(int=10_003, version=4)), "actor_role": "auditor"},
    ]

    pids = [p.pid for p in participants] or ["PID_U0001_00000000"]

    out: list[dict[str, Any]] = []
    for i in range(total):
        action, obj = actions[i % len(actions)]
        actor = actors[i % len(actors)]
        ts = base_ts - timedelta(minutes=i * 7)

        object_id = None
        if obj == "config":
            object_id = "ROUTING_MAX_PATHS"
        elif obj == "feature_flags":
            object_id = None
        elif obj == "participant":
            object_id = pids[(i * 17) % len(pids)]
        elif obj == "equivalent":
            object_id = ["UAH", "EUR", "HOUR"][i % 3]
        elif obj == "transaction":
            object_id = f"TX_{(i * 104729) % 10**8:08d}"

        before_state: dict[str, Any] | None = None
        after_state: dict[str, Any] | None = None
        reason = None
        if action == "admin.config.patch":
            before_state = {"ROUTING_MAX_PATHS": 2}
            after_state = {"ROUTING_MAX_PATHS": 3}
        elif action == "admin.feature_flags.patch":
            before_state = {"multipath_enabled": False}
            after_state = {"multipath_enabled": True}
        elif action == "admin.participants.freeze":
            reason = "operational maintenance"
            before_state = {"status": "active"}
            after_state = {"status": "suspended"}
        elif action == "admin.participants.unfreeze":
            reason = "maintenance complete"
            before_state = {"status": "suspended"}
            after_state = {"status": "active"}
        elif action == "admin.equivalents.patch":
            before_state = {"is_active": True}
            after_state = {"is_active": True, "description": "updated"}
        elif action == "admin.transactions.abort":
            reason = "stuck tx unblock"
            before_state = {"state": "PREPARE_IN_PROGRESS"}
            after_state = {"state": "ABORTED"}

        out.append(
            {
                "id": str(UUID(int=i + 1, version=4)),
                "timestamp": _iso(ts),
                "action": action,
                "object_type": obj,
                "object_id": object_id,
                **actor,
                "reason": reason,
                "before_state": before_state,
                "after_state": after_state,
                "request_id": f"req_{(i * 99991) % 10**8:08d}",
                "ip_address": f"10.0.{(i % 50) + 1}.{(i % 200) + 10}",
                "user_agent": "admin-fixtures/1",
            }
        )

    return out


def write_common_admin_datasets(*, datasets_dir, participants: list[Participant], base_ts: datetime) -> None:
    datasets_dir = datasets_dir  # Path-like
    _write_json(datasets_dir / "health.json", generate_health(base_ts=base_ts))
    _write_json(datasets_dir / "health-db.json", generate_health_db(base_ts=base_ts))
    _write_json(datasets_dir / "migrations.json", generate_migrations(base_ts=base_ts))
    _write_json(datasets_dir / "feature-flags.json", generate_feature_flags())
    _write_json(datasets_dir / "config.json", generate_config())
    _write_json(datasets_dir / "integrity-status.json", generate_integrity_status(base_ts=base_ts))
    _write_json(datasets_dir / "audit-log.json", generate_audit_log(participants=participants, base_ts=base_ts, total=180))
