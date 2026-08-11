from unittest.mock import MagicMock

import pytest

from app.api import deps
from app.config import settings


def _request() -> MagicMock:
    request = MagicMock()
    request.method = "GET"
    request.headers = {}
    request.cookies = {}
    request.client.host = "untrusted.example"
    return request


def test_admin_token_match_uses_compare_digest(monkeypatch) -> None:
    calls: list[tuple[bytes, bytes]] = []

    def fake_compare_digest(candidate: bytes, expected: bytes) -> bool:
        calls.append((candidate, expected))
        return candidate == expected

    monkeypatch.setattr(settings, "ADMIN_TOKEN", "expected-admin-token")
    monkeypatch.setattr(deps.secrets, "compare_digest", fake_compare_digest)

    assert deps._admin_token_matches("expected-admin-token") is True
    assert deps._admin_token_matches("wrong-admin-token") is False
    assert calls == [
        (b"expected-admin-token", b"expected-admin-token"),
        (b"wrong-admin-token", b"expected-admin-token"),
    ]


@pytest.mark.asyncio
async def test_every_admin_dependency_path_uses_shared_token_matcher(monkeypatch) -> None:
    calls: list[str] = []

    def matcher(candidate: str) -> bool:
        calls.append(candidate)
        return True

    monkeypatch.setattr(deps, "_admin_token_matches", matcher)

    await deps.require_participant_or_admin(
        db=MagicMock(),
        token=None,
        x_admin_token="participant-or-admin-token",
    )
    await deps.require_admin(
        request=_request(),
        x_admin_token="strict-admin-token",
    )
    actor = await deps.require_simulator_actor(
        request=_request(),
        db=MagicMock(),
        x_admin_token="simulator-admin-token",
        x_simulator_owner=None,
        token=None,
    )

    assert actor.is_admin is True
    assert calls == [
        "participant-or-admin-token",
        "strict-admin-token",
        "simulator-admin-token",
    ]
