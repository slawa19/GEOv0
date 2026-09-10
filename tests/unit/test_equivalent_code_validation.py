import pytest

from app.utils.exceptions import BadRequestException
from app.utils.validation import validate_equivalent_code, validate_equivalent_precision


@pytest.mark.parametrize(
    "code",
    [
        "USD",
        "HOUR",
        "A",
        "A1",
        "A_1",
        "ABCDEFGHIJKLMNO1",  # 16 chars
    ],
)
def test_validate_equivalent_code_accepts_valid_codes(code: str) -> None:
    validate_equivalent_code(code)


@pytest.mark.parametrize(
    "code",
    [
        "",  # empty
        "usd",  # lowercase
        "Usd",  # mixed
        "A-B",  # invalid char
        "A B",  # space
        "AAAAAAAAAAAAAAAAA",  # 17 chars
        None,
    ],
)
def test_validate_equivalent_code_rejects_invalid_codes(code) -> None:
    with pytest.raises(BadRequestException):
        validate_equivalent_code(code)


#: Both ENDS of the domain. The upper one moved 18 -> 8 on 2026-08-25 (012 / S1) with the bound
#: itself: `Equivalent.precision` is now the storage scale of `Numeric(20, 8)`, which is also
#: what protocol §3.2 declares (`docs/ru/02-protocol-spec.md:155`).
@pytest.mark.parametrize("precision", [0, 8])
def test_validate_equivalent_precision_accepts_integer_bounds(precision: int) -> None:
    assert validate_equivalent_precision(precision) == precision


#: `9` is the FIRST value past the upper end and is the one that probes the bound; it was added
#: with the narrowing. `19` is kept - it was the original case, it must stay refused, and the two
#: together say the refusal is a range and not a single forbidden number. Without `9` this test
#: would have stayed green while the bound silently moved back to 18.
@pytest.mark.parametrize("precision", [-1, 9, 19, None, "2", 2.0, True])
def test_validate_equivalent_precision_rejects_noncanonical_values(precision) -> None:
    with pytest.raises(BadRequestException, match="Invalid equivalent precision"):
        validate_equivalent_precision(precision)
