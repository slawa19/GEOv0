"""PID generation: base58(sha256(public_key))

Revision ID: 008
Revises: 007
Create Date: 2026-01-06

Recomputes participants.pid to match protocol spec, and updates PID string references in:
- auth_challenges.pid
- transactions.payload JSON fields (from/to/routes.path)
"""

from __future__ import annotations

import base64
import hashlib
from typing import Sequence, Union

import base58
from alembic import op
import sqlalchemy as sa
from nacl.encoding import Base64Encoder
from nacl.signing import VerifyKey


revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _pid_v0_base64url(public_key_b64: str) -> str:
    VerifyKey(public_key_b64, encoder=Base64Encoder)
    raw = base64.b64decode(public_key_b64)
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _pid_v1_base58_sha256(public_key_b64: str) -> str:
    VerifyKey(public_key_b64, encoder=Base64Encoder)
    raw = base64.b64decode(public_key_b64)
    return base58.b58encode(hashlib.sha256(raw).digest()).decode("utf-8")


def _migrate_pid_strings(mapping: dict[str, str], payload: object) -> object:
    if not isinstance(payload, dict):
        return payload

    updated = dict(payload)

    for key in ("from", "to"):
        value = updated.get(key)
        if isinstance(value, str) and value in mapping:
            updated[key] = mapping[value]

    routes = updated.get("routes")
    if isinstance(routes, list):
        new_routes = []
        for route in routes:
            if not isinstance(route, dict):
                new_routes.append(route)
                continue
            new_route = dict(route)
            path = new_route.get("path")
            if isinstance(path, list):
                new_route["path"] = [mapping.get(p, p) if isinstance(p, str) else p for p in path]
            new_routes.append(new_route)
        updated["routes"] = new_routes

    return updated


def _recompute_pids(connection: sa.Connection, *, derive_new_pid) -> dict[str, str]:
    participants = connection.execute(sa.text("SELECT id, pid, public_key FROM participants")).fetchall()
    mapping: dict[str, str] = {}
    new_pids: set[str] = set()

    for row in participants:
        old_pid = row.pid
        new_pid = derive_new_pid(row.public_key)
        mapping[old_pid] = new_pid
        if new_pid in new_pids:
            raise RuntimeError("PID migration produced duplicate PID values")
        new_pids.add(new_pid)

    # Two-phase update to avoid accidental unique conflicts.
    for row in participants:
        tmp_pid = f"migrating:{str(row.id)}"
        connection.execute(
            sa.text("UPDATE participants SET pid = :pid WHERE id = :id"),
            {"pid": tmp_pid, "id": row.id},
        )

    for row in participants:
        connection.execute(
            sa.text("UPDATE participants SET pid = :pid WHERE id = :id"),
            {"pid": mapping[row.pid], "id": row.id},
        )

    return mapping


def upgrade() -> None:
    bind = op.get_bind()
    if bind is None:
        raise RuntimeError("No DB bind available")

    # Build mapping from current pid -> new pid, then update participants.pid.
    # NOTE: mapping keys are old PIDs as stored before this migration.
    participants = bind.execute(sa.text("SELECT id, pid, public_key FROM participants")).fetchall()
    old_to_new: dict[str, str] = {}
    new_pids: set[str] = set()
    for row in participants:
        new_pid = _pid_v1_base58_sha256(row.public_key)
        old_to_new[row.pid] = new_pid
        if new_pid in new_pids:
            raise RuntimeError("PID migration produced duplicate PID values")
        new_pids.add(new_pid)

    # Two-phase update for participants.pid to avoid unique collisions.
    for row in participants:
        tmp_pid = f"migrating:{str(row.id)}"
        bind.execute(sa.text("UPDATE participants SET pid = :pid WHERE id = :id"), {"pid": tmp_pid, "id": row.id})
    for row in participants:
        bind.execute(sa.text("UPDATE participants SET pid = :pid WHERE id = :id"), {"pid": old_to_new[row.pid], "id": row.id})

    # Update auth_challenges.pid.
    challenges = bind.execute(sa.text("SELECT id, pid FROM auth_challenges")).fetchall()
    for row in challenges:
        new_pid = old_to_new.get(row.pid)
        if new_pid is None:
            continue
        bind.execute(sa.text("UPDATE auth_challenges SET pid = :pid WHERE id = :id"), {"pid": new_pid, "id": row.id})

    # Update transactions.payload PID strings for history/filtering continuity.
    update_tx_payload = sa.text("UPDATE transactions SET payload = :payload WHERE id = :id").bindparams(
        sa.bindparam("payload", type_=sa.JSON()),
        sa.bindparam("id"),
    )

    tx_rows = bind.execute(sa.text("SELECT id, payload FROM transactions")).fetchall()
    for row in tx_rows:
        new_payload = _migrate_pid_strings(old_to_new, row.payload)
        if new_payload != row.payload:
            bind.execute(update_tx_payload, {"payload": new_payload, "id": row.id})


def downgrade() -> None:
    bind = op.get_bind()
    if bind is None:
        raise RuntimeError("No DB bind available")

    # Build mapping from current (v1) pid -> old (v0) pid based on stored public_key.
    participants = bind.execute(sa.text("SELECT id, pid, public_key FROM participants")).fetchall()
    v1_to_v0: dict[str, str] = {}
    old_pids: set[str] = set()
    for row in participants:
        old_pid = _pid_v0_base64url(row.public_key)
        v1_to_v0[row.pid] = old_pid
        if old_pid in old_pids:
            raise RuntimeError("PID downgrade produced duplicate PID values")
        old_pids.add(old_pid)

    # Two-phase update for participants.pid.
    for row in participants:
        tmp_pid = f"migrating:{str(row.id)}"
        bind.execute(sa.text("UPDATE participants SET pid = :pid WHERE id = :id"), {"pid": tmp_pid, "id": row.id})
    for row in participants:
        bind.execute(sa.text("UPDATE participants SET pid = :pid WHERE id = :id"), {"pid": v1_to_v0[row.pid], "id": row.id})

    # Update auth_challenges.pid.
    challenges = bind.execute(sa.text("SELECT id, pid FROM auth_challenges")).fetchall()
    for row in challenges:
        new_pid = v1_to_v0.get(row.pid)
        if new_pid is None:
            continue
        bind.execute(sa.text("UPDATE auth_challenges SET pid = :pid WHERE id = :id"), {"pid": new_pid, "id": row.id})

    # Update transactions.payload.
    update_tx_payload = sa.text("UPDATE transactions SET payload = :payload WHERE id = :id").bindparams(
        sa.bindparam("payload", type_=sa.JSON()),
        sa.bindparam("id"),
    )

    tx_rows = bind.execute(sa.text("SELECT id, payload FROM transactions")).fetchall()
    for row in tx_rows:
        new_payload = _migrate_pid_strings(v1_to_v0, row.payload)
        if new_payload != row.payload:
            bind.execute(update_tx_payload, {"payload": new_payload, "id": row.id})
