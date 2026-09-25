from app.db.base import Base

# The debt journal's three tables are unmapped Core `Table`s (programme 015, step 4). They are
# imported HERE, with the mapped models, because "the models" is what every schema builder in
# this repository means by `Base.metadata` - `create_all` and Alembic's autogenerate
# comparison. A table that is not
# imported by the time metadata is read simply does not exist for any of them. It is deliberately
# NOT re-exported below: nothing may treat it as a model, because being unreachable from the ORM
# is the journal's enforcement (app/db/journal_tables.py).
from app.db import journal_tables as _journal_tables  # noqa: F401
# The reconciliation baseline and result (programme 015, step 5a), unmapped for the same reason.
from app.db import reconciliation_tables as _reconciliation_tables  # noqa: F401
from .equivalent import Equivalent
from .participant import Participant
from .trustline import TrustLine
from .debt import Debt
from .transaction import Transaction
from .auth_challenge import AuthChallenge
from .audit_log import AuditLog
from .integrity_checkpoint import IntegrityCheckpoint
from .config import Config
from .simulator_storage import SimulatorRun, SimulatorRunMetric, SimulatorRunBottleneck, SimulatorRunArtifact

# THE DEBT JOURNAL IS WRITTEN BY THE DATABASE (programme 018 stage B, migration 029). This import
# attaches the journal's sequence, functions and triggers to the tables' `after_create`, so that a
# schema built with `Base.metadata.create_all` (mode A of the test fixtures) carries the same writer
# and guards as a migrated one; `tests/integration/test_p018_b_schema_parity_postgres.py` compares
# the two. It replaced the import that armed the listener journal (`app/core/ledger/journal.py`,
# deleted by the same stage): nothing is installed on any engine or session any more.
from app.db import journal_triggers as _journal_triggers  # noqa: E402,F401

__all__ = [
    "Base",
    "Equivalent",
    "Participant",
    "TrustLine",
    "Debt",
    "Transaction",
    "AuthChallenge",
    "AuditLog",
    "IntegrityCheckpoint",
    "Config",
    "SimulatorRun",
    "SimulatorRunMetric",
    "SimulatorRunBottleneck",
    "SimulatorRunArtifact",
]