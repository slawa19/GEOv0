"""The one place a PostgreSQL SQLSTATE is read off an exception (024 `T2415.1`, F-024-10).

EXTRACTION IS SHARED, RETRY POLICY IS NOT. Which codes a failure carries is a fact about the driver
and SQLAlchemy; what to do about them belongs to the owner of the transaction (Р-4.3 of 021, spec
024 «Запрещено»): the payment and the money-phase replay retry `ROLLED_BACK_SQLSTATES` and the
debt-pair `23505`, the inject also retries `55P03`, clearing retries `ROLLED_BACK_SQLSTATES` only and
turns `55P03` into a timeout. Those sets stay in their modules; this module has no policy.

WALK ORDER - `deliberate_chain`, rule of 2026-09-12. The exception itself; then its `orig` when that is
an exception (SQLAlchemy's `DBAPIError` carries the driver error there); otherwise its `__cause__`
(`raise ... from`). `__context__` is NEVER followed. Python sets `__context__` to whatever was being
handled when an exception was raised, which may be an unrelated earlier failure: retry code that
catches a 40001 and then hits a terminal error inside that `except` block made the terminal error
inherit the conflict's identity, and a PostgreSQL 23505 raised inside a 40001 handler was retried
although retrying it cannot succeed. Nothing legitimate is lost: SQLAlchemy raises `DBAPIError` FROM
the driver error, and its asyncpg adapter raises the adapted error FROM the asyncpg one, so a genuine
code is always reachable through deliberate wrapping.

WHERE THE CODE SITS. `sqlstate` (asyncpg, and SQLAlchemy's asyncpg adapter) or `pgcode` (psycopg, and
the same adapter); a driver may expose it as `.code`, but on a `DBAPIError` wrapper `.code` names
SQLAlchemy's documentation page (`dbapi`, `gkpj`), not a SQLSTATE.
"""

from __future__ import annotations

from typing import Iterator

from sqlalchemy.exc import DBAPIError

#: PostgreSQL reports these for a transaction IT has already rolled back - 40001
#: serialization_failure, 40P01 deadlock_detected - so the attempt is known not to have landed.
#: A fact about the server, not a retry policy: each owner decides whether it retries them.
ROLLED_BACK_SQLSTATES = frozenset({"40001", "40P01"})

_SQLSTATE_ATTRIBUTES = ("sqlstate", "pgcode")


def deliberate_chain(exc: BaseException | None) -> Iterator[BaseException]:
    """`exc`, then `orig`, otherwise `__cause__` - never `__context__` (module docstring)."""

    current = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current

        following = getattr(current, "orig", None)
        if not isinstance(following, BaseException):
            following = current.__cause__
        current = following if isinstance(following, BaseException) else None


def sqlstate(exc: BaseException | None, *, walk: bool = True, bare_code: bool = True) -> str | None:
    """The SQLSTATE `exc` carries, or None.

    With `walk`, the whole deliberate chain is read, `sqlstate` before `pgcode` before a driver's bare
    `.code`; without it, only `exc` itself - an owner that inspects one driver error passes it here.
    `bare_code=False` ignores `.code` altogether (the inject never read it).
    """

    nodes = list(deliberate_chain(exc)) if walk else [exc] if exc is not None else []
    for attribute in _SQLSTATE_ATTRIBUTES:
        for node in nodes:
            value = getattr(node, attribute, None)
            if value:
                return str(value)
    if bare_code:
        for node in nodes:
            if isinstance(node, DBAPIError):
                continue
            value = getattr(node, "code", None)
            if value:
                return str(value)
    return None


def chain_codes(exc: BaseException) -> set[str]:
    """Every code any link of the deliberate chain names, a wrapper's own `.code` included.

    Clearing tests membership (`& ROLLED_BACK_SQLSTATES`, `"55P03" in`) rather than reading one code;
    the wrapper's documentation code is in the set but matches no SQLSTATE any owner asks about.
    """

    return {
        str(value).strip()
        for node in deliberate_chain(exc)
        for attribute in (*_SQLSTATE_ATTRIBUTES, "code")
        if (value := getattr(node, attribute, None)) is not None
    }
