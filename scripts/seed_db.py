"""Seed a GEOv0 database with demo data - by running a community's recipe through the domain services.

The one source is the recipe (`scripts/seed_recipe.py`, programme 017 `T1711`): participants, trust lines,
payments, clearing and freezes are PERFORMED through `ParticipantService` / `TrustLineService` /
`PaymentService` / `ClearingService` and the admin freeze handler, so every debt is the journalled effect of an
operation and the seed ends by reconciling what it produced.

The two direct-insert sources - `--source fixtures` (admin fixture datasets: equivalents, participants, trust
lines, debts as one `SEED` operation, transactions and audit rows written as rows) and `--source seeds` (the legacy
`seeds/*.json`) - were deleted by programme 030 S2 (`F-030-3`, owner decisions В1 and В4 of 2026-10-05): demo data
goes through the real API with the same checks, and the system is not adapted to demo data. A database seeded by
them is reseeded, not converted. `admin-fixtures/` stays the Admin UI's mock-mode data; only its import into a
database is gone.
"""

import argparse
import asyncio
import os
import sys

# Добавляем корень проекта в путь поиска
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed GEOv0 DB with demo data by running a community's recipe")
    parser.add_argument(
        "--source",
        choices=["recipe"],
        default="recipe",
        help=(
            "Seed source: recipe (run a community's recipe through the domain services) - the only one; "
            "the direct-insert sources were removed by programme 030 S2"
        ),
    )
    parser.add_argument(
        "--community",
        # The two `-v2` pack ids have no recipe; they stay offered so the recipe refuses them by name and lists the
        # communities that can be seeded (`scripts/seed_recipe.py::seed_community`).
        choices=[
            "greenfield-village-100",
            "riverside-town-50",
            "greenfield-village-100-v2",
            "riverside-town-50-v2",
        ],
        default=None,
        help="The community whose description and recipe under seeds/communities/ are executed; required.",
    )
    args = parser.parse_args()

    # THE RECIPE PATH DOES NOT INSERT ROWS. It performs the community's operations through
    # ParticipantService / TrustLineService / PaymentService / ClearingService and the admin
    # freeze handler, takes the reconciliation baseline on empty debts before the first
    # payment, and then RECONCILES what it produced. See `scripts/seed_recipe.py`.
    if not args.community:
        print(
            "--source recipe needs --community; a recipe belongs to one community.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    from scripts.seed_recipe import SeedRefusal, seed_community
    from app.db.session import AsyncSessionLocal

    try:
        report = await seed_community(AsyncSessionLocal, community_id=args.community)
    except SeedRefusal as refusal:
        print(f"Seeding refused: {refusal}", file=sys.stderr)
        raise SystemExit(1) from refusal
    print(f"Seeding completed successfully (source=recipe). {report.summary()}")
    for name, check in sorted(report.acceptance.items()):
        print(f"  acceptance {name}: PASSED - {check['detail']}")
    print(f"  ref -> PID table: {report.key_table_path}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
