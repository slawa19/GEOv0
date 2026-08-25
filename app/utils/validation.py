import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.utils.exceptions import BadRequestException
from app.schemas.equivalent import normalize_equivalent_metadata


_EQUIVALENT_CODE_RE = re.compile(r"^[A-Z0-9_]{1,16}$")


def validate_equivalent_code(code: str) -> None:
    if not isinstance(code, str) or not code or not _EQUIVALENT_CODE_RE.fullmatch(code):
        raise BadRequestException("Invalid equivalent code")


def validate_equivalent_precision(precision: int) -> int:
    if (
        not isinstance(precision, int)
        or isinstance(precision, bool)
        or precision < 0
        or precision > 18
    ):
        raise BadRequestException("Invalid equivalent precision")
    return precision


def validate_equivalent_metadata(metadata: Any) -> dict | None:
    """Validate and normalize Equivalent.metadata.

    - metadata.type must be one of: fiat, time, commodity, custom
    - iso_code is optional; if provided it must be 3 uppercase letters and only for type=fiat

    Returns normalized dict (or None).
    """
    try:
        return normalize_equivalent_metadata(metadata)
    except Exception as exc:
        raise BadRequestException(f"Invalid equivalent metadata: {exc}")


def validate_idempotency_key(key: str) -> str:
    if not isinstance(key, str):
        raise BadRequestException("Invalid Idempotency-Key")

    normalized = key.strip()
    if not normalized:
        raise BadRequestException("Invalid Idempotency-Key")
    if len(normalized) > 128:
        raise BadRequestException("Idempotency-Key too long")

    return normalized


_TX_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def validate_tx_id(tx_id: str) -> str:
    """Validate client-generated tx_id (idempotency key).

    MVP constraints:
    - required non-empty string
    - max length 64 (matches DB column)
    - limited charset to avoid whitespace/control chars
    """

    if not isinstance(tx_id, str):
        raise BadRequestException("Invalid tx_id")

    normalized = tx_id.strip()
    if not normalized:
        raise BadRequestException("Invalid tx_id")
    if len(normalized) > 64:
        raise BadRequestException("tx_id too long")
    if not _TX_ID_RE.fullmatch(normalized):
        raise BadRequestException("Invalid tx_id")

    return normalized


# --- Amount validation ---
#
# Canonical JSON constraints for numeric values (protocol) require us to be strict about
# special values and representation:
# - forbid NaN/Infinity
# - forbid exponent notation (e/E)
#
# Additionally we enforce conservative scale/precision bounds to avoid pathological inputs
# (e.g. extremely long fractions).

# 012/T1201: this was 18 while every money column is Numeric(20, 8), which is the whole of
# `F-012-1` - a participant signed one number and the ledger kept another, larger one. It is now
# the storage scale, so a money path that reached for `parse_amount_decimal` without arguments
# could not re-open the hole.
#
# HOW MUCH THIS CONSTANT ACTUALLY DEFENDS: nothing today, and the first edition of this comment
# claimed otherwise.  It said "widening it re-opens F-012-1, and `test_p012_rt1_...` demonstrates
# that flip".  Measured 2026-08-24 by putting it back to 18: five of the six tests in that module
# stayed green, and the single failure was the counter-check asserting this constant against a
# literal.  The reason is that `parse_money_amount` - the only door money passes through - always
# passes its bounds explicitly, and no production caller reaches `parse_amount_decimal` bare
# (`grep -rn parse_amount_decimal app/` finds this file only).  So this is a conservative default
# for a future bare caller, not the money bound.  The money bound is `MONEY_MAX_SCALE` below, and
# the counter-check in `test_p012_rt1_...` now flips THAT one, over HTTP.
DEFAULT_MAX_AMOUNT_SCALE = 8
DEFAULT_MAX_AMOUNT_PRECISION = 50  # total digits in the decimal string (excluding sign and '.')

# --- Money: the storage-capacity door (012 / F-012-1, T1201) ---
#
# `Debt.amount` and `TrustLine.limit` are `Numeric(20, 8)` (`app/db/models/debt.py`,
# `app/db/models/trustline.py`).  PostgreSQL does NOT truncate what does not fit: it rounds,
# in both directions, and it rounds silently.  Measured on PostgreSQL 16.9:
#
#   0.123456789          -> 0.12345679   (the ledger keeps MORE than was signed)
#   0.000000001          -> 0            (the value disappears; the positivity CHECK then
#                                         fires and escapes as HTTP 500 E010)
#   1000000000000        -> NumericValueOutOfRangeError, i.e. a 500 as well
#
# Both bounds below are therefore load-bearing, and they are two different escapes of the
# same class.  `MONEY_MAX_SCALE` is the fraction the column can hold; `MONEY_MAX_INTEGER_DIGITS`
# is what is left of `precision 20` once the scale is spent, i.e. `abs(value) < 10**12`.
# Narrowing the scale alone still admits `12345678901234567890`, which PostgreSQL refuses.
#
# These are facts about the COLUMN, not about `Equivalent.precision`.  Rejecting on precision
# is a separate, deferred product decision (variant B): precision is declared `ge=0, le=18`,
# so it does not even close this finding, and an admin editing it would retroactively
# invalidate stored rows.
MONEY_MAX_SCALE = 8
MONEY_MAX_INTEGER_DIGITS = 12

# The door's one LEXICAL bound, and it is not a storage bound.
#
# 012/T1201 first edition bounded the money door on the SCALE OF THE STRING, which refused
# `"0.100000000"` - a value `Numeric(20, 8)` holds exactly, and one this repository's own
# renderer produces: `to_money_str` treats `Equivalent.precision` as a MINIMUM number of
# fraction digits, so any equivalent with `precision > 8` renders `0.1` as `"0.1000000000"` and
# the door then answered 400/E009 to its own output.  Judging the spelling instead of the value
# is what made the door and `is_storable_money` - documented as its predicate form - disagree.
#
# What survives of the lexical rule is a bound on LENGTH, for pathological input only: a caller
# may not spell a fraction longer than any of our own producers can emit for money the ledger
# holds.  18 is that number because `Equivalent.precision` is declared `ge=0, le=18`
# (`app/schemas/equivalents.py:39`, `app/schemas/admin.py:218`) and `to_money_str` pads to
# `precision`, so for a value that fits `Numeric(20, 8)` it returns at most 18 fraction digits.
# It is also exactly the scale the door accepted before T1201, so nothing that used to be
# admitted becomes a 400 because of this bound.
#
# CAVEAT (012, second round): the `le=18` this leans on is itself contested - protocol §3.2
# declares `precision` as 0..8 (`docs/ru/02-protocol-spec.md:143,155`), and whether the document
# or the schema is the one to move is recorded as an open fork in spec 015.  Whichever way that
# lands, this bound only gets tighter (8 also covers every producer), so no admitted spelling is
# retroactively wrong - the number just should not be read as protocol-derived until 015 answers.
MONEY_MAX_LEXICAL_SCALE = 18

_AMOUNT_STR_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_FORBIDDEN_AMOUNT_LITERALS = {
    "nan",
    "inf",
    "+inf",
    "-inf",
    "infinity",
    "+infinity",
    "-infinity",
}


def parse_amount_decimal(
    amount: Any,
    *,
    max_scale: int | None = DEFAULT_MAX_AMOUNT_SCALE,
    max_precision: int | None = DEFAULT_MAX_AMOUNT_PRECISION,
    max_integer_digits: int | None = None,
    require_positive: bool = False,
    field: str | None = None,
) -> Decimal:
    """Parse and validate an API amount as a strict decimal string.

    Accepts only plain decimal strings like: 0, 0.1, 10.00, 0001.23

    Rejects:
    - NaN / Infinity / -Infinity
    - exponent notation (e/E)
    - leading/trailing whitespace
    - empty / non-numeric strings
    - excessive scale/precision (conservative limits)

    If require_positive=True, enforces amount > 0.

    Raises BadRequestException("Invalid amount format") or
    BadRequestException("Amount must be positive").

    NOTE ON THE DEFAULTS.  These are generic parser bounds, not storage bounds: the default
    `max_scale` of 18 is wider than the `Numeric(20, 8)` money columns.  Anything that will be
    written to `Debt.amount` or `TrustLine.limit` must go through `parse_money_amount` instead,
    which supplies the capacity bounds.  See the `MONEY_MAX_*` note above.

    `field` is diagnostic only: when given, the raised `BadRequestException` carries
    `details` naming the offending field and the bound it broke.  The messages themselves are
    deliberately unchanged - they are asserted verbatim by
    `tests/integration/test_payments_amount_validation.py` and are part of the public 400/E009
    shape.
    """

    def _reject(message: str, **extra: Any) -> BadRequestException:
        if field is None:
            return BadRequestException(message)
        details: dict[str, Any] = {"field": field}
        details.update(extra)
        return BadRequestException(message, details=details)

    if amount is None:
        raise _reject("Invalid amount format")

    amount_str = amount if isinstance(amount, str) else str(amount)

    if not amount_str:
        raise _reject("Invalid amount format")

    # No implicit normalization: reject any surrounding whitespace.
    if amount_str != amount_str.strip():
        raise _reject("Invalid amount format")

    lowered = amount_str.lower()

    # Explicitly forbid special float-like literals even if some callers pass them as strings.
    if lowered in _FORBIDDEN_AMOUNT_LITERALS:
        raise _reject("Invalid amount format")

    # Exponent notation is forbidden (canonical JSON recommendation and DoS hardening).
    if "e" in lowered:
        raise _reject("Invalid amount format")

    # Strict decimal string: optional '-' then digits, optional fraction with at least 1 digit.
    # Note: '+' is intentionally NOT supported.
    if _AMOUNT_STR_RE.fullmatch(amount_str) is None:
        raise _reject("Invalid amount format")

    unsigned = amount_str[1:] if amount_str.startswith("-") else amount_str
    if "." in unsigned:
        int_part, frac_part = unsigned.split(".", 1)
        scale = len(frac_part)
    else:
        int_part, frac_part = unsigned, ""
        scale = 0

    if max_scale is not None and scale > max_scale:
        raise _reject("Invalid amount format", max_scale=max_scale)

    if max_precision is not None and (len(int_part) + len(frac_part)) > max_precision:
        raise _reject("Invalid amount format", max_precision=max_precision)

    # Magnitude, i.e. how many integer digits the target can hold.  Counted on the
    # SIGNIFICANT digits so that the long-standing tolerance for leading zeros ("0001.23")
    # is preserved: it is the VALUE that has to fit, not the spelling.
    if max_integer_digits is not None:
        significant_int_digits = len(int_part.lstrip("0"))
        if significant_int_digits > max_integer_digits:
            raise _reject(
                "Invalid amount format", max_integer_digits=max_integer_digits
            )

    try:
        as_decimal = Decimal(amount_str)
    except (InvalidOperation, ValueError):
        raise _reject("Invalid amount format")

    # Decimal() could still theoretically produce non-finite values for special inputs,
    # so keep an explicit guard.
    if not as_decimal.is_finite():
        raise _reject("Invalid amount format")

    if require_positive and as_decimal <= 0:
        raise _reject("Amount must be positive")

    return as_decimal


def is_storable_money(value: Any) -> bool:
    """True when `value` fits `Numeric(20, 8)` exactly - no rounding, no overflow.

    THE money capacity rule, and the only one.  `parse_money_amount` below is this predicate
    plus an HTTP-shaped refusal and the wire grammar; the writers that do not answer an HTTP
    request and therefore cannot raise a 400 (scenario seeding, inject execution: they build
    `Decimal`s out of arbitrary config and drop rows they cannot use) call it directly, with
    their own skip-and-log blast radius.

    It is a question about a VALUE, not about how the value was written.  `Decimal("0.1")`,
    `Decimal("0.100000000")` and `Decimal("1E-1")` are the same number and get the same answer,
    because `Numeric(20, 8)` gives them the same answer.
    """

    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return False
    if not value.is_finite():
        return False

    # Magnitude first: `quantize` below raises on operands too large for the arithmetic
    # context, so asking about the fraction before the magnitude would fail for exactly the
    # values this predicate exists to reject.
    if abs(value) >= Decimal(10) ** MONEY_MAX_INTEGER_DIGITS:
        return False

    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= -MONEY_MAX_SCALE:
        return True
    # More fraction digits than the column declares - storable only if they are all zero,
    # because PostgreSQL would otherwise round rather than truncate.
    try:
        return value == value.quantize(Decimal("1E-%d" % MONEY_MAX_SCALE))
    except InvalidOperation:
        return False


def parse_money_amount(
    amount: Any,
    *,
    field: str = "amount",
    require_positive: bool = False,
    require_non_negative: bool = False,
) -> Decimal:
    """The money door: parse an amount the ledger columns can hold exactly, or refuse it.

    Two rules, and they are deliberately of different kinds:

    * the WIRE GRAMMAR, applied to strings - a plain decimal string and nothing else: no
      `float` literals, no exponent notation, no NaN/Infinity, no surrounding whitespace, at
      most `MONEY_MAX_LEXICAL_SCALE` fraction digits and `DEFAULT_MAX_AMOUNT_PRECISION` digits
      in total.  That is a bound on what may be *written*, for pathological input.
    * the CAPACITY RULE, applied to the value: `is_storable_money`, i.e. the value must survive
      `Numeric(20, 8)` unchanged - representable at scale 8 and `abs(value) < 10**12`.

    Refusal is `BadRequestException` -> HTTP 400 / `E009`, the existing classification for a
    scale overflow.  No new error code: callers that own a different envelope (the simulator
    Interact API) translate it themselves.

    WHY THE CAPACITY RULE IS ON THE VALUE (012/T1201, fixed 2026-08-24).  The first edition
    bounded the *lexical* scale of the string at 8 and called `is_storable_money` "the predicate
    form of `parse_money_amount`" ten lines below.  They disagreed, and the door was the wrong
    one of the two:

        '0.100000000'      door REFUSED, is_storable_money True, Numeric(20,8) holds it exactly
        '1000.0000000000'  door REFUSED, is_storable_money True, ditto
        '1E+3'             door REFUSED, is_storable_money True, ditto

    `## Intended` licenses refusing *the value that cannot be stored exactly*.  `0.1` with
    trailing zeros is not that value, and refusing it was a compatibility break the programme
    inflicted on itself twice: `to_money_str` treats `Equivalent.precision` as a MINIMUM number
    of fraction digits, so any equivalent with `precision > 8` renders `0.1` as `"0.1000000000"`
    and the door answered 400/E009 to its own renderer; and `POST /trustlines` refused
    `"limit": "1e3"` while accepting `"limit": 1e3`.  The door now asks the same question the
    predicate asks, by calling it.

    WHY BOTH CAPACITY BOUNDS.  `Numeric(20, 8)` is 20 significant digits of which 8 are the
    fraction.  Values that overflow either half are lost differently and both losses are silent
    to the caller: too many fraction digits are ROUNDED by PostgreSQL (in both directions, so
    the ledger can end up holding more than was signed), while too large a magnitude raises
    `numeric field overflow` deep inside the commit and escapes as HTTP 500.  Narrowing only the
    scale leaves the second escape open.

    EXPONENT NOTATION, DECIDED RATHER THAN INHERITED.  A `str` carrying `e`/`E` is refused, and
    a `Decimal` whose `str()` happens to carry one is not.  The programme's invariant ("money
    never leaves in exponent form") is an OUTPUT rule, so it does not settle the input question
    by itself; two things do.  First, `T1209` has already told 011 that the canon must declare
    money fields with `pattern: ^-?\\d+(\\.\\d+)?$` - accepting `"1e3"` here would contradict, on
    the server, the contract 012 is asking the canon to publish.  Second, for `POST /payments`
    the signature is taken over the amount STRING verbatim, so an accepted `"1e3"` would put
    exponent-form money into `transactions.payload`, i.e. into stored history - which is the
    output rule after all.

    But a `Decimal` is a value that has already been parsed, and its spelling is Python's, not
    the client's - so a `Decimal` argument is re-spelled plainly (`format(v, "f")`) before the
    grammar runs, and only the capacity rule can refuse it.  That branch now serves INTERNAL
    callers (the clearing engine, admin repairs); no HTTP entrance feeds this door a `Decimal`
    any more.  `trustlines/service.py` used to: `limit` was typed `Decimal` on the schema, so
    `{"limit": "1e3"}` arrived as `Decimal('1E+3')` and `{"limit": 1e3}` as `Decimal('1000')`,
    the door refused one spelling and admitted the other for the same value, and - worse - the
    service rebuilt the SIGNED payload from `str(data.limit)`, so for `"0.00000001"` the client
    signed `"0.00000001"` while the server verified against `"1E-8"`, and the smallest storable
    limit was unsignable.  The honest fix was the one this note used to defer as "owned by the
    canon": `api/openapi.yaml` had declared `limit: type: string` all along, and the schema now
    agrees (`TrustLineCreateRequest.limit: str`), so the trust-line door sees the client's own
    string, exponent notation is refused where a client writes it, and the signature covers the
    verbatim string - exactly the `POST /payments` contract.
    `tests/integration/test_p012_t1201_money_door_at_the_entrances.py` holds the decision.

    WHY NOT `Equivalent.precision`.  Deferred by product decision (`VERDICT-DOOR: C`):
    precision is a representation parameter declared `ge=0, le=18`, so it neither bounds the
    column nor closes this finding, and an admin lowering it would retroactively invalidate
    stored rows.
    """

    def _reject(message: str, **extra: Any) -> BadRequestException:
        details: dict[str, Any] = {"field": field}
        details.update(extra)
        return BadRequestException(message, details=details)

    if isinstance(amount, Decimal):
        # A value, not a spelling - see EXPONENT NOTATION above.  Non-finite `Decimal`s are
        # left to the grammar, which already owns that vocabulary.
        amount = format(amount, "f") if amount.is_finite() else str(amount)

    value = parse_amount_decimal(
        amount,
        max_scale=MONEY_MAX_LEXICAL_SCALE,
        max_precision=DEFAULT_MAX_AMOUNT_PRECISION,
        max_integer_digits=None,
        # Positivity is checked below, AFTER capacity, so that every input refused before this
        # change keeps the message it had: `parse_amount_decimal` would otherwise answer
        # "Amount must be positive" for e.g. "-0.123456789", which used to be
        # "Invalid amount format".
        require_positive=False,
        field=field,
    )

    if not is_storable_money(value):
        # The same `details` shape the lexical bounds used to raise, so the two capacity
        # failures stay distinguishable to a client: the fraction is lost by rounding, the
        # magnitude by overflow.
        if abs(value) >= Decimal(10) ** MONEY_MAX_INTEGER_DIGITS:
            raise _reject(
                "Invalid amount format", max_integer_digits=MONEY_MAX_INTEGER_DIGITS
            )
        raise _reject("Invalid amount format", max_scale=MONEY_MAX_SCALE)

    if require_positive and value <= 0:
        raise _reject("Amount must be positive")

    # A trust-line limit may be zero but not negative.  This lived on the pydantic schema as
    # `ge=0` while `limit` was typed `Decimal`; when the field became a `str` (signed verbatim,
    # see `TrustLineCreateRequest.limit`) the bound moved here, behind the same door and with
    # the same refusal envelope as every other money rule.
    if require_non_negative and value < 0:
        raise _reject("Amount must be non-negative")

    return value


_ALLOWED_TRUSTLINE_POLICY_KEYS = {
    "auto_clearing",
    "can_be_intermediate",
    "max_hop_usage",
    "daily_limit",
    "blocked_participants",
}


def validate_trustline_policy(policy: dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise BadRequestException("Invalid trustline policy")

    unknown = set(policy.keys()) - _ALLOWED_TRUSTLINE_POLICY_KEYS
    if unknown:
        raise BadRequestException(f"Unknown trustline policy keys: {sorted(unknown)}")

    if "auto_clearing" in policy and policy["auto_clearing"] is not None and not isinstance(policy["auto_clearing"], bool):
        raise BadRequestException("trustline.policy.auto_clearing must be boolean")

    if "can_be_intermediate" in policy and policy["can_be_intermediate"] is not None and not isinstance(policy["can_be_intermediate"], bool):
        raise BadRequestException("trustline.policy.can_be_intermediate must be boolean")

    for key in ("max_hop_usage", "daily_limit"):
        if key not in policy or policy[key] is None:
            continue
        value = policy[key]
        if key == "daily_limit":
            # `daily_limit` IS a money quantity by the protocol, so a STRING here obeys the
            # money wire GRAMMAR - plain decimal, no exponent, no NaN/Infinity (the same
            # input-form rule as every other money string in 012).  What it does NOT obey,
            # since T1210 finding 12, is the STORAGE CAPACITY rule: this value lives in the
            # `policy` JSON column, not in a `Numeric(20, 8)` money column, so bounding it to
            # scale 8 / magnitude 10**12 refused values the column stores verbatim - a
            # forward-looking rule for an enforcement day that has not come, on a column that
            # has no capacity to protect.  A JSON NUMBER stays admissible because the canon
            # declares the field `oneOf` string|number|null; a number is a parsed value, not
            # a client spelling, so it is re-spelled plainly before the grammar rather than
            # judged on how Python would print it (`1e16` arrives as a float and must not be
            # refused for `str()`'s exponent).  REACHABILITY CAVEAT (T1210-bis): on the signed
            # HTTP path a FRACTIONAL number never gets this far today - `canonical_json`
            # refuses floats, so it dies before `verify_signature` with an honest 400 (see
            # trustlines/service.py), and only integers arrive here as numbers.  That the
            # canon admits a number the canonical form cannot carry is a recorded contract
            # fork, not this validator's to settle; the value rule here stays caller-agnostic
            # so whichever way the fork lands, no number is refused for its Python spelling.
            #
            # Refusals keep the grammar's own message and `details` instead of the blanket
            # "must be a number" the first edition raised: a capacity complaint relabelled as
            # a type complaint told the client its number was not a number.  "must be a
            # number" is reserved for values of a non-numeric TYPE (bool, dict, list, ...);
            # a malformed string gets the grammar's "Invalid amount format" naming the field.
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                raise BadRequestException(f"trustline.policy.{key} must be a number")
            candidate = value
            if not isinstance(candidate, str):
                try:
                    candidate = format(Decimal(str(candidate)), "f")
                except (InvalidOperation, ValueError):
                    raise BadRequestException(f"trustline.policy.{key} must be a number")
            as_decimal = parse_amount_decimal(
                candidate,
                max_scale=MONEY_MAX_LEXICAL_SCALE,
                max_precision=DEFAULT_MAX_AMOUNT_PRECISION,
                max_integer_digits=None,
                require_positive=False,
                field=f"policy.{key}",
            )
        else:
            try:
                as_decimal = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise BadRequestException(f"trustline.policy.{key} must be a number")
        if as_decimal < 0:
            raise BadRequestException(f"trustline.policy.{key} must be >= 0")

    if "blocked_participants" in policy and policy["blocked_participants"] is not None:
        value = policy["blocked_participants"]
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise BadRequestException("trustline.policy.blocked_participants must be a list of strings")
