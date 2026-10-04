"""Persistent records and a small PostgreSQL-backed job queue."""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .execution import ExecutionArtifact, ExperimentSpec


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://costguard:costguard@localhost:5432/costguard",
    )


engine = create_engine(database_url(), pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    suite_id: Mapped[str] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(200))
    digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SuiteRow(Base):
    __tablename__ = "suites"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class BaselineRow(Base):
    __tablename__ = "baselines"
    suite_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(36), ForeignKey("artifacts.id"))


class ComparisonRow(Base):
    __tablename__ = "comparisons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    baseline_id: Mapped[str] = mapped_column(String(36), ForeignKey("artifacts.id"))
    candidate_id: Mapped[str] = mapped_column(String(36), ForeignKey("artifacts.id"))
    request_payload: Mapped[dict] = mapped_column(JSONB)
    report: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(24), index=True)
    spec: Mapped[dict] = mapped_column(JSONB)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("artifacts.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


@contextmanager
def session_scope():
    with Session(engine) as session:
        with session.begin():
            yield session


def ready() -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def _json(model) -> dict:
    return json.loads(model.model_dump_json())


def import_artifact(artifact: ExecutionArtifact) -> tuple[str, bool]:
    payload = _json(artifact)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    artifact_id = str(artifact.artifact_id)
    with session_scope() as session:
        existing = session.get(ArtifactRow, artifact_id)
        if existing:
            if existing.digest != digest:
                raise ValueError("artifact_id already exists with different content")
            return artifact_id, False
        session.add(ArtifactRow(
            id=artifact_id,
            suite_id=artifact.suite_id,
            name=artifact.name,
            digest=digest,
            payload=payload,
        ))
    return artifact_id, True


def get_artifact(artifact_id: str) -> ExecutionArtifact | None:
    with session_scope() as session:
        row = session.get(ArtifactRow, artifact_id)
        return ExecutionArtifact.model_validate(row.payload) if row else None


def list_artifacts() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(ArtifactRow).order_by(ArtifactRow.created_at.desc())).all()
        return [{"id": r.id, "suite_id": r.suite_id, "name": r.name, "created_at": r.created_at.isoformat()} for r in rows]


def save_suite(spec: ExperimentSpec) -> str:
    with session_scope() as session:
        row = session.get(SuiteRow, spec.suite_id)
        if row is None:
            session.add(SuiteRow(id=spec.suite_id, payload=_json(spec)))
        else:
            row.payload = _json(spec)
    return spec.suite_id


def get_suite(suite_id: str) -> ExperimentSpec | None:
    with session_scope() as session:
        row = session.get(SuiteRow, suite_id)
        return ExperimentSpec.model_validate(row.payload) if row else None


def list_suites() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(SuiteRow).order_by(SuiteRow.id)).all()
        return [{"suite_id": r.id, "name": r.payload.get("name", r.id)} for r in rows]


def set_baseline(suite_id: str, artifact_id: str) -> None:
    artifact = get_artifact(artifact_id)
    if artifact is None or artifact.suite_id != suite_id:
        raise ValueError("baseline artifact must exist in the selected suite")
    with session_scope() as session:
        row = session.get(BaselineRow, suite_id)
        if row is None:
            session.add(BaselineRow(suite_id=suite_id, artifact_id=artifact_id))
        else:
            row.artifact_id = artifact_id


def get_baseline(suite_id: str) -> str | None:
    with session_scope() as session:
        row = session.get(BaselineRow, suite_id)
        return row.artifact_id if row else None


def save_comparison(baseline_id: str, candidate_id: str, request: dict, report: dict) -> str:
    report_id = str(uuid4())
    with session_scope() as session:
        session.add(ComparisonRow(
            id=report_id,
            baseline_id=baseline_id,
            candidate_id=candidate_id,
            request_payload=request,
            report=report,
        ))
    return report_id


def get_comparison(report_id: str) -> dict | None:
    with session_scope() as session:
        row = session.get(ComparisonRow, report_id)
        return {"id": row.id, "request": row.request_payload, "report": row.report} if row else None


def list_comparisons() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(ComparisonRow).order_by(ComparisonRow.created_at.desc())).all()
        return [{"id": r.id, "baseline_id": r.baseline_id, "candidate_id": r.candidate_id,
                 "status": r.report.get("status"), "created_at": r.created_at.isoformat()} for r in rows]


def create_job(spec: ExperimentSpec) -> str:
    job_id = str(uuid4())
    with session_scope() as session:
        session.add(JobRow(id=job_id, state="queued", spec=_json(spec), total=len(spec.cases)))
    return job_id


def get_job(job_id: str) -> dict | None:
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        return _job_view(row) if row else None


def _job_view(row: JobRow) -> dict:
    return {"id": row.id, "state": row.state, "progress": row.progress, "total": row.total,
            "artifact_id": row.artifact_id, "error": row.error}


def list_jobs() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(JobRow).order_by(JobRow.created_at.desc())).all()
        return [_job_view(row) for row in rows]


def claim_job() -> tuple[str, ExperimentSpec] | None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        expired = session.scalars(select(JobRow).where(JobRow.state == "running", JobRow.lease_until < now)).all()
        for row in expired:
            row.state = "interrupted"
            row.error = "Runner lease expired; paid calls are not replayed automatically"
        row = session.scalars(
            select(JobRow).where(JobRow.state == "queued")
            .order_by(JobRow.created_at).with_for_update(skip_locked=True).limit(1)
        ).first()
        if row is None:
            return None
        row.state = "running"
        row.lease_until = now + timedelta(seconds=60)
        return row.id, ExperimentSpec.model_validate(row.spec)


def update_job(job_id: str, *, progress: int | None = None, state: str | None = None,
               artifact_id: str | None = None, error: str | None = None) -> None:
    with session_scope() as session:
        row = session.get(JobRow, job_id, with_for_update=True)
        if row is None:
            raise ValueError("job not found")
        if progress is not None:
            row.progress = progress
        if state is not None:
            row.state = state
        if artifact_id is not None:
            row.artifact_id = artifact_id
        if error is not None:
            row.error = error
        if row.state == "running":
            row.lease_until = datetime.now(timezone.utc) + timedelta(seconds=60)


def cancel_job(job_id: str) -> bool:
    with session_scope() as session:
        row = session.get(JobRow, job_id, with_for_update=True)
        if row is None or row.state not in {"queued", "running"}:
            return False
        row.state = "cancelled"
        return True
