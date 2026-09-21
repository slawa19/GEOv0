"""T1523 cell 8: the payer process. Not a test module - it is run as a separate OS process.

Two modes, one code path:

* `--mode die-after-commit` wraps `PaymentEngine.commit` so that it awaits the REAL commit and then
  calls `os._exit(17)` before control returns to `PaymentService._create_payment_impl` - the point
  at which the payment is durable and the caller has not been answered. The wrapper lives HERE, in
  the child, and not in application code: the brief's stop condition 2 forbids a production hook
  for this, and none is needed, because a subprocess may patch whatever it likes inside itself.
* `--mode answer` runs the same request unpatched. This is the process that comes after the
  restart and must be handed the stored result.

Everything the process prints on stdout is a single tagged line, so the parent can tell a commit
that happened from a response that was produced:

    COMMITTED-THEN-EXIT:<tx_id>   the real commit returned; the process is about to die
    RESULT:<json>                 `create_payment` returned, i.e. a response exists

The database URL arrives as an argument and is placed in the environment BEFORE any application
module is imported, because `app.config.settings` reads it at import time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

EXIT_AFTER_COMMIT = 17

# Run as a script, Python puts THIS directory on the path, not the repository root: `app` would be
# unimportable. The parent passes the root as cwd; make it importable here so the child does not
# depend on the caller's PYTHONPATH.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("die-after-commit", "answer"), required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--body", required=True, help="path to the signed request body, JSON")
    parser.add_argument("--sender-id", required=True)
    return parser.parse_args()


async def _pay(mode: str, sender_id: uuid.UUID, body: dict) -> int:
    from app.core.payments.engine import PaymentEngine
    from app.core.payments.service import PaymentService
    from app.db.session import AsyncSessionLocal
    from app.schemas.payment import PaymentCreateRequest

    if mode == "die-after-commit":
        original_commit = PaymentEngine.commit

        async def _commit_then_exit(self, tx_id, *, commit=True):
            result = await original_commit(self, tx_id, commit=commit)
            print("COMMITTED-THEN-EXIT:" + str(tx_id), flush=True)
            sys.stdout.flush()
            sys.stderr.flush()
            # No unwinding, no session close, no response: the process is gone here.
            os._exit(EXIT_AFTER_COMMIT)
            return result  # pragma: no cover - unreachable by construction

        PaymentEngine.commit = _commit_then_exit  # type: ignore[method-assign]

    request = PaymentCreateRequest.model_validate(body)
    async with AsyncSessionLocal() as session:
        try:
            result = await PaymentService(session).create_payment(sender_id, request)
        except Exception as exc:  # reported to the parent, which decides whether it matters
            print(
                "ERROR:"
                + json.dumps(
                    {
                        "type": type(exc).__name__,
                        "message": str(getattr(exc, "message", exc)),
                        "code": str(getattr(exc, "code", "")),
                        "status_code": getattr(exc, "status_code", None),
                        "details": getattr(exc, "details", None),
                    },
                    default=str,
                ),
                flush=True,
            )
            return 3
    print("RESULT:" + result.model_dump_json(), flush=True)
    return 0


def main() -> int:
    args = _parse_args()
    os.environ["DATABASE_URL"] = args.database_url
    body = json.loads(Path(args.body).read_text(encoding="utf-8"))
    return asyncio.run(_pay(args.mode, uuid.UUID(args.sender_id), body))


if __name__ == "__main__":
    sys.exit(main())
