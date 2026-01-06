import pytest

from app.utils.validation import validate_equivalent_metadata


def test_equivalent_metadata_allows_none():
    assert validate_equivalent_metadata(None) is None


def test_equivalent_metadata_valid_fiat_with_iso_code():
    assert validate_equivalent_metadata({"type": "fiat", "iso_code": "UAH"}) == {"type": "fiat", "iso_code": "UAH"}


def test_equivalent_metadata_valid_time_without_iso_code():
    assert validate_equivalent_metadata({"type": "time"}) == {"type": "time", "iso_code": None}


@pytest.mark.parametrize(
    "meta",
    [
        {},
        {"type": ""},
        {"type": "unknown"},
        {"type": "fiat", "iso_code": "uah"},
        {"type": "fiat", "iso_code": "USDT"},
        {"type": "time", "iso_code": "UAH"},
    ],
)
def test_equivalent_metadata_invalid(meta):
    with pytest.raises(Exception):
        validate_equivalent_metadata(meta)
