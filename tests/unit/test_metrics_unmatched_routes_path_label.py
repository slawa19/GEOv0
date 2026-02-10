import uuid

import pytest

from app.utils.metrics import HTTP_REQUESTS_TOTAL


def _counter_value(labels: dict[str, str]) -> float:
    # Do not call `.labels(...)` here: it would create the time series even when
    # we want to assert it was NOT created.
    for metric in HTTP_REQUESTS_TOTAL.collect():
        for sample in metric.samples:
            if sample.name == "geo_http_requests_total" and sample.labels == labels:
                return float(sample.value)
    return 0.0


@pytest.mark.asyncio
async def test_unmatched_route_uses_fixed_path_label(client):
    random_path = f"/random/{uuid.uuid4()}"

    unmatched_labels = {"method": "GET", "path": "__unmatched__", "status": "404"}
    raw_labels = {"method": "GET", "path": random_path, "status": "404"}

    before_unmatched = _counter_value(unmatched_labels)
    before_raw = _counter_value(raw_labels)

    resp = await client.get(random_path)
    assert resp.status_code == 404

    after_unmatched = _counter_value(unmatched_labels)
    after_raw = _counter_value(raw_labels)

    assert after_unmatched == before_unmatched + 1
    assert after_raw == before_raw

