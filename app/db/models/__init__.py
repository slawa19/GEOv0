from app.db.base import Base

# The debt journal's three tables are unmapped Core `Table`s (programme 015, step 4). They are
# imported HERE, with the mapped models, because "the models" is what every schema builder in
# this repository means by `Base.metadata` - `create_all` on both test tiers,
# `scripts/init_sqlite_db.py`, and Alembic's autogenerate comparison. A table that is not
# imported by the time metadata is read simply does not exist for any of them. It is deliberately
# NOT re-exported below: nothing may treat it as a model, because being unreachable from the ORM
# is the journal's enforcement (app/db/journal_tables.py).
from app.db import journal_tables as _journal_tables  # noqa: F401
from .equivalent import Equivalent
from .participant import Participant
from .trustline import TrustLine
from .debt import Debt
from .transaction import Transaction
from .prepare_lock import PrepareLock
from .auth_challenge import AuthChallenge
from .audit_log import AuditLog
from .integrity_checkpoint import IntegrityCheckpoint
from .config import Config
from .simulator_storage import SimulatorRun, SimulatorRunMetric, SimulatorRunBottleneck, SimulatorRunArtifact

# ARMING THE DEBT JOURNAL (programme 015, phase B step 4 slice C). Importing this module installs
# the journal's listeners on the `Engine` and `Session` classes, so that from here on a row in
# `debts` may only change inside a declared operation. It is imported HERE, with the tables it
# protects, because that is the only place that covers a process which imported the models and
# nothing else - a maintenance script, a REPL, `scripts/seed_db.py` run by hand. See the activation
# note at the bottom of `app/core/ledger/journal.py`; `tests/unit/test_p015_b4_entries_and_money.py`
# (`C15`) is the counterexample that tells this placement apart from an application entry point.
import app.core.ledger.journal  # noqa: E402,F401  (imported for its arming side effect)

__all__ = [
    "Base",
    "Equivalent",
    "Participant",
    "TrustLine",
    "Debt",
    "Transaction",
    "PrepareLock",
    "AuthChallenge",
    "AuditLog",
    "IntegrityCheckpoint",
    "Config",
    "SimulatorRun",
    "SimulatorRunMetric",
    "SimulatorRunBottleneck",
    "SimulatorRunArtifact",
]