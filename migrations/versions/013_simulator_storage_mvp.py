"""Simulator storage (MVP)

Revision ID: 013_simulator_storage_mvp
Revises: 012_participant_type_business
Create Date: 2026-01-28

Adds DB-first tables for simulator runs/metrics/bottlenecks/artifacts.
Designed to work on SQLite (tests) and Postgres.
"""

from alembic import op
import sqlalchemy as sa


revision = "013_simulator_storage_mvp"
down_revision = "012_participant_type_business"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulator_runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("scenario_id", sa.String(length=200), nullable=False, index=True),
        sa.Column("mode", sa.String(length=20), nullable=False, index=True),
        sa.Column("state", sa.String(length=20), nullable=False, index=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sim_time_ms", sa.Integer(), nullable=True),
        sa.Column("tick_index", sa.Integer(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("intensity_percent", sa.Integer(), nullable=True),
        sa.Column("ops_sec", sa.Float(), nullable=True),
        sa.Column("queue_depth", sa.Integer(), nullable=True),
        sa.Column("errors_total", sa.Integer(), nullable=True),
        sa.Column("last_event_type", sa.String(length=100), nullable=True),
        sa.Column("current_phase", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("mode IN ('fixtures','real')", name="chk_simulator_runs_mode"),
        sa.CheckConstraint(
            "state IN ('idle','running','paused','stopping','stopped','error')",
            name="chk_simulator_runs_state",
        ),
        sa.CheckConstraint("sim_time_ms IS NULL OR sim_time_ms >= 0", name="chk_simulator_runs_sim_time_ms"),
        sa.CheckConstraint(
            "intensity_percent IS NULL OR (intensity_percent >= 0 AND intensity_percent <= 100)",
            name="chk_simulator_runs_intensity",
        ),
        sa.CheckConstraint("queue_depth IS NULL OR queue_depth >= 0", name="chk_simulator_runs_queue_depth"),
        sa.CheckConstraint("errors_total IS NULL OR errors_total >= 0", name="chk_simulator_runs_errors_total"),
    )

    op.create_index("ix_simulator_runs_created_at", "simulator_runs", ["created_at"])
    op.create_index(
        "ix_simulator_runs_scenario_created_at",
        "simulator_runs",
        ["scenario_id", "created_at"],
    )

    op.create_table(
        "simulator_run_metrics",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("equivalent_code", sa.String(length=50), primary_key=True),
        sa.Column("key", sa.String(length=50), primary_key=True),
        sa.Column("t_ms", sa.Integer(), primary_key=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.CheckConstraint("t_ms >= 0", name="chk_simulator_run_metrics_t_ms"),
        sa.CheckConstraint(
            "key IN ('success_rate','avg_route_length','total_debt','clearing_volume','bottlenecks_score','active_participants','active_trustlines')",
            name="chk_simulator_run_metrics_key",
        ),
    )
    op.create_index(
        "ix_simulator_run_metrics_run_key",
        "simulator_run_metrics",
        ["run_id", "key"],
    )

    op.create_table(
        "simulator_run_bottlenecks",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("equivalent_code", sa.String(length=50), primary_key=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            primary_key=True,
            server_default=sa.func.now(),
        ),
        sa.Column("target_type", sa.String(length=50), primary_key=True),
        sa.Column("target_id", sa.String(length=200), primary_key=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason_code", sa.String(length=50), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="chk_simulator_run_bottlenecks_score"),
    )
    op.create_index(
        "ix_simulator_run_bottlenecks_run_equiv_time",
        "simulator_run_bottlenecks",
        ["run_id", "equivalent_code", "computed_at"],
    )

    op.create_table(
        "simulator_run_artifacts",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=200), primary_key=True),
        sa.Column("content_type", sa.String(length=200), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=128), nullable=True),
        sa.Column("storage_url", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="chk_simulator_run_artifacts_size"),
    )
    op.create_index(
        "ix_simulator_run_artifacts_created_at",
        "simulator_run_artifacts",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_simulator_run_artifacts_created_at", table_name="simulator_run_artifacts")
    op.drop_table("simulator_run_artifacts")

    op.drop_index("ix_simulator_run_bottlenecks_run_equiv_time", table_name="simulator_run_bottlenecks")
    op.drop_table("simulator_run_bottlenecks")

    op.drop_index("ix_simulator_run_metrics_run_key", table_name="simulator_run_metrics")
    op.drop_table("simulator_run_metrics")

    op.drop_index("ix_simulator_runs_scenario_created_at", table_name="simulator_runs")
    op.drop_index("ix_simulator_runs_created_at", table_name="simulator_runs")
    op.drop_table("simulator_runs")
