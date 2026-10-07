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
    checkpoint: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("artifacts.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SettingRow(Base):
    __tablename__ = "settings"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class InvestigationRow(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(36), ForeignKey("comparisons.id"))
    state: Mapped[str] = mapped_column(String(24), index=True)
    request: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    proposal_job_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("jobs.id"), nullable=True)
    proposal_report_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("comparisons.id"), nullable=True)
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
                # New optional schema fields must not make an unchanged historical
                # artifact conflict with itself when it is exported and re-imported.
                normalized = _json(ExecutionArtifact.model_validate(existing.payload))
                if normalized != payload:
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
    if (artifact.suite_cases is None or len(artifact.cases) != len(artifact.suite_cases)
            or any(case.status != "ok" or not case.calls for case in artifact.cases)
            or any(call.usage_source != "gateway" and call.usage_source != "provider"
                   for case in artifact.cases for call in case.calls)
            or any(call.status != "ok" for case in artifact.cases for call in case.calls)):
        raise ValueError("baseline requires a complete case manifest and observed successful execution")
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


def _job_view(row: JobRow, include_checkpoint=True) -> dict:
    result = {"id": row.id, "state": row.state, "progress": row.progress, "total": row.total,
              "artifact_id": row.artifact_id, "error": row.error}
    if include_checkpoint:
        result["checkpoint"] = row.checkpoint or {}
    return result


def list_jobs() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(JobRow).order_by(JobRow.created_at.desc())).all()
        return [_job_view(row, include_checkpoint=False) for row in rows]


def claim_job() -> tuple[str, ExperimentSpec] | None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        expired = session.scalars(select(JobRow).where(JobRow.state == "running", JobRow.lease_until < now)
                                  .with_for_update(skip_locked=True)).all()
        for row in expired:
            row.state = "interrupted"
            row.error = "Runner lease expired; paid calls are not replayed automatically"
            _persist_checkpoint(session, row)
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


def checkpoint_job(job_id: str, artifact: ExecutionArtifact, progress: int) -> bool:
    """Persist before and after each network call; never replay an in-flight receipt."""
    with session_scope() as session:
        row = session.get(JobRow, job_id, with_for_update=True)
        if row is None or row.state not in {"running", "cancelled"}:
            return False
        row.checkpoint = _json(artifact)
        row.progress = progress
        return True


def _persist_checkpoint(session, row):
    if not row.checkpoint or row.artifact_id:
        return
    artifact = ExecutionArtifact.model_validate(row.checkpoint)
    payload = _json(artifact)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    session.add(ArtifactRow(id=str(artifact.artifact_id), suite_id=artifact.suite_id, name=artifact.name,
                           digest=hashlib.sha256(raw.encode()).hexdigest(), payload=payload))
    row.artifact_id = str(artifact.artifact_id)


def finalize_job(job_id: str, state: str, error: str | None = None) -> None:
    with session_scope() as session:
        row = session.get(JobRow, job_id, with_for_update=True)
        if row is None:
            return
        _persist_checkpoint(session, row)
        if row.state == "running":
            row.state, row.error = state, error


def get_job_spec(job_id: str) -> ExperimentSpec | None:
    with session_scope() as session:
        row = session.get(JobRow, job_id)
        return ExperimentSpec.model_validate(row.spec) if row else None


def get_analyst_settings() -> dict | None:
    with session_scope() as session:
        row = session.get(SettingRow, "analyst")
        return row.payload if row else None


def save_analyst_settings(settings) -> None:
    with session_scope() as session:
        row = session.get(SettingRow, "analyst")
        if row is None:
            session.add(SettingRow(id="analyst", payload=_json(settings)))
        else:
            row.payload = _json(settings)


def source_experiment(artifact_id: str) -> ExperimentSpec | None:
    with session_scope() as session:
        row = session.scalars(select(JobRow).where(JobRow.artifact_id == artifact_id)
                              .order_by(JobRow.created_at.desc()).limit(1)).first()
        return ExperimentSpec.model_validate(row.spec) if row else None


def create_investigation(report_id: str, question: str, analyst, source=None) -> str:
    investigation_id = str(uuid4())
    with session_scope() as session:
        session.add(InvestigationRow(
            id=investigation_id, report_id=report_id, state="queued", result={},
            request={"question": question, "analyst": _json(analyst),
                     "source_spec": _json(source) if source else None},
        ))
    return investigation_id


def _investigation_view(row: InvestigationRow) -> dict:
    return {"id": row.id, "report_id": row.report_id, "state": row.state,
            "request": row.request, "result": row.result, "error": row.error,
            "proposal_job_id": row.proposal_job_id, "proposal_report_id": row.proposal_report_id,
            "created_at": row.created_at.isoformat()}


def get_investigation(investigation_id: str) -> dict | None:
    with session_scope() as session:
        row = session.get(InvestigationRow, investigation_id)
        return _investigation_view(row) if row else None


def list_investigations() -> list[dict]:
    with session_scope() as session:
        rows = session.scalars(select(InvestigationRow).order_by(InvestigationRow.created_at.desc()).limit(100)).all()
        return [{"id": row.id, "report_id": row.report_id, "state": row.state,
                 "question": row.request["question"], "created_at": row.created_at.isoformat()}
                for row in rows]


def claim_investigation() -> str | None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        expired = session.scalars(select(InvestigationRow).where(
            InvestigationRow.state == "running", InvestigationRow.lease_until < now)).all()
        for row in expired:
            row.state = "interrupted"
            row.error = "Runner lease expired; analyst calls are not replayed automatically"
        row = session.scalars(select(InvestigationRow).where(InvestigationRow.state == "queued")
            .order_by(InvestigationRow.created_at).with_for_update(skip_locked=True).limit(1)).first()
        if row is None:
            return None
        row.state = "running"
        row.lease_until = now + timedelta(seconds=180)
        return row.id


def update_investigation(investigation_id: str, *, result=None, state=None, error=None) -> None:
    with session_scope() as session:
        row = session.get(InvestigationRow, investigation_id, with_for_update=True)
        if row is None:
            return
        if result is not None:
            row.result = json.loads(json.dumps(result, default=str))
        if row.state == "running":
            if state is not None:
                row.state = state
            row.error = error
            row.lease_until = datetime.now(timezone.utc) + timedelta(seconds=180)


def cancel_investigation(investigation_id: str) -> bool:
    with session_scope() as session:
        row = session.get(InvestigationRow, investigation_id, with_for_update=True)
        if row is None or row.state not in {"queued", "running"}:
            return False
        row.state = "cancelled"
        return True


def run_investigation_proposal(investigation_id: str, system_prompt: str | None = None) -> str:
    """The explicit execution action is idempotent to avoid duplicate paid jobs."""
    with session_scope() as session:
        row = session.get(InvestigationRow, investigation_id, with_for_update=True)
        if row is None:
            raise ValueError("investigation not found")
        if row.proposal_job_id:
            if system_prompt is not None and session.get(JobRow, row.proposal_job_id).spec["system_prompt"] != system_prompt:
                raise ValueError("Proposal was already approved with a different prompt")
            return row.proposal_job_id
        if row.state != "completed" or not row.result.get("proposal"):
            raise ValueError("completed investigation has no executable proposal")
        spec = ExperimentSpec.model_validate(row.result["proposal"]["spec"])
        if system_prompt is not None:
            spec = ExperimentSpec.model_validate({**spec.model_dump(mode="json"), "system_prompt": system_prompt})
        row.result = {**row.result, "approval": {"system_prompt": spec.system_prompt,
            "edited": spec.system_prompt != row.result["proposal"]["spec"]["system_prompt"]}}
        job_id = str(uuid4())
        session.add(JobRow(id=job_id, state="queued", spec=_json(spec), total=len(spec.cases)))
        session.flush()
        row.proposal_job_id = job_id
        return job_id


def compare_investigation_proposal(investigation_id: str) -> str:
    """Compare the proposed run against the original baseline and original policy."""
    from .economics import compare_artifacts
    from .models import ComparisonPolicy, PricingCatalog
    with session_scope() as session:
        row = session.get(InvestigationRow, investigation_id, with_for_update=True)
        if row is None:
            raise ValueError("investigation not found")
        if row.proposal_report_id:
            return row.proposal_report_id
        job = session.get(JobRow, row.proposal_job_id) if row.proposal_job_id else None
        if job is None or not job.artifact_id or job.state != "completed":
            raise ValueError("proposed experiment has not completed")
        original = session.get(ComparisonRow, row.report_id)
        request = dict(original.request_payload)
        request["candidate_id"] = job.artifact_id
        left = ExecutionArtifact.model_validate(session.get(ArtifactRow, original.baseline_id).payload)
        right = ExecutionArtifact.model_validate(session.get(ArtifactRow, job.artifact_id).payload)
        report = compare_artifacts(left, right, PricingCatalog.model_validate(request["pricing"]),
            ComparisonPolicy.model_validate(request["policy"]) if request.get("policy") else None,
            request.get("monthly_requests"))
        report_id = str(uuid4())
        session.add(ComparisonRow(id=report_id, baseline_id=original.baseline_id,
            candidate_id=job.artifact_id, request_payload=request, report=_json(report)))
        session.flush()
        row.proposal_report_id = report_id
        previous = ExecutionArtifact.model_validate(session.get(ArtifactRow, original.candidate_id).payload)
        second = compare_artifacts(previous, right, PricingCatalog.model_validate(request["pricing"]),
            ComparisonPolicy.model_validate(request["policy"]) if request.get("policy") else None,
            request.get("monthly_requests"))
        candidate_report_id = str(uuid4())
        session.add(ComparisonRow(id=candidate_report_id, baseline_id=original.candidate_id,
            candidate_id=job.artifact_id, request_payload={**request, "baseline_id": original.candidate_id},
            report=_json(second)))
        row.result = {**row.result, "candidate_comparison_id": candidate_report_id}
        return report_id
