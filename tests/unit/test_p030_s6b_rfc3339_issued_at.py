"""030 S6b (review of S5 `T3095` #2, P3): `issued_at` and `expected.close_requested_at` of a signed line operation are
RFC 3339 date-times with an explicit offset or `Z` - not whatever `datetime.fromisoformat` happens to take (Python
3.11 accepts any letter between date and time, a space, and the basic format `20261006T120000Z`)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.trustlines.service import _parse_rfc3339
from app.utils.exceptions import BadRequestException


@pytest.mark.parametrize("text", ["2026-10-06X12:00:00+00:00", "2026-10-06 12:00:00+00:00", "20261006T120000Z",
                                  "2026-10-06T12:00:00", "2026-10-06T12:00:00+0000", "2026-10-06T12:00Z",
                                  "2026-10-06", 12, None,
                                  # offset out of RFC 3339 §5.6 range: 030 S6 `T3096` #4 (minutes 60..99 were taken)
                                  "2026-10-06T12:00:00+00:60", "2026-10-06T12:00:00-00:99", "2026-10-06T12:00:00+24:00"])
def test_a_form_outside_rfc3339_is_refused(text) -> None:
    with pytest.raises(BadRequestException) as refused:
        _parse_rfc3339(text, field="issued_at")
    assert refused.value.details == {"field": "issued_at", "reason": "not_rfc3339"}


@pytest.mark.parametrize("text,moment", [
    ("2026-10-06T12:00:00Z", datetime(2026, 10, 6, 12, tzinfo=timezone.utc)),
    ("2026-10-06T12:00:00.250000+02:00", datetime(2026, 10, 6, 12, 0, 0, 250000, tzinfo=timezone(timedelta(hours=2)))),
    ("2026-10-06T12:00:00.1234567-00:30", datetime(2026, 10, 6, 12, 0, 0, 123456, tzinfo=timezone(-timedelta(minutes=30)))),
])
def test_control_an_rfc3339_date_time_is_parsed(text, moment) -> None:
    assert _parse_rfc3339(text, field="issued_at") == moment
