from app.db.models.simulator_storage import SimulatorRun


def test_simulator_run_owner_column_matches_postgres_migration_contract() -> None:
    owner_id = SimulatorRun.__table__.c.owner_id
    owner_kind = SimulatorRun.__table__.c.owner_kind

    assert owner_id.nullable is False
    assert owner_kind.nullable is True


def test_simulator_run_has_owner_state_created_index() -> None:
    index = next(
        (
            item
            for item in SimulatorRun.__table__.indexes
            if item.name == "ix_simulator_runs_owner_state_created"
        ),
        None,
    )

    assert index is not None
    assert [column.name for column in index.columns] == [
        "owner_id",
        "state",
        "created_at",
    ]
