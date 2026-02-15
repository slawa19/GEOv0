from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.db.models.debt import Debt
from app.db.models.equivalent import Equivalent
from app.db.models.participant import Participant
from app.db.models.trustline import TrustLine
from app.schemas.simulator import (
    SimulatorActionClearingCycle,
    SimulatorActionEdgeRef,
    SimulatorGraphLink,
    SimulatorGraphNode,
    SimulatorGraphSnapshot,
)
from app.utils.exceptions import RoutingException


@pytest.fixture
def interact_actions_enabled(monkeypatch):
    """Enable simulator interact actions and bypass run lifecycle checks."""

    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    # Avoid depending on in-memory runtime run registry.
    monkeypatch.setattr(simulator_module, "_require_run_accepts_actions_or_error", lambda _run_id: None)

    return simulator_module


async def _seed_alice_bob_uah(db_session):
    alice = Participant(
        pid="alice",
        display_name="Alice",
        public_key="A" * 64,
        type="person",
        status="active",
        profile={},
    )
    bob = Participant(
        pid="bob",
        display_name="Bob",
        public_key="B" * 64,
        type="person",
        status="active",
        profile={},
    )
    uah = Equivalent(code="UAH", precision=2, is_active=True)

    db_session.add_all([alice, bob, uah])
    await db_session.commit()
    return alice, bob, uah


def test_build_clearing_done_cycle_edges_payload_merges_cycles_stable_and_dedup() -> None:
    import app.api.v1.simulator as simulator_module

    executed = [
        SimulatorActionClearingCycle(
            cleared_amount="1",
            edges=[
                SimulatorActionEdgeRef(from_="B", to="C"),
                SimulatorActionEdgeRef(from_="A", to="B"),
            ],
        ),
        SimulatorActionClearingCycle(
            cleared_amount="2",
            edges=[
                SimulatorActionEdgeRef(from_="C", to="A"),
                # duplicate edge across cycles must be deduped
                SimulatorActionEdgeRef(from_="A", to="B"),
            ],
        ),
    ]

    payload = simulator_module._build_clearing_done_cycle_edges_payload(executed)
    assert payload == [
        {"from": "A", "to": "B"},
        {"from": "B", "to": "C"},
        {"from": "C", "to": "A"},
    ]


@pytest.mark.asyncio
async def test_action_trustline_create_happy_and_conflict(client, db_session, interact_actions_enabled):
    # Arrange
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act 1: happy path
    r1 = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "limit": "10",
            "client_action_id": "c_tl_create_1",
        },
    )

    # Assert 1
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert p1["ok"] is True
    assert isinstance(p1.get("trustline_id"), str) and p1["trustline_id"]
    assert p1["from_pid"] == "alice"
    assert p1["to_pid"] == "bob"
    assert p1["equivalent"] == "UAH"
    assert p1["limit"] == "10"

    # Act 2: error path (duplicate create)
    r2 = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "limit": "10",
            "client_action_id": "c_tl_create_2",
        },
    )
    assert r2.status_code == 409
    p2 = r2.json()
    assert p2.get("code") == "TRUSTLINE_EXISTS"


@pytest.mark.asyncio
async def test_action_trustline_create_schema_validation_is_invalid_request(
    client, db_session, interact_actions_enabled
):
    # Arrange
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act: missing required field `limit` -> RequestValidationError
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
        },
    )

    # Assert: simulator action envelope + INVALID_REQUEST
    assert r.status_code == 400
    payload = r.json()
    assert payload.get("code") == "INVALID_REQUEST"
    assert payload.get("message") == "Invalid request"
    assert isinstance(payload.get("details", {}).get("errors"), list)


@pytest.mark.asyncio
async def test_action_trustline_update_happy_and_used_exceeds_new_limit(
    client, db_session, interact_actions_enabled
):
    # Arrange
    alice, bob, uah = await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Create trustline via endpoint
    rc = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "limit": "100",
        },
    )
    assert rc.status_code == 200, rc.text
    trustline_id = rc.json()["trustline_id"]

    # Act 1: happy update
    r1 = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-update",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "new_limit": "150",
            "client_action_id": "c_tl_update_1",
        },
    )
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert p1["ok"] is True
    assert p1["trustline_id"] == trustline_id
    # Stored trustline.limit is Numeric(20, 8) -> DB returns a scaled Decimal.
    assert p1["old_limit"] == "100.00000000"
    assert p1["new_limit"] == "150"

    # Arrange used debt (used amount is debt from `to` -> `from`)
    db_session.add(
        Debt(
            debtor_id=bob.id,
            creditor_id=alice.id,
            equivalent_id=uah.id,
            amount=Decimal("50"),
        )
    )
    await db_session.commit()

    # Act 2: error update (new_limit < used)
    r2 = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-update",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "new_limit": "40",
            "client_action_id": "c_tl_update_2",
        },
    )
    assert r2.status_code == 409
    p2 = r2.json()
    assert p2.get("code") == "USED_EXCEEDS_NEW_LIMIT"


@pytest.mark.asyncio
async def test_action_trustline_close_happy_and_has_debt(client, db_session, interact_actions_enabled):
    # Arrange
    alice, bob, uah = await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Create trustline via endpoint
    rc = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "limit": "10",
        },
    )
    assert rc.status_code == 200, rc.text
    trustline_id = rc.json()["trustline_id"]

    # Act 1: happy close (used=0)
    r1 = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-close",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "client_action_id": "c_tl_close_1",
        },
    )
    assert r1.status_code == 200, r1.text
    p1 = r1.json()
    assert p1["ok"] is True
    assert p1["trustline_id"] == trustline_id

    # Debt case: use a different equivalent code to avoid DB-level UNIQUE constraint
    # (trust_lines is unique on from/to/equivalent regardless of status).
    usd = Equivalent(code="USD", precision=2, is_active=True)
    db_session.add(usd)
    await db_session.commit()

    rc_usd = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "USD",
            "limit": "10",
        },
    )
    assert rc_usd.status_code == 200, rc_usd.text

    # Arrange debt
    db_session.add(
        Debt(
            debtor_id=bob.id,
            creditor_id=alice.id,
            equivalent_id=usd.id,
            amount=Decimal("1"),
        )
    )
    await db_session.commit()

    # Act 2: error close (used>0)
    r2 = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-close",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "USD",
            "client_action_id": "c_tl_close_2",
        },
    )
    assert r2.status_code == 409
    p2 = r2.json()
    assert p2.get("code") == "TRUSTLINE_HAS_DEBT"


@pytest.mark.asyncio
async def test_action_participants_list_is_run_scoped_snapshot_only(
    client, db_session, interact_actions_enabled, monkeypatch
):
    """participants-list must be run/snapshot-scoped (ignore global Participant table)."""

    import app.api.v1.simulator as simulator_module

    # Arrange: DB has an extra "foreign" participant not present in run snapshot.
    await _seed_alice_bob_uah(db_session)
    db_session.add(
        Participant(
            pid="mallory",
            display_name="Mallory",
            public_key="M" * 64,
            type="person",
            status="active",
            profile={},
        )
    )
    await db_session.commit()

    async def _fake_build_graph_snapshot(*, run_id: str, equivalent: str, session=None):
        assert run_id == "test-run"
        # Snapshot contains only alice + bob.
        return SimulatorGraphSnapshot(
            equivalent=str(equivalent),
            generated_at=datetime.now(timezone.utc),
            nodes=[
                SimulatorGraphNode(id="alice", name="Alice", type="person", status="active"),
                SimulatorGraphNode(id="bob", name="Bob", type="person", status="active"),
            ],
            links=[],
        )

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _fake_build_graph_snapshot)

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/simulator/runs/test-run/actions/participants-list",
        headers=headers,
    )

    # Assert
    assert r.status_code == 200, r.text
    payload = r.json()
    pids = [x["pid"] for x in payload["items"]]
    assert pids == ["alice", "bob"]


@pytest.mark.asyncio
async def test_action_trustlines_list_is_run_scoped_and_filters_by_participant_pid(
    client, db_session, interact_actions_enabled, monkeypatch
):
    """trustlines-list must be run/snapshot-scoped and filter incoming+outgoing by pid."""

    import app.api.v1.simulator as simulator_module

    # Arrange
    alice, bob, uah = await _seed_alice_bob_uah(db_session)

    # Add a "foreign" trustline in DB that must NOT leak into the list (snapshot-scoped).
    mallory = Participant(
        pid="mallory",
        display_name="Mallory",
        public_key="M" * 64,
        type="person",
        status="active",
        profile={},
    )
    db_session.add(mallory)
    await db_session.commit()
    db_session.add(
        TrustLine(
            from_participant_id=alice.id,
            to_participant_id=mallory.id,
            equivalent_id=uah.id,
            limit=Decimal("999"),
            status="active",
        )
    )
    await db_session.commit()

    async def _fake_build_graph_snapshot(*, run_id: str, equivalent: str, session=None):
        assert run_id == "test-run"
        assert str(equivalent).upper() == "UAH"
        return SimulatorGraphSnapshot(
            equivalent=str(equivalent),
            generated_at=datetime.now(timezone.utc),
            nodes=[
                SimulatorGraphNode(id="alice", name="Alice", type="person", status="active"),
                SimulatorGraphNode(id="bob", name="Bob", type="person", status="active"),
            ],
            links=[
                # outgoing from alice
                SimulatorGraphLink(
                    source="alice",
                    target="bob",
                    trust_limit="10",
                    used="1",
                    available="9",
                    status="active",
                ),
                # incoming to alice
                SimulatorGraphLink(
                    source="bob",
                    target="alice",
                    trust_limit="7",
                    used="0",
                    available="7",
                    status="active",
                ),
                # must NOT be returned (closed)
                SimulatorGraphLink(
                    source="alice",
                    target="bob",
                    trust_limit="10",
                    used="0",
                    available="10",
                    status="closed",
                ),
            ],
        )

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _fake_build_graph_snapshot)

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act 1: full list for run snapshot
    r_all = await client.get(
        "/api/v1/simulator/runs/test-run/actions/trustlines-list?equivalent=UAH",
        headers=headers,
    )
    assert r_all.status_code == 200, r_all.text
    items_all = r_all.json()["items"]
    assert [(x["from_pid"], x["to_pid"]) for x in items_all] == [
        ("alice", "bob"),
        ("bob", "alice"),
    ]

    # Act 2: filter by participant_pid should include incoming+outgoing
    r_f = await client.get(
        "/api/v1/simulator/runs/test-run/actions/trustlines-list?equivalent=UAH&participant_pid=alice",
        headers=headers,
    )
    assert r_f.status_code == 200, r_f.text
    items_f = r_f.json()["items"]
    assert {(x["from_pid"], x["to_pid"]) for x in items_f} == {
        ("alice", "bob"),
        ("bob", "alice"),
    }


@pytest.mark.asyncio
async def test_action_trustline_create_self_loop_is_invalid_request(
    client, db_session, interact_actions_enabled
):
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "alice",
            "equivalent": "UAH",
            "limit": "10",
        },
    )
    assert r.status_code == 400
    payload = r.json()
    assert payload.get("code") == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_action_payment_real_happy_mocked(client, db_session, interact_actions_enabled, monkeypatch):
    # Arrange
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    async def _mock_create_payment_internal(self, *_args, **_kwargs):
        return SimpleNamespace(tx_id=uuid.uuid4(), status="committed", routes=[])

    monkeypatch.setattr(interact_actions_enabled.PaymentService, "create_payment_internal", _mock_create_payment_internal)

    # Act
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/payment-real",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "amount": "1",
            "client_action_id": "c_pay_1",
        },
    )

    # Assert
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["ok"] is True
    assert isinstance(payload.get("payment_id"), str) and payload["payment_id"]
    assert payload["status"] == "committed"
    assert payload["amount"] == "1"


@pytest.mark.asyncio
async def test_action_payment_real_emits_tx_updated_with_edge_patch(
    client, db_session, interact_actions_enabled, monkeypatch
):
    """Regression test for interact backend L4: tx.updated must contain patches.

    We don't validate patch correctness here; only that edge_patch is non-empty
    (so UI can update incrementally without full refresh).
    """

    import app.api.v1.simulator as simulator_module

    # Arrange
    alice, bob, uah = await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Trustline is required for meaningful edge_patch.
    db_session.add(
        TrustLine(
            from_participant_id=alice.id,
            to_participant_id=bob.id,
            equivalent_id=uah.id,
            limit=Decimal("10"),
            status="active",
        )
    )
    await db_session.commit()

    # Fake runtime run record so SSE emission is executed (not swallowed by NotFound).
    run = SimpleNamespace(
        run_id="test-run",
        tick_index=0,
        _real_participants=[(alice.id, alice.pid), (bob.id, bob.pid)],
        _real_viz_by_eq={},
    )
    monkeypatch.setattr(simulator_module.runtime, "get_run", lambda _run_id: run)

    emitted: dict[str, object] = {}

    class _FakeEmitter:
        def __init__(self, *, sse, utc_now, logger):
            return None

        def emit_tx_updated(
            self,
            *,
            run_id: str,
            run,
            equivalent: str,
            from_pid,
            to_pid,
            amount,
            amount_flyout: bool,
            ttl_ms: int,
            edges,
            node_badges=None,
            intensity_key=None,
            edge_patch=None,
            node_patch=None,
            event_id=None,
        ) -> None:
            emitted["edge_patch"] = edge_patch
            emitted["node_patch"] = node_patch

    monkeypatch.setattr(simulator_module, "SseEventEmitter", _FakeEmitter)

    async def _mock_create_payment_internal(self, *_args, **_kwargs):
        return SimpleNamespace(tx_id=uuid.uuid4(), status="committed", routes=[])

    monkeypatch.setattr(
        interact_actions_enabled.PaymentService,
        "create_payment_internal",
        _mock_create_payment_internal,
    )

    # Act
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/payment-real",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "amount": "1",
            "client_action_id": "c_pay_patch_1",
        },
    )

    # Assert
    assert r.status_code == 200, r.text
    assert isinstance(emitted.get("edge_patch"), list)
    assert len(emitted["edge_patch"]) > 0
    assert isinstance(emitted["edge_patch"][0], dict)
    assert emitted["edge_patch"][0].get("source") == "alice"
    assert emitted["edge_patch"][0].get("target") == "bob"


@pytest.mark.asyncio
async def test_action_payment_real_no_route_mocked(client, db_session, interact_actions_enabled, monkeypatch):
    # Arrange
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    async def _mock_create_payment_internal(self, *_args, **_kwargs):
        raise RoutingException("no route", insufficient_capacity=False)

    monkeypatch.setattr(interact_actions_enabled.PaymentService, "create_payment_internal", _mock_create_payment_internal)

    # Act
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/payment-real",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "amount": "1",
            "client_action_id": "c_pay_2",
        },
    )

    # Assert
    assert r.status_code == 409
    payload = r.json()
    assert payload.get("code") == "NO_ROUTE"


@pytest.mark.asyncio
async def test_action_payment_real_insufficient_capacity_when_topology_path_exists(
    client, db_session, interact_actions_enabled, monkeypatch
):
    # Arrange: trustline topology exists (alice -> bob), but payment engine reports routing failure.
    alice, bob, uah = await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Enable payment from alice -> bob via trustline bob -> alice.
    tl = TrustLine(
        from_participant_id=bob.id,
        to_participant_id=alice.id,
        equivalent_id=uah.id,
        status="active",
        limit=Decimal("0"),
        policy=None,
    )
    db_session.add(tl)
    await db_session.commit()

    # The endpoint uses PaymentRouter cached topology; since this test writes trustlines directly
    # (not via API/service that would invalidate), ensure cache doesn't contain stale empty graph.
    from app.core.payments.router import PaymentRouter

    PaymentRouter.invalidate_cache("UAH")

    async def _mock_create_payment_internal(self, *_args, **_kwargs):
        raise RoutingException("no capacity", insufficient_capacity=True)

    monkeypatch.setattr(
        interact_actions_enabled.PaymentService,
        "create_payment_internal",
        _mock_create_payment_internal,
    )

    # Act
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/payment-real",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "amount": "1",
            "client_action_id": "c_pay_cap_1",
        },
    )

    # Assert
    assert r.status_code == 409
    payload = r.json()
    assert payload.get("code") == "INSUFFICIENT_CAPACITY"


@pytest.mark.asyncio
async def test_action_payment_real_amount_manual_validation_stays_invalid_amount(
    client, db_session, interact_actions_enabled
):
    # Arrange
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act: amount <= 0 is validated manually via parse_amount_decimal(..., require_positive=True)
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/payment-real",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "amount": "0",
        },
    )

    # Assert
    assert r.status_code == 400
    payload = r.json()
    assert payload.get("code") == "INVALID_AMOUNT"


@pytest.mark.asyncio
async def test_action_clearing_real_happy_zero_cycles(client, db_session, interact_actions_enabled):
    # Arrange
    await _seed_alice_bob_uah(db_session)
    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/clearing-real",
        headers=headers,
        json={
            "equivalent": "UAH",
            "max_depth": 6,
            "client_action_id": "c_clear_1",
        },
    )

    # Assert
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["ok"] is True
    assert payload["equivalent"] == "UAH"
    assert payload["cleared_cycles"] == 0
    assert isinstance(payload.get("cycles"), list)


@pytest.mark.asyncio
async def test_action_clearing_real_total_cleared_amount_is_actual_not_precalc(
    client, db_session, interact_actions_enabled, monkeypatch
):
    # Arrange: create a real debt triangle cycle.
    alice = Participant(
        pid="alice",
        display_name="Alice",
        public_key="A" * 64,
        type="person",
        status="active",
        profile={},
    )
    bob = Participant(
        pid="bob",
        display_name="Bob",
        public_key="B" * 64,
        type="person",
        status="active",
        profile={},
    )
    carol = Participant(
        pid="carol",
        display_name="Carol",
        public_key="C" * 64,
        type="person",
        status="active",
        profile={},
    )
    uah = Equivalent(code="UAH", precision=2, is_active=True)
    db_session.add_all([alice, bob, carol, uah])
    await db_session.commit()

    # Trustlines required by auto-clearing policy check.
    db_session.add_all(
        [
            TrustLine(
                from_participant_id=bob.id,
                to_participant_id=alice.id,
                equivalent_id=uah.id,
                status="active",
                limit=Decimal("100"),
                policy={"auto_clearing": True},
            ),
            TrustLine(
                from_participant_id=carol.id,
                to_participant_id=bob.id,
                equivalent_id=uah.id,
                status="active",
                limit=Decimal("100"),
                policy={"auto_clearing": True},
            ),
            TrustLine(
                from_participant_id=alice.id,
                to_participant_id=carol.id,
                equivalent_id=uah.id,
                status="active",
                limit=Decimal("100"),
                policy={"auto_clearing": True},
            ),
        ]
    )
    await db_session.commit()

    # Debts: alice->bob=5, bob->carol=10, carol->alice=7 => actual clear amount is 5.
    db_session.add_all(
        [
            Debt(
                debtor_id=alice.id,
                creditor_id=bob.id,
                equivalent_id=uah.id,
                amount=Decimal("5"),
            ),
            Debt(
                debtor_id=bob.id,
                creditor_id=carol.id,
                equivalent_id=uah.id,
                amount=Decimal("10"),
            ),
            Debt(
                debtor_id=carol.id,
                creditor_id=alice.id,
                equivalent_id=uah.id,
                amount=Decimal("7"),
            ),
        ]
    )
    await db_session.commit()

    # Make find_cycles lie about per-edge amounts to ensure endpoint doesn't pre-calc based on it.
    import app.core.clearing.service as clearing_service_module

    original_find_cycles = clearing_service_module.ClearingService.find_cycles

    async def _find_cycles_with_stale_amounts(self, equivalent_code: str, max_depth: int = 6):
        cycles = await original_find_cycles(self, equivalent_code, max_depth=max_depth)
        for cycle in cycles:
            for edge in cycle:
                edge["amount"] = "999"  # wrong pre-calc amount
        return cycles

    monkeypatch.setattr(
        clearing_service_module.ClearingService,
        "find_cycles",
        _find_cycles_with_stale_amounts,
    )

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/clearing-real",
        headers=headers,
        json={
            "equivalent": "UAH",
            "max_depth": 3,
            "client_action_id": "c_clear_actual_1",
        },
    )

    # Assert
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["ok"] is True
    assert payload["cleared_cycles"] == 1
    assert Decimal(str(payload["total_cleared_amount"])) == Decimal("5")
    assert isinstance(payload.get("cycles"), list)
    assert Decimal(str(payload["cycles"][0]["cleared_amount"])) == Decimal("5")


@pytest.mark.asyncio
async def test_action_participants_list_returns_array(
    client, db_session, interact_actions_enabled, monkeypatch
):
    import app.api.v1.simulator as simulator_module

    # Arrange
    await _seed_alice_bob_uah(db_session)

    async def _fake_build_graph_snapshot(*, run_id: str, equivalent: str, session=None):
        assert run_id == "test-run"
        return SimulatorGraphSnapshot(
            equivalent=str(equivalent),
            generated_at=datetime.now(timezone.utc),
            nodes=[
                SimulatorGraphNode(id="alice", name="Alice", type="person", status="active"),
                SimulatorGraphNode(id="bob", name="Bob", type="person", status="active"),
            ],
            links=[],
        )

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _fake_build_graph_snapshot)

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/simulator/runs/test-run/actions/participants-list",
        headers=headers,
    )
    assert r.status_code == 200, r.text

    # Assert
    payload = r.json()
    assert isinstance(payload.get("items"), list)
    assert {x.get("pid") for x in payload["items"]} >= {"alice", "bob"}


@pytest.mark.asyncio
async def test_action_trustlines_list_returns_array(
    client, db_session, interact_actions_enabled, monkeypatch
):
    import app.api.v1.simulator as simulator_module

    # Arrange
    await _seed_alice_bob_uah(db_session)

    async def _fake_build_graph_snapshot(*, run_id: str, equivalent: str, session=None):
        assert run_id == "test-run"
        assert str(equivalent).upper() == "UAH"
        return SimulatorGraphSnapshot(
            equivalent=str(equivalent),
            generated_at=datetime.now(timezone.utc),
            nodes=[
                SimulatorGraphNode(id="alice", name="Alice", type="person", status="active"),
                SimulatorGraphNode(id="bob", name="Bob", type="person", status="active"),
            ],
            links=[],
        )

    monkeypatch.setattr(simulator_module.runtime, "build_graph_snapshot", _fake_build_graph_snapshot)

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}

    # Act
    r = await client.get(
        "/api/v1/simulator/runs/test-run/actions/trustlines-list",
        headers=headers,
        params={"equivalent": "UAH"},
    )
    assert r.status_code == 200, r.text

    # Assert
    payload = r.json()
    assert isinstance(payload.get("items"), list)


@pytest.mark.asyncio
async def test_trustline_create_used_read_error_returns_503_and_error_envelope(
    client, db_session, monkeypatch
):
    import app.api.v1.simulator as simulator_module

    monkeypatch.setenv("SIMULATOR_ACTIONS_ENABLE", "1")
    # Keep the test focused on the used_now failure path.
    monkeypatch.setattr(simulator_module, "_require_run_accepts_actions_or_error", lambda _run_id: None)

    alice = Participant(
        pid="alice",
        display_name="Alice",
        public_key="A" * 64,
        type="person",
        status="active",
        profile={},
    )
    bob = Participant(
        pid="bob",
        display_name="Bob",
        public_key="B" * 64,
        type="person",
        status="active",
        profile={},
    )
    uah = Equivalent(code="UAH", precision=2, is_active=True)

    db_session.add_all([alice, bob, uah])
    await db_session.commit()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("used read failed")

    monkeypatch.setattr(simulator_module, "_trustline_used_amount", _boom)

    headers = {"X-Admin-Token": settings.ADMIN_TOKEN}
    r = await client.post(
        "/api/v1/simulator/runs/test-run/actions/trustline-create",
        headers=headers,
        json={
            "from_pid": "alice",
            "to_pid": "bob",
            "equivalent": "UAH",
            "limit": "10",
            "client_action_id": "c1",
        },
    )

    assert r.status_code == 503
    body = r.json()
    assert body.get("code") == "TRUSTLINE_USED_UNAVAILABLE"
    assert isinstance(body.get("message"), str) and body.get("message")
    assert body.get("details", {}).get("equivalent") == "UAH"
    assert body.get("details", {}).get("from_pid") == "alice"
    assert body.get("details", {}).get("to_pid") == "bob"

