from __future__ import annotations

import asyncio

import pytest

from app.core.payments.service import _drain_payment_cleanup


@pytest.mark.asyncio
async def test_payment_cleanup_is_drained_after_caller_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()
    terminal = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        try:
            await release.wait()
        finally:
            terminal.set()

    owner = asyncio.create_task(_drain_payment_cleanup(cleanup))
    await started.wait()
    owner.cancel("caller cancelled")
    await asyncio.sleep(0)
    assert terminal.is_set() is False

    release.set()
    result = await owner

    assert isinstance(result, asyncio.CancelledError)
    assert terminal.is_set() is True


@pytest.mark.asyncio
async def test_repeated_cancellation_cancels_but_still_drains_cleanup():
    started = asyncio.Event()
    terminal = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            terminal.set()

    owner = asyncio.create_task(_drain_payment_cleanup(cleanup))
    await started.wait()
    owner.cancel("first cancellation")
    await asyncio.sleep(0)
    owner.cancel("repeated cancellation")

    result = await owner

    assert isinstance(result, asyncio.CancelledError)
    assert terminal.is_set() is True
