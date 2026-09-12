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