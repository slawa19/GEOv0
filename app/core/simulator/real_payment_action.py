from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _RealPaymentAction:
    seq: int
    equivalent: str
    sender_pid: str
    receiver_pid: str
    amount: str
    # 036 B1: a payment of a scenario's `payment` event carries the key of the EVENT (run, launch epoch, event index), not
    # the tick's: see `scripted_event_idempotency_key`. None = the tick's key, as for every planned payment.
    idempotency_key: str | None = None
