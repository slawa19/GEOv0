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
        "clearing_enabled": True,
        "audit_log_enabled": True,
    }


def generate_config() -> dict[str, Any]:
    return {
        "routing": {"max_paths_per_payment": 6, "max_hops": 6},
        "clearing": {"max_cycle_len": 6},
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
        ("CONFIG_PATCH", "config"),
        ("FEATURE_FLAG_SET", "feature_flag"),
        ("PARTICIPANT_FREEZE", "participant"),
        ("PARTICIPANT_UNFREEZE", "participant"),
        ("TX_ABORT", "transaction"),
    ]

    actors = [
        {"actor_id": "admin:root", "actor_role": "admin"},
        {"actor_id": "operator:ops-1", "actor_role": "operator"},
        {"actor_id": "auditor:audit-1", "actor_role": "auditor"},
    ]

    pids = [p.pid for p in participants] or ["PID_U0001_00000000"]

    out: list[dict[str, Any]] = []
    for i in range(total):
        action, obj = actions[i % len(actions)]
        actor = actors[i % len(actors)]
        ts = base_ts - timedelta(minutes=i * 7)

        object_id = None
        if obj == "config":
            object_id = "routing.max_paths_per_payment"
        elif obj == "feature_flag":
            object_id = "multipath_enabled"
        elif obj == "participant":
            object_id = pids[(i * 17) % len(pids)]
        elif obj == "transaction":
            object_id = f"TX_{(i * 104729) % 10**8:08d}"

        out.append(
            {
                "id": f"AUD_{i+1:05d}",
                "action": action,
                "object": obj,
                "object_id": object_id,
                **actor,
                "created_at": _iso(ts),
                "details": {},
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
