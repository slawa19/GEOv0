"""024 `T2414.1`: the simulator knobs moved from raw `os.getenv` reads into `Settings` keep their parsing.

The raw reads fell back to the default on an empty or unparseable value instead of refusing to start; the
Interact-mode flag was true only for "1", "true", "TRUE" and "yes"; the amount cap was optional, positive and
quantized down to 0.01; the app version preferred GEO_APP_VERSION to APP_VERSION. Each case below is one of
those rules, read through a fresh `Settings()` from the process environment.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("SIMULATOR_TICK_MS_BASE", "250", 250),
        ("SIMULATOR_TICK_MS_BASE", " 250 ", 250),
        ("SIMULATOR_TICK_MS_BASE", "", 1000),
        ("SIMULATOR_TICK_MS_BASE", "fast", 1000),
        ("SIMULATOR_TICK_MS_BASE", "2.5", 1000),
        ("SIMULATOR_REAL_MAX_ERRORS_TOTAL", "", None),
        ("SIMULATOR_REAL_MAX_ERRORS_TOTAL", "7", 7),
        ("SIMULATOR_REAL_ENABLE_INJECT", "2", 2),
        ("SIMULATOR_REAL_ENABLE_INJECT", "true", 0),
        ("SIMULATOR_ACTIONS_ENABLE", "1", True),
        ("SIMULATOR_ACTIONS_ENABLE", "yes", True),
        ("SIMULATOR_ACTIONS_ENABLE", "TRUE", True),
        ("SIMULATOR_ACTIONS_ENABLE", "True", False),
        ("SIMULATOR_ACTIONS_ENABLE", "on", False),
        ("SIMULATOR_ACTIONS_ENABLE", "", False),
        ("SIMULATOR_REAL_AMOUNT_CAP", "500", Decimal("500.00")),
        ("SIMULATOR_REAL_AMOUNT_CAP", "12.349", Decimal("12.349")),  # INTENTIONAL, 028 F-028-32: applied in each step
        ("SIMULATOR_REAL_AMOUNT_CAP", "0", None),
        ("SIMULATOR_REAL_AMOUNT_CAP", "NaN", None),
        ("SIMULATOR_REAL_AMOUNT_CAP", "cap", None),
        # Values the quantization refuses (024 `T2414` §15 fix-delta, P2): the raw read caught those too.
        ("SIMULATOR_REAL_AMOUNT_CAP", "Infinity", None),
        ("SIMULATOR_REAL_AMOUNT_CAP", "-Infinity", None),
        ("SIMULATOR_REAL_AMOUNT_CAP", "1e100", Decimal("1e100")),  # INTENTIONAL, 028 F-028-32: no 0.01 quantize to refuse it
        ("SIMULATOR_REAL_AMOUNT_CAP", "sNaN", None),
        ("SIMULATOR_SCENARIO_ALLOWLIST", "all", "all"),
    ],
)
def test_a_moved_simulator_knob_parses_as_its_raw_read_did(monkeypatch, name, raw, expected) -> None:
    monkeypatch.setenv(name, raw)
    assert getattr(Settings(), name) == expected


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, ""), ({"APP_VERSION": "1.2"}, "1.2"), ({"GEO_APP_VERSION": "2.0", "APP_VERSION": "1.2"}, "2.0")],
)
def test_the_app_version_prefers_geo_app_version(monkeypatch, env, expected) -> None:
    for name in ("GEO_APP_VERSION", "APP_VERSION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert Settings().GEO_APP_VERSION == expected
