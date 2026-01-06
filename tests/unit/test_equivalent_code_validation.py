import pytest

from app.utils.exceptions import BadRequestException
from app.utils.validation import validate_equivalent_code


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
