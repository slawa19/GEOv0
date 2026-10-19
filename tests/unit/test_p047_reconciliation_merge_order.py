"""047 `T4703`, fix-delta after §15: the order guard of criterion (b)'s merge sees EVERY disorder of the journal stream.

`app/core/ledger/reconciliation.py::_streamed_criterion_b` merges the envelopes (ordered by id) with the journal rows
(ordered by operation id). Over a stream out of that order the merge would hand an envelope the wrong rows, so a
disorder must be an error (`ReconciliationReadError`), never a verdict. Until this fix the journal advanced only while
its row was at or below the current envelope, and the rows after the last envelope were closed unread - two shapes of
disorder got through and became findings (the §15 reviews of `e902e332..583f7a19`: Codex P3 #2, Argon (a)).

THE STAND. No database: `_streamed` is replaced by a stand that serves the two statements from lists (the envelope
statement is the one selecting `intent`), so the order is the test's. The envelopes are TEST_FIXTURE kinds where the
verdict does not matter and PAYMENT v1 where it does - a rule that reads the entries, so a row handed to the wrong
envelope shows as a finding.

ORPHANS (a journal row whose operation is not among the envelopes - possible only with the foreign key off) keep their
treatment of `main` `e902e332`: grouped by operation, never looked up by any envelope, so criterion (b) never read them;
criterion (a) sums and counts them in `_journal_sums`, which this does not touch. Here: passed over, order still checked.

MUTATION: drop `await _drain(rows)` from `_streamed_criterion_b` - the two reviewers' reproducers go red (no error).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.core.ledger import reconciliation
from app.core.ledger.reconciliation import ReconciliationReadError

EQ = uuid.UUID(int=0xE0)
A, B = uuid.UUID(int=0xA), uuid.UUID(int=0xB)


def _id(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _payment_v1(n: int) -> SimpleNamespace:
    """A PAYMENT v1 envelope paying A -> B: its rule refutes every entry outside the pair {A, B}."""

    tx_id = f"tx-{n}"
    intent = {
        "tx_id": tx_id,
        "locks": [{"flows": [{"from": str(A), "to": str(B), "amount": "1", "equivalent": str(EQ)}]}],
    }
    return SimpleNamespace(
        id=_id(n), kind="PAYMENT", tx_id=tx_id, intent=intent, intent_digest="0" * 64, intent_encoding_version=1,
        tx_payload=None,
    )


def _row(operation: int, debtor: uuid.UUID = A, creditor: uuid.UUID = B) -> SimpleNamespace:
    return SimpleNamespace(
        operation_id=_id(operation), ordinal=1, debtor_id=debtor, creditor_id=creditor, effect="I",
        amount_before=None, delta=1,
    )


def _serve(monkeypatch, envelopes: list, journal: list) -> None:
    async def streamed(_session, statement):
        for row in envelopes if "intent" in statement.selected_columns.keys() else journal:
            yield row

    monkeypatch.setattr(reconciliation, "_streamed", streamed)


async def _merge():
    return await reconciliation._streamed_criterion_b(None, EQ)


@pytest.mark.asyncio
async def test_p047_a_decreasing_pair_after_the_last_envelope_is_an_error(monkeypatch) -> None:
    """Codex P3 #2: envelopes [2], journal operation ids [3, 2] - 3 an orphan ahead of the envelope's own row.

    Before the fix: the merge never advanced past 3, envelope 2 was examined with no rows and the decreasing pair was
    closed unread - a verdict instead of an error.
    """

    _serve(monkeypatch, [_payment_v1(2)], [_row(3), _row(2)])
    with pytest.raises(ReconciliationReadError, match="out of operation order"):
        await _merge()


@pytest.mark.asyncio
async def test_p047_a_row_ahead_of_every_envelope_is_an_error_not_findings(monkeypatch) -> None:
    """Argon (a): a row whose operation id is above every envelope's, at the head of the journal stream.

    Before the fix the merge never advanced again, and every envelope was judged on no rows. The rows here are on an
    edge outside the payment's pair, so a v1 envelope that DID receive its row would say so: the error, not that
    finding, is what must come out.
    """

    top = 2**128 - 1
    envelopes = [_payment_v1(1), _payment_v1(2)]
    _serve(monkeypatch, envelopes, [_row(top), _row(1, B, A), _row(2, A, uuid.UUID(int=0xC))])
    with pytest.raises(ReconciliationReadError, match="out of operation order"):
        await _merge()


@pytest.mark.asyncio
async def test_p047_envelopes_out_of_id_order_are_an_error(monkeypatch) -> None:
    _serve(monkeypatch, [_payment_v1(2), _payment_v1(1)], [_row(1), _row(2)])
    with pytest.raises(ReconciliationReadError, match="envelopes read out of id order"):
        await _merge()


@pytest.mark.asyncio
async def test_p047_an_ordered_stream_with_orphans_merges_as_the_grouping_did(monkeypatch) -> None:
    """CONTROL, so the guard is not vacuous: the same rows IN ORDER, with orphans before, between and after the
    envelopes, give no error and exactly the outcome of the rule-level `_criterion_b` over the grouping by operation -
    which never handed an orphan to anyone (`main` `e902e332`)."""

    envelopes = [_payment_v1(2), _payment_v1(4), _payment_v1(6)]
    journal = [
        _row(1), _row(2), _row(2, B, A), _row(3, A, uuid.UUID(int=0xC)), _row(4, A, uuid.UUID(int=0xC)),
        _row(5), _row(7), _row(8, B, uuid.UUID(int=0xC)),
    ]
    _serve(monkeypatch, envelopes, journal)
    findings, coverage = await _merge()

    grouped: dict = {}
    for row in journal:
        grouped.setdefault(row.operation_id, []).append(reconciliation._entry(row))
    assert (findings, coverage) == reconciliation._criterion_b(EQ, envelopes, grouped)
    # The stand reads what it claims: envelope 4's row off the pair is refuted, nothing of the orphans is.
    assert [(f["operation_id"], f["rule"]) for f in findings] == [(str(_id(4)), "entry_outside_the_flow_pairs")]
    assert coverage == (("structural_only", "PAYMENT", 3),)
