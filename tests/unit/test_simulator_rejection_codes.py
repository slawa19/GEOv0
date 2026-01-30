import pytest


@pytest.mark.parametrize(
    "err_details, expected",
    [
        ({"exc": "RoutingException", "geo_code": "E002"}, "ROUTING_NO_CAPACITY"),
        ({"exc": "RoutingException", "geo_code": "E001"}, "ROUTING_NO_ROUTE"),
        ({"exc": "RoutingException", "geo_code": "E999"}, "ROUTING_REJECTED"),
        ({"exc": "TrustLineException", "geo_code": "E003"}, "TRUSTLINE_LIMIT_EXCEEDED"),
        ({"exc": "TrustLineException", "geo_code": "E004"}, "TRUSTLINE_NOT_ACTIVE"),
        ({"exc": "TrustLineException", "geo_code": "E999"}, "TRUSTLINE_REJECTED"),
        ({"exc": "NotFoundException", "geo_code": "E001", "message": "Equivalent USD not found"}, "EQUIVALENT_NOT_FOUND"),
        (
            {"exc": "NotFoundException", "geo_code": "E001", "message": "Participants not found: ['a']"},
            "PARTICIPANT_NOT_FOUND",
        ),
        ({"exc": "NotFoundException", "geo_code": "E001", "message": "Transaction 123 not found"}, "TX_NOT_FOUND"),
        ({"exc": "BadRequestException", "geo_code": "E009"}, "INVALID_INPUT"),
        ({"exc": "ConflictException", "geo_code": "E008"}, "CONFLICT"),
        ({"exc": "UnauthorizedException", "geo_code": "E006"}, "UNAUTHORIZED"),
        ({"exc": "ForbiddenException", "geo_code": "E006"}, "FORBIDDEN"),
        ({"exc": "InvalidSignatureException", "geo_code": "E005"}, "INVALID_SIGNATURE"),
        ({"exc": "CryptoException", "geo_code": "E005"}, "INVALID_SIGNATURE"),
        ({"exc": "SomeOtherException", "geo_code": "E000"}, "PAYMENT_REJECTED"),
        (None, "PAYMENT_REJECTED"),
        ("not-a-dict", "PAYMENT_REJECTED"),
    ],
)
def test_map_rejection_code(err_details, expected):
    from app.core.simulator.runtime import _map_rejection_code

    assert _map_rejection_code(err_details) == expected
