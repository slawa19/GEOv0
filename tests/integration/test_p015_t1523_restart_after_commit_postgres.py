"""T1523 cell 8: process A commits a payment and dies before answering; process B is handed it.

THE GAP THIS CLOSES. The T1523 inventory's section 4 is one word long - "None". Nothing in the tree
kills a process that has committed a payment and replays the same request in another one. The
nearest cases are same-process: the clearing `connection_loss` boundary and a `pg_terminate_backend`
on a trust line. Both leave the Python process, its session and its caches alive, so what they show
is that ONE process can recover its own commit. The question a payer actually asks after a crash -
"my request was never answered; if I send it again, do I pay twice?" - was not measured at all.

HOW IT IS MEASURED. Two real OS processes run application code against this PostgreSQL test
database (`tests/integration/t1523_restart_child.py`):

* A runs the signed request with `PaymentEngine.commit` wrapped so that it awaits the real commit
  and then `os._exit(17)`s before returning to `PaymentService`. The patch is in the child, not in
  the application (the brief's stop condition 2).
* B runs the SAME signed request, unpatched. The signature is verified against the sender's
  persisted public key, so no server-side secret is shared between them.

THE PREMISES, asserted before the property, because each of them could silently fail and leave the
cell green:

1. A died at the patched point - exit code 17, and its stdout carries `COMMITTED-THEN-EXIT` and no
   `RESULT`, i.e. no response was ever produced;
2. the commit is durable - read from a THIRD connection after A is gone, with one COMPLETED
   envelope whose `effect_count` matches its entry rows and one debt of 10.00;
3. B is a different process (its pid differs from A's) and it reaches the same row.

Only then: B answers COMMITTED, and the triple - debts, `transactions` rows for that `tx_id`, and
the envelope with its entries - is byte-identical to the snapshot taken while A was dead.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from nacl.signing import SigningKey
from sqlalchemy import delete, select

from tests.debt_setup import purge_test_ledger

pytestmark = pytest.mark.postgres

_CHILD = Path(__file__).resolve().parent / "t1523_restart_child.py"
_REPO_ROOT = Path(__file__).resolve().parents[2]
EXIT_AFTER_COMMIT = 17


async def _effects(session, tx_id: str, equivalent_id) -> dict[str, object]:
    from app.db.journal_tables import debt_journal_entries, debt_operations
    from app.db.models.debt import Debt
    from app.db.models.transaction import Transaction

    debts = sorted(
        (str(debtor), str(creditor), str(amount))
        for debtor, creditor, amount in (
            await session.execute(
                select(Debt.debtor_id, Debt.creditor_id, Debt.amount).where(
                    Debt.equivalent_id == equivalent_id
                )
            )
        ).all()
    )
    transactions = sorted(
        (str(tx_uuid), str(state))
        for tx_uuid, state in (
            await session.execute(
                select(Transaction.id, Transaction.state).where(Transaction.tx_id == tx_id)
            )
        ).all()
    )
    envelopes = (
        await session.execute(
            select(
                debt_operations.c.id,
                debt_operations.c.state,
                debt_operations.c.effect_count,
            ).where(
                debt_operations.c.kind == "PAYMENT",
                debt_operations.c.identity == tx_id,
            )
        )
    ).all()
    entries: list[tuple[str, str]] = []
    for operation_id, _state, _count in envelopes:
        rows = (
            await session.execute(
                select(debt_journal_entries.c.effect, debt_journal_entries.c.delta).where(
                    debt_journal_entries.c.operation_id == operation_id
                )
            )
        ).all()
        entries.extend((str(effect), str(delta)) for effect, delta in rows)
    entries.sort()
    return {
        "debts": debts,
        "transactions": transactions,
        "envelopes": sorted((str(state), count) for _id, state, count in envelopes),
        "entries": entries,
    }


async def _run_child(mode: str, *, database_url: str, body_path: Path, sender_id: uuid.UUID):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(_CHILD),
        "--mode",
        mode,
        "--database-url",
        database_url,
        "--body",
        str(body_path),
        "--sender-id",
        str(sender_id),
        cwd=str(_REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # `wait_for` cancels the READ, not the process. Without this the child - which writes
        # payments against the same test database - keeps running while the test moves on to
        # cleanup, free to hold locks or commit after the assertions it was supposed to feed.
        # A timeout here already means something went wrong; the point of killing it is that
        # the NEXT test still measures what it thinks it measures.
        process.kill()
        await process.wait()
        raise
    return (
        process.returncode,
        process.pid,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


@pytest.mark.asyncio
async def test_a_payment_committed_by_a_process_that_died_is_replayed_by_another_postgres(
    db_session, tmp_path
):
    from app.core.auth.canonical import canonical_json
    from app.core.auth.crypto import generate_keypair, get_pid_from_public_key
    from app.db.models.audit_log import IntegrityAuditLog
    from app.db.models.equivalent import Equivalent
    from app.db.models.participant import Participant
    from app.db.models.prepare_lock import PrepareLock
    from app.db.models.transaction import Transaction
    from app.db.models.trustline import TrustLine
    from tests.conftest import TEST_DATABASE_URL, TestingSessionLocal

    if not TEST_DATABASE_URL.startswith("postgresql"):
        pytest.skip("Cell 8 needs two processes on one PostgreSQL database")

    nonce = uuid.uuid4().hex[:10]
    equivalent_id = uuid.uuid4()
    sender_id = uuid.uuid4()
    receiver_id = uuid.uuid4()
    equivalent_code = f"P8{nonce}".upper()[:16]
    tx_id = str(uuid.uuid4())

    sender_public, sender_private = generate_keypair()
    receiver_public, _receiver_private = generate_keypair()
    sender_pid = get_pid_from_public_key(sender_public)
    receiver_pid = get_pid_from_public_key(receiver_public)

    try:
        # Seeded on its own connection and really committed: two other processes have to see it.
        async with TestingSessionLocal() as setup:
            setup.add_all(
                [
                    Equivalent(
                        id=equivalent_id,
                        code=equivalent_code,
                        description="T1523 cell 8",
                        precision=2,
                    ),
                    Participant(
                        id=sender_id,
                        pid=sender_pid,
                        display_name="A",
                        public_key=sender_public,
                        type="person",
                        status="active",
                    ),
                    Participant(
                        id=receiver_id,
                        pid=receiver_pid,
                        display_name="B",
                        public_key=receiver_public,
                        type="person",
                        status="active",
                    ),
                ]
            )
            await setup.commit()
            setup.add(
                TrustLine(
                    from_participant_id=receiver_id,
                    to_participant_id=sender_id,
                    equivalent_id=equivalent_id,
                    limit=Decimal("100.00"),
                    status="active",
                )
            )
            await setup.commit()

        message = canonical_json(
            {
                "tx_id": tx_id,
                "to": receiver_pid,
                "equivalent": equivalent_code,
                "amount": "10.00",
            }
        )
        body = {
            "tx_id": tx_id,
            "to": receiver_pid,
            "equivalent": equivalent_code,
            "amount": "10.00",
            "signature": base64.b64encode(
                SigningKey(base64.b64decode(sender_private)).sign(message).signature
            ).decode("utf-8"),
        }
        body_path = tmp_path / "t1523_cell8_body.json"
        body_path.write_text(json.dumps(body), encoding="utf-8")

        # --- process A: commits, then dies before answering -----------------------------------
        code_a, pid_a, stdout_a, stderr_a = await _run_child(
            "die-after-commit",
            database_url=TEST_DATABASE_URL,
            body_path=body_path,
            sender_id=sender_id,
        )

        assert code_a == EXIT_AFTER_COMMIT, (
            f"process A exited {code_a}, not at the patched point.\n"
            f"stdout={stdout_a!r}\nstderr={stderr_a[-3000:]!r}"
        )
        assert f"COMMITTED-THEN-EXIT:{tx_id}" in stdout_a, stdout_a
        assert "RESULT:" not in stdout_a, (
            f"process A produced a response after all: {stdout_a!r}"
        )
        assert "ERROR:" not in stdout_a, stdout_a

        # --- the commit is durable, read from a third connection ------------------------------
        async with TestingSessionLocal() as observer:
            after_crash = await _effects(observer, tx_id, equivalent_id)
        assert len(after_crash["transactions"]) == 1, after_crash["transactions"]
        assert after_crash["transactions"][0][1] == "COMMITTED", after_crash["transactions"]
        assert after_crash["envelopes"] == [
            ("COMPLETED", len(after_crash["entries"]))
        ], after_crash
        assert len(after_crash["entries"]) > 0, after_crash
        assert len(after_crash["debts"]) == 1, after_crash["debts"]
        assert Decimal(after_crash["debts"][0][2]) == Decimal("10.00"), after_crash["debts"]

        # --- process B: a different process, the same signed request --------------------------
        code_b, pid_b, stdout_b, stderr_b = await _run_child(
            "answer",
            database_url=TEST_DATABASE_URL,
            body_path=body_path,
            sender_id=sender_id,
        )

        assert pid_b != pid_a, (pid_a, pid_b)
        assert code_b == 0, (
            f"process B exited {code_b}.\nstdout={stdout_b!r}\nstderr={stderr_b[-3000:]!r}"
        )
        result_lines = [
            line for line in stdout_b.splitlines() if line.startswith("RESULT:")
        ]
        assert len(result_lines) == 1, stdout_b
        answered = json.loads(result_lines[0][len("RESULT:") :])
        assert answered["tx_id"] == tx_id, answered
        assert answered["status"] == "COMMITTED", answered
        assert answered["amount"] == "10.00", answered

        async with TestingSessionLocal() as observer:
            after_replay = await _effects(observer, tx_id, equivalent_id)
        assert after_replay == after_crash, (
            "the replay in the new process moved debts, wrote a second transaction row or "
            f"opened a second envelope: {after_replay!r} != {after_crash!r}"
        )

        # The prepare lock of the dead process is gone with its commit - no leftovers for the
        # second process to trip over.
        async with TestingSessionLocal() as observer:
            leftovers = (
                await observer.execute(
                    select(PrepareLock.id).where(PrepareLock.tx_id == tx_id)
                )
            ).all()
        assert leftovers == [], leftovers
    finally:
        async with TestingSessionLocal() as cleanup:
            await purge_test_ledger(cleanup, equivalent_ids=[equivalent_id])
            await cleanup.execute(
                delete(IntegrityAuditLog).where(IntegrityAuditLog.tx_id == tx_id)
            )
            await cleanup.execute(delete(PrepareLock).where(PrepareLock.tx_id == tx_id))
            await cleanup.execute(delete(Transaction).where(Transaction.tx_id == tx_id))
            await cleanup.execute(
                delete(TrustLine).where(TrustLine.equivalent_id == equivalent_id)
            )
            await cleanup.execute(
                delete(Participant).where(Participant.id.in_([sender_id, receiver_id]))
            )
            await cleanup.execute(delete(Equivalent).where(Equivalent.id == equivalent_id))
            await cleanup.commit()
