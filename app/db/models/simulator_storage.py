from __future__ import annotations

from sqlalchemy import CheckConstraint, DateTime, Float, Index, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SimulatorRun(Base):
    __tablename__ = "simulator_runs"

    # NOTE: stored as TEXT for cross-db compatibility (SQLite in tests).
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    scenario_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sim_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tick_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    intensity_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ops_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)

    errors_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_event_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    current_phase: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("mode IN ('fixtures','real')", name="chk_simulator_runs_mode"),
        CheckConstraint("state IN ('idle','running','paused','stopping','stopped','error')", name="chk_simulator_runs_state"),
        CheckConstraint("sim_time_ms IS NULL OR sim_time_ms >= 0", name="chk_simulator_runs_sim_time_ms"),
        CheckConstraint(
            "intensity_percent IS NULL OR (intensity_percent >= 0 AND intensity_percent <= 100)",
            name="chk_simulator_runs_intensity",
        ),
        CheckConstraint("queue_depth IS NULL OR queue_depth >= 0", name="chk_simulator_runs_queue_depth"),
        CheckConstraint("errors_total IS NULL OR errors_total >= 0", name="chk_simulator_runs_errors_total"),
        Index("ix_simulator_runs_scenario_created_at", "scenario_id", "created_at"),
    )


class SimulatorRunMetric(Base):
    __tablename__ = "simulator_run_metrics"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    equivalent_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    t_ms: Mapped[int] = mapped_column(Integer, primary_key=True)

    value: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        CheckConstraint("t_ms >= 0", name="chk_simulator_run_metrics_t_ms"),
        CheckConstraint(
            "key IN ('success_rate','avg_route_length','total_debt','clearing_volume','bottlenecks_score')",
            name="chk_simulator_run_metrics_key",
        ),
        Index("ix_simulator_run_metrics_run_key", "run_id", "key"),
    )


class SimulatorRunBottleneck(Base):
    __tablename__ = "simulator_run_bottlenecks"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    equivalent_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    computed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), primary_key=True, server_default=func.now())

    target_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(200), primary_key=True)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="chk_simulator_run_bottlenecks_score"),
        Index(
            "ix_simulator_run_bottlenecks_run_equiv_time",
            "run_id",
            "equivalent_code",
            "computed_at",
        ),
    )


class SimulatorRunArtifact(Base):
    __tablename__ = "simulator_run_artifacts"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), primary_key=True)

    content_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_url: Mapped[str] = mapped_column(String(500), nullable=False)

    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="chk_simulator_run_artifacts_size"),
        Index("ix_simulator_run_artifacts_created_at", "created_at"),
    )
