from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any


async def _drain_task(
    task: asyncio.Task[Any],
    cancellation: asyncio.CancelledError | None,
) -> asyncio.CancelledError | None:
    """Wait for ``task`` to finish without losing caller cancellation."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            current = asyncio.current_task()
            if cancellation is None and current is not None and current.cancelling():
                cancellation = exc
        except Exception:
            # The child has reached a failed terminal state. Its exception is
            # classified by task.result() after the drain loop.
            pass
    return cancellation


async def _run_to_terminal(
    operation: Callable[[], Awaitable[Any]],
    cancellation: asyncio.CancelledError | None,
) -> tuple[Exception | asyncio.CancelledError | None, asyncio.CancelledError | None]:
    task = asyncio.create_task(operation())
    cancellation = await _drain_task(task, cancellation)

    error: Exception | asyncio.CancelledError | None = None
    try:
        task.result()
    except asyncio.CancelledError as exc:
        error = exc
    except Exception as exc:
        error = exc
    return error, cancellation


async def resolve_rollback_under_cancellation(
    *,
    rollback: Callable[[], Awaitable[Any]],
    on_rollback: Callable[[], Any],
    on_unknown: Callable[[], Any],
    on_failure: Callable[[Exception | asyncio.CancelledError], Any] | None = None,
) -> None:
    """Drain rollback, resolve its actual outcome once, then restore cancellation."""

    rollback_error, cancellation = await _run_to_terminal(rollback, None)

    resolver_error: Exception | None = None
    try:
        if rollback_error is None:
            on_rollback()
        else:
            on_unknown()
    except Exception as exc:
        resolver_error = exc

    if rollback_error is not None and on_failure is not None:
        try:
            on_failure(rollback_error)
        except Exception as exc:
            if resolver_error is None:
                resolver_error = exc

    if cancellation is not None:
        raise cancellation
    if rollback_error is not None:
        raise rollback_error
    if resolver_error is not None:
        raise resolver_error


async def resolve_commit_under_cancellation(
    *,
    commit: Callable[[], Awaitable[Any]],
    rollback: Callable[[], Awaitable[Any]],
    on_commit: Callable[[], Any],
    on_rollback: Callable[[], Any],
    on_unknown: Callable[[], Any],
    logger: logging.Logger,
) -> None:
    """Resolve a commit from its terminal result, then restore cancellation.

    ``asyncio.shield`` keeps the database operation alive, but a second caller
    cancellation can still interrupt the surrounding await. Drain both commit
    and failure-cleanup rollback to a terminal result before invoking exactly
    one resolver callback. A failed cleanup selects the unknown outcome but
    never masks the original commit error; caller cancellation keeps priority.
    """

    cancellation: asyncio.CancelledError | None = None
    commit_error, cancellation = await _run_to_terminal(commit, cancellation)

    resolver_error: Exception | None = None
    try:
        if commit_error is None:
            on_commit()
        else:
            rollback_error, cancellation = await _run_to_terminal(
                rollback,
                cancellation,
            )
            commit_cancelled = isinstance(commit_error, asyncio.CancelledError)
            if commit_cancelled:
                try:
                    logger.warning(
                        "simulator.real.commit_outcome_unknown "
                        "reason=commit_cancelled cleanup_outcome=%s "
                        "commit_error=%s rollback_error=%s",
                        (
                            "rollback_succeeded"
                            if rollback_error is None
                            else "rollback_failed"
                        ),
                        type(commit_error).__name__,
                        (
                            type(rollback_error).__name__
                            if rollback_error is not None
                            else "None"
                        ),
                    )
                except Exception:
                    pass

            if rollback_error is None and not commit_cancelled:
                on_rollback()
            else:
                if rollback_error is not None:
                    try:
                        logger.warning(
                            "simulator.real.rollback_after_commit_failure_failed "
                            "commit_error=%s rollback_error=%s",
                            type(commit_error).__name__,
                            type(rollback_error).__name__,
                            exc_info=(
                                type(rollback_error),
                                rollback_error,
                                rollback_error.__traceback__,
                            ),
                        )
                    except Exception:
                        pass
                on_unknown()
    except Exception as exc:
        resolver_error = exc

    if cancellation is not None:
        raise cancellation
    if commit_error is not None:
        raise commit_error
    if resolver_error is not None:
        raise resolver_error
