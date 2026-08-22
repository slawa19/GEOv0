"""RT-009-5, the counter-check: none of the eight sites may read back after the commit.

Program 009, `T905` / `F-009-6`.

`tests/integration/test_p1_commit_then_refresh_postgres.py` proves the contract on the
trustline-create path with a real connection loss.  It cannot prove it for the other seven
without seven more Postgres stands, and the acceptance criterion of this program is
explicit that reverting **any** of the eight must fail a test -- otherwise fixing one site
looks like closing the finding.

So this is the structural half of the same counter-check: for each site, the readback must
appear before the commit and never after it.  The rule the fix implements is one sentence --
materialise the whole success inside the uncommitted transaction, then commit, and perform
no mandatory database read afterwards -- and a rule that simple can be checked directly on
the source rather than restated eight times in prose.

Note on what is deliberately NOT asserted: the simulator routes do touch the database after
the commit, in the best-effort SSE and edge-patch block.  That is fine and must stay
allowed -- it is wrapped in `try/except` and cannot turn a durable mutation into a reported
failure.  The rule is about *mandatory* readback, which in this codebase is spelled
`refresh(`.
"""

from __future__ import annotations

import inspect
import re

import pytest

import app.api.v1.simulator as simulator_module
from app.core.auth.service import AuthService
from app.core.participants.service import ParticipantService
from app.core.trustlines.service import TrustLineService


# (label, callable) for each of the eight pairs named in the spec.
_SITES = [
    ("simulator.action_trustline_create", simulator_module.action_trustline_create),
    ("simulator.action_trustline_update", simulator_module.action_trustline_update),
    ("simulator.action_trustline_close", simulator_module.action_trustline_close),
    ("TrustLineService.create", TrustLineService.create),
    ("TrustLineService.update", TrustLineService.update),
    ("ParticipantService.create_participant", ParticipantService.create_participant),
    ("ParticipantService.update_participant", ParticipantService.update_participant),
    ("AuthService.create_challenge", AuthService.create_challenge),
]

_COMMIT = re.compile(r"\.commit\(\)")
_REFRESH = re.compile(r"\.refresh\(")


@pytest.mark.parametrize(("label", "func"), _SITES, ids=[s[0] for s in _SITES])
def test_no_refresh_after_commit(label: str, func) -> None:
    source = inspect.getsource(func)

    commits = [m.start() for m in _COMMIT.finditer(source)]
    assert commits, f"{label}: no commit found -- this list is out of date with the code"

    refreshes = [m.start() for m in _REFRESH.finditer(source)]
    assert refreshes, (
        f"{label}: no refresh found. If the readback was removed rather than moved that is "
        "fine, but update this test so it keeps meaning something"
    )

    last_commit = max(commits)
    after = [pos for pos in refreshes if pos > last_commit]
    assert not after, (
        f"{label}: a refresh() follows the commit. The commit is durable at that point, so "
        "a failure in that read reports a mutation that already happened as failed, and "
        "the retry it invites is not idempotent (F-009-6). Move the readback before the "
        "commit."
    )


def test_the_site_list_matches_the_spec() -> None:
    """Eight, not three. The old count was inherited from registry 008 without a recount."""

    assert len(_SITES) == 8
