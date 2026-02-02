import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select

# Добавляем корень проекта в путь поиска
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import get_db_session
from app.db.models import AuditLog, Debt, Equivalent, Participant, Transaction, TrustLine
from app.utils.validation import validate_equivalent_code, validate_equivalent_metadata


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    v = str(s).strip()
    if not v:
        return None
    # Accept ISO with Z.
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _public_key_for_pid(pid: str) -> str:
    # Participant.public_key requires 64 chars and uniqueness.
    return hashlib.sha256(pid.encode("utf-8")).hexdigest()


def _map_participant_status_to_db(status: str | None) -> str:
    v = str(status or "").strip().lower()
    if v in ("", "none"):
        return "active"
    if v == "frozen":
        return "suspended"
    if v == "banned":
        return "deleted"
    if v in ("active", "suspended", "left", "deleted"):
        return v
    return "active"


def _default_equivalent_metadata(code: str) -> dict[str, Any]:
    c = str(code or "").strip().upper()
    if c == "HOUR":
        return {"type": "time"}
    if len(c) == 3 and c.isalpha():
        # Good enough heuristic for demo data.
        return {"type": "fiat", "iso_code": c}
    return {"type": "custom"}


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_json_optional(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    return _load_json(path)


def _iter_slice(items: list[Any], max_items: int | None) -> Iterable[Any]:
    if max_items is None:
        return items
    return items[: max(0, int(max_items))]


async def _seed_from_seeds_dir(repo_root: str) -> None:
    """Legacy seeding from seeds/*.json (small, iterative)."""
    seeds_dir = os.path.join(repo_root, "seeds")
    equivalents_path = os.path.join(seeds_dir, "equivalents.json")
    participants_path = os.path.join(seeds_dir, "participants.json")
    trustlines_path = os.path.join(seeds_dir, "trustlines.json")

    async for session in get_db_session():
        print("Starting seeding process (source=seeds)...")
        try:
            if os.path.exists(equivalents_path):
                equivalents_data = _load_json(equivalents_path)
                for eq_data in equivalents_data:
                    validate_equivalent_code(eq_data.get("code"))

                    if "metadata" in eq_data and "metadata_" not in eq_data:
                        eq_data["metadata_"] = eq_data.pop("metadata")
                    eq_data["metadata_"] = validate_equivalent_metadata(eq_data.get("metadata_"))

                    existing = (
                        await session.execute(select(Equivalent).where(Equivalent.code == eq_data["code"]))
                    ).scalar_one_or_none()
                    if not existing:
                        session.add(Equivalent(**eq_data))
                await session.flush()

            if os.path.exists(participants_path):
                participants_data = _load_json(participants_path)
                for p_data in participants_data:
                    if isinstance(p_data, dict):
                        p_data = {k: v for k, v in p_data.items() if not str(k).startswith("_")}

                    existing = (
                        await session.execute(select(Participant).where(Participant.pid == p_data["pid"]))
                    ).scalar_one_or_none()
                    if not existing:
                        session.add(Participant(**p_data))
                await session.flush()

            if os.path.exists(trustlines_path):
                trustlines_data = _load_json(trustlines_path)
                for tl_data in trustlines_data:
                    if isinstance(tl_data, dict):
                        tl_data = {k: v for k, v in tl_data.items() if not str(k).startswith("_")}

                    from_pid = tl_data.pop("from_pid", None) or tl_data.pop("from")
                    to_pid = tl_data.pop("to_pid", None) or tl_data.pop("to")
                    eq_code = tl_data.pop("equivalent_code", None) or tl_data.pop("equivalent")

                    p_from = (
                        await session.execute(select(Participant).where(Participant.pid == from_pid))
                    ).scalar_one_or_none()
                    p_to = (
                        await session.execute(select(Participant).where(Participant.pid == to_pid))
                    ).scalar_one_or_none()
                    eq = (
                        await session.execute(select(Equivalent).where(Equivalent.code == eq_code))
                    ).scalar_one_or_none()

                    if not (p_from and p_to and eq):
                        continue

                    existing = (
                        await session.execute(
                            select(TrustLine).where(
                                TrustLine.from_participant_id == p_from.id,
                                TrustLine.to_participant_id == p_to.id,
                                TrustLine.equivalent_id == eq.id,
                            )
                        )
                    ).scalar_one_or_none()

                    if not existing:
                        session.add(
                            TrustLine(
                                from_participant_id=p_from.id,
                                to_participant_id=p_to.id,
                                equivalent_id=eq.id,
                                **tl_data,
                            )
                        )

            await session.commit()
            print("Seeding completed successfully.")
        except Exception as e:
            await session.rollback()
            print(f"Seeding failed: {e}")
            raise
        break


async def _seed_from_admin_fixtures(repo_root: str, *, max_transactions: int = 500, max_audit: int = 500) -> None:
    """Seed SQLite DB using canonical admin fixtures datasets.

    This makes real-mode Admin UI look like the fixtures-first prototype (rich demo data).
    """
    datasets_dir = os.path.join(repo_root, "admin-fixtures", "v1", "datasets")
    await _seed_from_admin_fixtures_datasets(
        datasets_dir,
        label="repo:admin-fixtures/v1",
        max_transactions=max_transactions,
        max_audit=max_audit,
    )


def _fixture_pack_out_v1(repo_root: str, seed_id: str) -> str:
    return os.path.join(repo_root, ".local-run", "fixture-packs", seed_id, "v1")


def _ensure_fixture_pack(repo_root: str, *, seed_id: str, regenerate: bool) -> str:
    """Generate a fixture pack into .local-run (avoids mutating tracked admin-fixtures/v1).

    Returns the datasets directory path.
    """

    generators: dict[str, str] = {
        "greenfield-village-100": os.path.join(
            repo_root, "admin-fixtures", "tools", "generate_seed_greenfield_village_100.py"
        ),
        "riverside-town-50": os.path.join(
            repo_root, "admin-fixtures", "tools", "generate_seed_riverside_town_50.py"
        ),
        "greenfield-village-100-v2": os.path.join(
            repo_root, "admin-fixtures", "tools", "generate_seed_greenfield_village_100_v2.py"
        ),
        "riverside-town-50-v2": os.path.join(
            repo_root, "admin-fixtures", "tools", "generate_seed_riverside_town_50_v2.py"
        ),
    }

    if seed_id not in generators:
        raise ValueError(f"Unknown community seed_id: {seed_id}")

    out_v1 = _fixture_pack_out_v1(repo_root, seed_id)
    meta_path = os.path.join(out_v1, "_meta.json")
    datasets_dir = os.path.join(out_v1, "datasets")

    if (not regenerate) and os.path.isfile(meta_path) and os.path.isdir(datasets_dir):
        return datasets_dir

    os.makedirs(datasets_dir, exist_ok=True)

    script = generators[seed_id]
    if not os.path.isfile(script):
        raise RuntimeError(f"Fixture generator not found: {script}")

    cmd = [sys.executable, script, "--out-v1", out_v1]
    print(f"Generating fixture pack: seed_id={seed_id} -> {out_v1}")
    proc = subprocess.run(cmd, cwd=repo_root)
    if proc.returncode != 0:
        raise RuntimeError(f"Fixture generator failed with exit code {proc.returncode}: {' '.join(cmd)}")
    if not os.path.isdir(datasets_dir):
        raise RuntimeError(f"Generated datasets dir not found: {datasets_dir}")
    return datasets_dir


async def _seed_from_admin_fixtures_datasets(
    datasets_dir: str,
    *,
    label: str,
    max_transactions: int = 500,
    max_audit: int = 500,
) -> None:
    """Seed SQLite DB using a fixture pack datasets directory."""
    if not os.path.isdir(datasets_dir):
        raise RuntimeError(f"Fixtures datasets dir not found: {datasets_dir}")

    equivalents_data = _load_json(os.path.join(datasets_dir, "equivalents.json"))
    participants_data = _load_json(os.path.join(datasets_dir, "participants.json"))
    trustlines_data = _load_json(os.path.join(datasets_dir, "trustlines.json"))
    debts_data = _load_json(os.path.join(datasets_dir, "debts.json"))
    # Some fixture packs may not include transactions.
    transactions_data = _load_json_optional(os.path.join(datasets_dir, "transactions.json"), [])
    audit_data = _load_json_optional(os.path.join(datasets_dir, "audit-log.json"), [])

    async for session in get_db_session():
        print(f"Starting seeding process (source=fixtures, pack={label})...")
        try:
            # --- Equivalents ---
            existing_eq_codes = set((await session.execute(select(Equivalent.code))).scalars().all())
            eq_models: list[Equivalent] = []
            for item in equivalents_data or []:
                code = str(item.get("code") or "").strip().upper()
                validate_equivalent_code(code)

                if code in existing_eq_codes:
                    continue

                raw_meta = item.get("metadata") or item.get("metadata_")
                if raw_meta is None:
                    raw_meta = _default_equivalent_metadata(code)
                eq_models.append(
                    Equivalent(
                        code=code,
                        symbol=item.get("symbol"),
                        description=item.get("description"),
                        precision=int(item.get("precision") or 2),
                        metadata_=validate_equivalent_metadata(raw_meta),
                        is_active=bool(item.get("is_active", True)),
                    )
                )
            session.add_all(eq_models)
            await session.flush()

            eq_by_code: dict[str, Equivalent] = {
                e.code: e for e in (await session.execute(select(Equivalent))).scalars().all()
            }

            # --- Participants ---
            existing_pids = set((await session.execute(select(Participant.pid))).scalars().all())
            p_models: list[Participant] = []
            for item in participants_data or []:
                pid = str(item.get("pid") or "").strip()
                if not pid:
                    continue

                if pid in existing_pids:
                    continue
                p_models.append(
                    Participant(
                        pid=pid,
                        display_name=str(item.get("display_name") or pid),
                        public_key=_public_key_for_pid(pid),
                        type=str(item.get("type") or "person"),
                        status=_map_participant_status_to_db(item.get("status")),
                        verification_level=int(item.get("verification_level") or 0),
                        profile=item.get("profile") or {},
                    )
                )
            session.add_all(p_models)
            await session.flush()

            p_by_pid: dict[str, Participant] = {
                p.pid: p for p in (await session.execute(select(Participant))).scalars().all()
            }

            # --- TrustLines ---
            existing_trustlines = set(
                (
                    await session.execute(
                        select(
                            TrustLine.from_participant_id,
                            TrustLine.to_participant_id,
                            TrustLine.equivalent_id,
                        )
                    )
                ).all()
            )
            tl_models: list[TrustLine] = []
            for item in trustlines_data or []:
                from_pid = str(item.get("from") or item.get("from_pid") or "").strip()
                to_pid = str(item.get("to") or item.get("to_pid") or "").strip()
                eq_code = str(item.get("equivalent") or item.get("equivalent_code") or "").strip().upper()
                if not (from_pid and to_pid and eq_code):
                    continue

                p_from = p_by_pid.get(from_pid)
                p_to = p_by_pid.get(to_pid)
                eq = eq_by_code.get(eq_code)
                if not (p_from and p_to and eq):
                    continue

                key = (p_from.id, p_to.id, eq.id)
                if key in existing_trustlines:
                    continue

                policy = item.get("policy") or {}
                if not isinstance(policy, dict):
                    policy = {}
                # Merge with defaults used by the model.
                policy = {
                    "auto_clearing": True,
                    "can_be_intermediate": True,
                    "max_hop_usage": None,
                    "daily_limit": None,
                    "blocked_participants": [],
                    **policy,
                }

                created_at = _parse_dt(item.get("created_at")) or datetime.now(timezone.utc)
                updated_at = _parse_dt(item.get("updated_at")) or created_at

                tl_models.append(
                    TrustLine(
                        from_participant_id=p_from.id,
                        to_participant_id=p_to.id,
                        equivalent_id=eq.id,
                        limit=Decimal(str(item.get("limit") or "0")),
                        status=str(item.get("status") or "active"),
                        policy=policy,
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )
            session.add_all(tl_models)
            await session.flush()

            # --- Debts ---
            existing_debts = set(
                (
                    await session.execute(
                        select(Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id)
                    )
                ).all()
            )
            debt_models: list[Debt] = []
            for item in debts_data or []:
                debtor_pid = str(item.get("debtor") or "").strip()
                creditor_pid = str(item.get("creditor") or "").strip()
                eq_code = str(item.get("equivalent") or "").strip().upper()
                if not (debtor_pid and creditor_pid and eq_code):
                    continue

                debtor = p_by_pid.get(debtor_pid)
                creditor = p_by_pid.get(creditor_pid)
                eq = eq_by_code.get(eq_code)
                if not (debtor and creditor and eq):
                    continue

                amt = Decimal(str(item.get("amount") or "0"))
                if amt <= 0:
                    continue

                key = (debtor.id, creditor.id, eq.id)
                if key in existing_debts:
                    continue

                debt_models.append(
                    Debt(
                        debtor_id=debtor.id,
                        creditor_id=creditor.id,
                        equivalent_id=eq.id,
                        amount=amt,
                    )
                )
            session.add_all(debt_models)
            await session.flush()

            # --- Transactions (subset, plus a few "stuck" ones for Incidents dashboard) ---
            existing_tx_ids = set((await session.execute(select(Transaction.id))).scalars().all())
            tx_models: list[Transaction] = []
            for item in _iter_slice(transactions_data or [], max_transactions):
                tx_id = str(item.get("tx_id") or item.get("id") or "").strip()
                if not tx_id:
                    continue

                initiator_pid = str(item.get("initiator_pid") or "").strip()
                initiator = p_by_pid.get(initiator_pid)
                if not initiator:
                    continue

                created_at = _parse_dt(item.get("created_at")) or datetime.now(timezone.utc)
                updated_at = _parse_dt(item.get("updated_at")) or created_at

                try:
                    tx_uuid = uuid.UUID(str(item.get("id"))) if item.get("id") else uuid.uuid5(uuid.NAMESPACE_URL, f"tx:{tx_id}")
                except Exception:
                    tx_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"tx:{tx_id}")

                if tx_uuid in existing_tx_ids:
                    continue

                tx_models.append(
                    Transaction(
                        id=tx_uuid,
                        tx_id=tx_id,
                        idempotency_key=item.get("idempotency_key"),
                        type=str(item.get("type") or "PAYMENT"),
                        initiator_id=initiator.id,
                        payload=item.get("payload") or {},
                        signatures=item.get("signatures") or [],
                        state=str(item.get("state") or "NEW"),
                        error=item.get("error"),
                        created_at=created_at,
                        updated_at=updated_at,
                    )
                )

            # Add deterministic stuck transactions for /admin/incidents.
            # Do not hardcode PIDs (fixtures packs differ); pick stable PIDs from the pack.
            def _seed_stuck(tx_id: str, initiator_pid: str, equivalent: str, *, created: str, updated: str) -> None:
                initiator = p_by_pid.get(initiator_pid)
                eq_code = str(equivalent).strip().upper()
                if not initiator or eq_code not in eq_by_code:
                    return

                stuck_uuid = uuid.uuid5(uuid.NAMESPACE_URL, f"stuck:{tx_id}")
                if stuck_uuid in existing_tx_ids:
                    return

                tx_models.append(
                    Transaction(
                        id=stuck_uuid,
                        tx_id=tx_id,
                        idempotency_key=None,
                        type="PAYMENT",
                        initiator_id=initiator.id,
                        payload={
                            "from": initiator_pid,
                            "to": initiator_pid,
                            "amount": "1.00",
                            "equivalent": eq_code,
                            "routes": [],
                            "idempotency": None,
                        },
                        signatures=[],
                        state="PREPARE_IN_PROGRESS",
                        error=None,
                        created_at=_parse_dt(created),
                        updated_at=_parse_dt(updated),
                    )
                )

            pids_sorted = sorted(p_by_pid.keys())
            if pids_sorted:
                pid_a = pids_sorted[0]
                pid_b = pids_sorted[len(pids_sorted) // 2]
                pid_c = pids_sorted[-1]
                eq_codes = sorted(eq_by_code.keys())
                eq_a = eq_codes[0] if eq_codes else "UAH"
                eq_b = eq_codes[1] if len(eq_codes) > 1 else eq_a
                eq_c = eq_codes[2] if len(eq_codes) > 2 else eq_a

                _seed_stuck(
                    f"TX_STUCK_{label}_0001",
                    pid_a,
                    eq_a,
                    created="2026-01-10T21:10:00Z",
                    updated="2026-01-10T21:40:00Z",
                )
                _seed_stuck(
                    f"TX_STUCK_{label}_0002",
                    pid_b,
                    eq_b,
                    created="2026-01-10T23:30:00Z",
                    updated="2026-01-10T23:55:00Z",
                )
                _seed_stuck(
                    f"TX_STUCK_{label}_0003",
                    pid_c,
                    eq_c,
                    created="2026-01-10T20:55:00Z",
                    updated="2026-01-10T21:25:00Z",
                )

            session.add_all(tx_models)
            await session.flush()

            # --- Audit log (subset) ---
            existing_audit_ids = set((await session.execute(select(AuditLog.id))).scalars().all())
            al_models: list[AuditLog] = []
            for item in _iter_slice(audit_data or [], max_audit):
                id_raw = str(item.get("id") or "").strip() or str(uuid.uuid4())
                ts = _parse_dt(item.get("timestamp")) or datetime.now(timezone.utc)

                audit_id = uuid.uuid5(uuid.NAMESPACE_URL, f"audit:{id_raw}")
                if audit_id in existing_audit_ids:
                    continue

                actor_role = item.get("actor_role")
                actor_label = str(item.get("actor_id") or "").strip()
                if actor_label and actor_role:
                    actor_role = f"{actor_role} ({actor_label})"
                elif actor_label and not actor_role:
                    actor_role = actor_label

                al_models.append(
                    AuditLog(
                        id=audit_id,
                        timestamp=ts,
                        actor_id=None,
                        actor_role=actor_role,
                        action=str(item.get("action") or ""),
                        object_type=item.get("object_type"),
                        object_id=item.get("object_id"),
                        reason=item.get("reason"),
                        before_state=item.get("before_state"),
                        after_state=item.get("after_state"),
                        request_id=item.get("request_id"),
                        ip_address=item.get("ip_address"),
                        user_agent=item.get("user_agent"),
                    )
                )
            session.add_all(al_models)

            await session.commit()
            print(
                "Seeding completed successfully. "
                f"equivalents={len(eq_models)} participants={len(p_models)} trustlines={len(tl_models)} debts={len(debt_models)} "
                f"transactions={len(tx_models)} audit_log={len(al_models)}"
            )
        except Exception as e:
            await session.rollback()
            print(f"Seeding failed: {e}")
            raise
        break


async def main() -> None:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description="Seed GEOv0 DB with demo data")
    parser.add_argument(
        "--source",
        choices=["fixtures", "seeds"],
        default=None,
        help="Seed source: fixtures (admin-fixtures datasets) or seeds (legacy seeds/*.json)",
    )
    parser.add_argument(
        "--max-transactions",
        type=int,
        default=500,
        help="Max transactions to import from fixtures (default: 500)",
    )
    parser.add_argument(
        "--max-audit",
        type=int,
        default=500,
        help="Max audit-log entries to import from fixtures (default: 500)",
    )
    parser.add_argument(
        "--community",
        choices=[
            "greenfield-village-100",
            "riverside-town-50",
            "greenfield-village-100-v2",
            "riverside-town-50-v2",
        ],
        default=None,
        help=(
            "When --source fixtures, generate and seed from a specific community fixture pack into .local-run "
            "(does not modify tracked admin-fixtures/v1)."
        ),
    )
    parser.add_argument(
        "--regenerate-fixtures",
        action="store_true",
        help="Force regeneration of the selected --community fixture pack in .local-run.",
    )
    args = parser.parse_args()

    source = args.source
    if source is None:
        # Ergonomic default: if fixtures datasets exist, use them (best UI demo experience).
        datasets_dir = os.path.join(repo_root, "admin-fixtures", "v1", "datasets")
        source = "fixtures" if os.path.isdir(datasets_dir) else "seeds"

    if source == "fixtures":
        if args.community:
            datasets_dir = _ensure_fixture_pack(repo_root, seed_id=args.community, regenerate=bool(args.regenerate_fixtures))
            await _seed_from_admin_fixtures_datasets(
                datasets_dir,
                label=f"local:{args.community}",
                max_transactions=args.max_transactions,
                max_audit=args.max_audit,
            )
        else:
            await _seed_from_admin_fixtures(repo_root, max_transactions=args.max_transactions, max_audit=args.max_audit)
    else:
        await _seed_from_seeds_dir(repo_root)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())