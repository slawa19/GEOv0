from __future__ import annotations

from app.core.payments.router import PaymentRouter


def test_payment_router_invalidate_cache_clears_specific_key() -> None:
    key1 = "AAA"
    key2 = "BBB"

    old = dict(PaymentRouter._graph_cache)
    try:
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache[key1] = (0.0, {}, {}, {}, {}, {})
        PaymentRouter._graph_cache[key2] = (0.0, {}, {}, {}, {}, {})

        PaymentRouter.invalidate_cache(key1)
        assert key1 not in PaymentRouter._graph_cache
        assert key2 in PaymentRouter._graph_cache

        PaymentRouter.invalidate_cache()
        assert PaymentRouter._graph_cache == {}
    finally:
        PaymentRouter._graph_cache.clear()
        PaymentRouter._graph_cache.update(old)
