"""024 `T2415.1`: every SQLSTATE policy over every exception shape, pinned before and after one extractor.

A CHARACTERIZATION, not a target. The table below was produced by the code at `7f9550d` (before the
extractor was shared) and is asserted unchanged after it: sharing the walk must not move one answer of
one policy. Policies deliberately differ and the table shows where (spec «Запрещено», Р-4.3): only the
inject treats `55P03` as transient; only the payment/money-phase owners and the inject accept the debt-
pair `23505`; the money replay and the inject read the driver error one level down (`orig`), the
payment and clearing classifiers walk the deliberate chain; the inject ignores a bare `.code`.

Shapes: `direct` - a driver error itself; `dbapi` - wrapped by SQLAlchemy (`IntegrityError` for class
23, `DBAPIError` otherwise, as the asyncpg adapter maps them); `cause` - a service error raised FROM
that; `context` - a service error raised WHILE HANDLING it (no `from`); `masked` - a terminal 23502
(not-null: no constraint name) whose wrapper was raised while handling it; `orig_ctx` - the same, but
the context sits on the asyncpg error of the terminal; `nested` - a wrapper whose `orig` has no code and carries the driver error as `__cause__`.
Carriers: `asyncpg` - what SQLAlchemy's asyncpg adapter raises (`sqlstate` = `pgcode` on the adapted
error, `constraint_name` on the asyncpg error it chains); `code` - a driver error exposing only `.code`.

Row: shape carrier code | payment sqlstate, flags, money-phase name, clearing codes, conflict cause,
constraint. Flags in order, `.` for false: R payment retryable, D debt-pair collision, X tx_id
collision, C clearing retryable, L clearing 55P03 interlock, I inject transient, T live trust-line clash.
The `orig_ctx` rows carried `~` in T while that cell changed on purpose (the trust-line classifier
walked `__context__`, F-024-10). The mask left with 029 F-029-25: it hid the classifier's answer in all
16 rows while the dedicated test pins only the two `23505/live` ones, so the other 14 were unpinned.

Synthetic by design: the real-driver shapes are exercised by the integration suites
(`test_p015_t1525_classification_reads_deliberate_wrapping_only_postgres.py`,
`test_p015_p1_money_conflict_predicate.py`); this module is the grid those cannot afford.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.clearing.service import ClearingService
from app.core.payments.service import (
    _classify_payment_db_error,
    _conflict_cause,
    _constraint_name,
    _is_tx_id_collision,
    _payment_db_sqlstate,
    is_debt_pair_collision,
)
from app.core.simulator.money_replay import money_conflict_name
from app.core.simulator.real_runner_impl import _is_transient_inject_db_error
from app.core.trustlines.service import _LIVE_TRUSTLINE_INDEX, _is_live_trustline_uniqueness_violation
from app.utils.exceptions import RetryablePaymentConflictException
from tests.p019_support import require_target

CODES = {
    "40001": ("40001", None),
    "40P01": ("40P01", None),
    "55P03": ("55P03", None),
    "23505/debt": ("23505", "uq_debts_debtor_creditor_equivalent"),
    "23505/tx": ("23505", "transactions_tx_id_key"),
    "23505/live": ("23505", _LIVE_TRUSTLINE_INDEX),
    "23503": ("23503", "fk_probe"),
    "none": (None, None),
}
SHAPES = ("direct", "dbapi", "cause", "context", "masked", "orig_ctx", "nested")
CARRIERS = ("asyncpg", "code")


class _Asyncpg(Exception):
    """The asyncpg error: `sqlstate` and the structured constraint name."""


class _Adapted(Exception):
    """SQLAlchemy's adapted error: `pgcode` = `sqlstate`, chained FROM the asyncpg error."""


class _CodeOnly(Exception):
    """A driver error that exposes its SQLSTATE only as `.code`."""


def _driver(carrier: str, code: str | None, constraint: str | None) -> BaseException:
    if carrier == "code":
        error = _CodeOnly(f"driver failure {code}")
        error.code = code
        error.constraint_name = constraint
        return error
    raw = _Asyncpg(f"driver failure {code}")
    raw.sqlstate, raw.constraint_name = code, constraint
    adapted = _Adapted(f"adapted {code}")
    adapted.sqlstate = adapted.pgcode = code
    adapted.__cause__ = raw
    return adapted


def _wrap(driver: BaseException | None, code: str | None) -> DBAPIError:
    wrapper = IntegrityError if (code or "").startswith("23") else DBAPIError
    return wrapper("INSERT INTO probe VALUES (1)", (), driver)


def _shape(shape: str, carrier: str, label: str) -> BaseException:
    code, constraint = CODES[label]
    driver = _driver(carrier, code, constraint)
    if shape == "direct":
        return driver
    if shape == "dbapi":
        return _wrap(driver, code)
    if shape in ("cause", "context"):
        service = RuntimeError("service failure")
        setattr(service, f"__{shape}__", _wrap(driver, code))
        return service
    terminal = _driver("asyncpg", "23502", None)
    if shape == "masked":
        wrapped = _wrap(terminal, "23502")
        wrapped.__context__ = _wrap(driver, code)
        return wrapped
    if shape == "orig_ctx":
        terminal.__cause__.__context__ = driver
        return _wrap(terminal, "23502")
    plain = Exception("driver wrapper without a code")
    plain.__cause__ = driver
    return _wrap(plain, code)


def _row(name: str, exc: BaseException) -> str:
    flags = [
        isinstance(_classify_payment_db_error(exc), RetryablePaymentConflictException),
        is_debt_pair_collision(exc),
        _is_tx_id_collision(exc),
        ClearingService._is_retryable_concurrency_error(exc),
        "55P03" in ClearingService._postgres_error_codes(exc),
        _is_transient_inject_db_error(exc),
        _is_live_trustline_uniqueness_violation(exc),
    ]
    marks = "".join(letter if flag else "." for letter, flag in zip("RDXCLIT", flags))
    clearing = ",".join(sorted(ClearingService._postgres_error_codes(exc))) or "-"
    return (
        f"{name} | {_payment_db_sqlstate(exc)} {marks} {money_conflict_name(exc)} {clearing}"
        f" {_conflict_cause(exc)} {_constraint_name(exc)}"
    )


def _matrix() -> list[str]:
    rows = [
        _row(f"{shape} {carrier} {label}", _shape(shape, carrier, label))
        for shape in SHAPES
        for carrier in CARRIERS
        for label in CODES
    ]
    rows.append(_row("non_db", ValueError("not a database error")))
    rows.append(_row("no_orig", IntegrityError("INSERT INTO probe VALUES (1)", (), None)))
    return rows


EXPECTED = """
direct asyncpg 40001 | 40001 ...C... None 40001 _Adapted None
direct asyncpg 40P01 | 40P01 ...C... None 40P01 _Adapted None
direct asyncpg 55P03 | 55P03 ....L.. None 55P03 _Adapted None
direct asyncpg 23505/debt | 23505 ....... None 23505 _Adapted uq_debts_debtor_creditor_equivalent
direct asyncpg 23505/tx | 23505 ....... None 23505 _Adapted transactions_tx_id_key
direct asyncpg 23505/live | 23505 ....... None 23505 _Adapted uq_trust_lines_live_from_to_equivalent
direct asyncpg 23503 | 23503 ....... None 23503 _Adapted fk_probe
direct asyncpg none | None ....... None - _Adapted None
direct code 40001 | 40001 ...C... None 40001 _CodeOnly None
direct code 40P01 | 40P01 ...C... None 40P01 _CodeOnly None
direct code 55P03 | 55P03 ....L.. None 55P03 _CodeOnly None
direct code 23505/debt | 23505 ....... None 23505 _CodeOnly uq_debts_debtor_creditor_equivalent
direct code 23505/tx | 23505 ....... None 23505 _CodeOnly transactions_tx_id_key
direct code 23505/live | 23505 ....... None 23505 _CodeOnly uq_trust_lines_live_from_to_equivalent
direct code 23503 | 23503 ....... None 23503 _CodeOnly fk_probe
direct code none | None ....... None - _CodeOnly None
dbapi asyncpg 40001 | 40001 R..C.I. 40001 40001,dbapi 40001 None
dbapi asyncpg 40P01 | 40P01 R..C.I. 40P01 40P01,dbapi 40P01 None
dbapi asyncpg 55P03 | 55P03 ....LI. None 55P03,dbapi 55P03 None
dbapi asyncpg 23505/debt | 23505 RD...I. 23505 23505,gkpj 23505 uq_debts_debtor_creditor_equivalent
dbapi asyncpg 23505/tx | 23505 ..X.... None 23505,gkpj 23505 transactions_tx_id_key
dbapi asyncpg 23505/live | 23505 ......T None 23505,gkpj 23505 uq_trust_lines_live_from_to_equivalent
dbapi asyncpg 23503 | 23503 ....... None 23503,gkpj 23503 fk_probe
dbapi asyncpg none | None ....... None dbapi DBAPIError None
dbapi code 40001 | 40001 R..C... 40001 40001,dbapi 40001 None
dbapi code 40P01 | 40P01 R..C... 40P01 40P01,dbapi 40P01 None
dbapi code 55P03 | 55P03 ....L.. None 55P03,dbapi 55P03 None
dbapi code 23505/debt | 23505 RD...I. 23505 23505,gkpj 23505 uq_debts_debtor_creditor_equivalent
dbapi code 23505/tx | 23505 ..X.... None 23505,gkpj 23505 transactions_tx_id_key
dbapi code 23505/live | 23505 ......T None 23505,gkpj 23505 uq_trust_lines_live_from_to_equivalent
dbapi code 23503 | 23503 ....... None 23503,gkpj 23503 fk_probe
dbapi code none | None ....... None dbapi DBAPIError None
cause asyncpg 40001 | 40001 R..C... None 40001,dbapi 40001 None
cause asyncpg 40P01 | 40P01 R..C... None 40P01,dbapi 40P01 None
cause asyncpg 55P03 | 55P03 ....L.. None 55P03,dbapi 55P03 None
cause asyncpg 23505/debt | 23505 RD..... None 23505,gkpj 23505 uq_debts_debtor_creditor_equivalent
cause asyncpg 23505/tx | 23505 ....... None 23505,gkpj 23505 transactions_tx_id_key
cause asyncpg 23505/live | 23505 ....... None 23505,gkpj 23505 uq_trust_lines_live_from_to_equivalent
cause asyncpg 23503 | 23503 ....... None 23503,gkpj 23503 fk_probe
cause asyncpg none | None ....... None dbapi DBAPIError None
cause code 40001 | 40001 R..C... None 40001,dbapi 40001 None
cause code 40P01 | 40P01 R..C... None 40P01,dbapi 40P01 None
cause code 55P03 | 55P03 ....L.. None 55P03,dbapi 55P03 None
cause code 23505/debt | 23505 RD..... None 23505,gkpj 23505 uq_debts_debtor_creditor_equivalent
cause code 23505/tx | 23505 ....... None 23505,gkpj 23505 transactions_tx_id_key
cause code 23505/live | 23505 ....... None 23505,gkpj 23505 uq_trust_lines_live_from_to_equivalent
cause code 23503 | 23503 ....... None 23503,gkpj 23503 fk_probe
cause code none | None ....... None dbapi DBAPIError None
context asyncpg 40001 | None ....... None - RuntimeError None
context asyncpg 40P01 | None ....... None - RuntimeError None
context asyncpg 55P03 | None ....... None - RuntimeError None
context asyncpg 23505/debt | None ....... None - RuntimeError None
context asyncpg 23505/tx | None ....... None - RuntimeError None
context asyncpg 23505/live | None ....... None - RuntimeError None
context asyncpg 23503 | None ....... None - RuntimeError None
context asyncpg none | None ....... None - RuntimeError None
context code 40001 | None ....... None - RuntimeError None
context code 40P01 | None ....... None - RuntimeError None
context code 55P03 | None ....... None - RuntimeError None
context code 23505/debt | None ....... None - RuntimeError None
context code 23505/tx | None ....... None - RuntimeError None
context code 23505/live | None ....... None - RuntimeError None
context code 23503 | None ....... None - RuntimeError None
context code none | None ....... None - RuntimeError None
masked asyncpg 40001 | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg 40P01 | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg 55P03 | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg 23505/debt | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg 23505/tx | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg 23505/live | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg 23503 | 23502 ....... None 23502,gkpj 23502 None
masked asyncpg none | 23502 ....... None 23502,gkpj 23502 None
masked code 40001 | 23502 ....... None 23502,gkpj 23502 None
masked code 40P01 | 23502 ....... None 23502,gkpj 23502 None
masked code 55P03 | 23502 ....... None 23502,gkpj 23502 None
masked code 23505/debt | 23502 ....... None 23502,gkpj 23502 None
masked code 23505/tx | 23502 ....... None 23502,gkpj 23502 None
masked code 23505/live | 23502 ....... None 23502,gkpj 23502 None
masked code 23503 | 23502 ....... None 23502,gkpj 23502 None
masked code none | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 40001 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 40P01 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 55P03 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 23505/debt | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 23505/tx | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 23505/live | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg 23503 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx asyncpg none | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 40001 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 40P01 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 55P03 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 23505/debt | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 23505/tx | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 23505/live | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code 23503 | 23502 ....... None 23502,gkpj 23502 None
orig_ctx code none | 23502 ....... None 23502,gkpj 23502 None
nested asyncpg 40001 | 40001 R..C... None 40001,dbapi 40001 None
nested asyncpg 40P01 | 40P01 R..C... None 40P01,dbapi 40P01 None
nested asyncpg 55P03 | 55P03 ....L.. None 55P03,dbapi 55P03 None
nested asyncpg 23505/debt | 23505 RD...I. 23505 23505,gkpj 23505 uq_debts_debtor_creditor_equivalent
nested asyncpg 23505/tx | 23505 ..X.... None 23505,gkpj 23505 transactions_tx_id_key
nested asyncpg 23505/live | 23505 ......T None 23505,gkpj 23505 uq_trust_lines_live_from_to_equivalent
nested asyncpg 23503 | 23503 ....... None 23503,gkpj 23503 fk_probe
nested asyncpg none | None ....... None dbapi DBAPIError None
nested code 40001 | 40001 R..C... None 40001,dbapi 40001 None
nested code 40P01 | 40P01 R..C... None 40P01,dbapi 40P01 None
nested code 55P03 | 55P03 ....L.. None 55P03,dbapi 55P03 None
nested code 23505/debt | 23505 RD...I. 23505 23505,gkpj 23505 uq_debts_debtor_creditor_equivalent
nested code 23505/tx | 23505 ..X.... None 23505,gkpj 23505 transactions_tx_id_key
nested code 23505/live | 23505 ......T None 23505,gkpj 23505 uq_trust_lines_live_from_to_equivalent
nested code 23503 | 23503 ....... None 23503,gkpj 23503 fk_probe
nested code none | None ....... None dbapi DBAPIError None
non_db | None ....... None - ValueError None
no_orig | None ....... None gkpj IntegrityError None
""".strip().splitlines()


def test_every_policy_answers_every_shape_as_pinned():
    actual = _matrix()
    assert len(actual) == len(SHAPES) * len(CARRIERS) * len(CODES) + 2 == 114
    mismatches = [f"- {want}\n+ {got}" for want, got in zip(EXPECTED, actual) if want != got]
    assert len(EXPECTED) == len(actual) and not mismatches, "\n".join(mismatches) or "\n".join(actual)


@pytest.mark.parametrize("carrier", CARRIERS)
def test_trustline_classifier_does_not_read_context(carrier):
    """F-024-10: a not-null violation raised while a live-line clash was being handled is not that clash.

    The trust-line classifier walked `__cause__ or __context__` (rule 2026-09-12: never `__context__`),
    so the terminal inherited the clash's constraint and `create` answered "trust line already exists"
    (409) for an unrelated failure. Control first: the same clash reached through deliberate wrapping is
    still recognised, so the fix cannot pass by recognising nothing.
    """
    for shape in ("dbapi", "nested"):
        assert _is_live_trustline_uniqueness_violation(_shape(shape, carrier, "23505/live")) is True
    masked = _shape("orig_ctx", carrier, "23505/live")
    require_target(
        _is_live_trustline_uniqueness_violation(masked) is False,
        f"{carrier}: a 23502 raised while handling a live-line clash was classified as the clash",
    )
