from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from market.db import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC).isoformat()


Json = JSON().with_variant(JSONB, "postgresql")


class Monitor(Base):
    __tablename__ = "monitor_queries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String, default=now)


class QueryVersion(Base):
    __tablename__ = "query_versions"
    __table_args__ = (UniqueConstraint("monitor_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    monitor_id: Mapped[str] = mapped_column(ForeignKey("monitor_queries.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Run(Base):
    __tablename__ = "collection_runs"
    __table_args__ = (UniqueConstraint("monitor_id", "logical_date", "kind", "mode"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    monitor_id: Mapped[str] = mapped_column(ForeignKey("monitor_queries.id"), index=True)
    query_version_id: Mapped[str] = mapped_column(ForeignKey("query_versions.id"))
    logical_date: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default="daily")
    mode: Mapped[str] = mapped_column(String, default="offline")
    state: Mapped[str] = mapped_column(String, default="queued", index=True)
    input: Mapped[dict] = mapped_column(Json)
    reserved_items: Mapped[int] = mapped_column(Integer)
    diagnostics: Mapped[dict] = mapped_column(Json, default=dict)
    raw_checkpoint: Mapped[list] = mapped_column(Json, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class SourceJob(Base):
    __tablename__ = "source_jobs"
    __table_args__ = (UniqueConstraint("source", "source_job_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    source: Mapped[str] = mapped_column(String, default="linkedin")
    source_job_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    company: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(String)
    posted_at: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    first_seen_at: Mapped[str] = mapped_column(String, index=True)
    last_seen_at: Mapped[str] = mapped_column(String)


class Snapshot(Base):
    __tablename__ = "job_snapshots"
    __table_args__ = (UniqueConstraint("job_id", "content_hash"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("source_jobs.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id"))
    content_hash: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(Json)
    observed_at: Mapped[str] = mapped_column(String)


class Observation(Base):
    __tablename__ = "job_query_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "job_id"),
        Index("ix_observation_cohort", "query_version_id", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id"))
    query_version_id: Mapped[str] = mapped_column(ForeignKey("query_versions.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("source_jobs.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("job_snapshots.id"))
    observed_at: Mapped[str] = mapped_column(String)


class Extraction(Base):
    __tablename__ = "extraction_results"
    __table_args__ = (UniqueConstraint("snapshot_id", "version"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("job_snapshots.id"), index=True)
    version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    result: Mapped[dict] = mapped_column(Json)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Quarantine(Base):
    __tablename__ = "quarantine"
    __table_args__ = (UniqueConstraint("run_id", "payload_hash"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id"))
    payload_hash: Mapped[str] = mapped_column(String)
    raw: Mapped[dict] = mapped_column(Json)
    reason: Mapped[str] = mapped_column(String)


class Metric(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("query_version_id", "period", "method"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    query_version_id: Mapped[str] = mapped_column(ForeignKey("query_versions.id"))
    period: Mapped[str] = mapped_column(String)
    method: Mapped[str] = mapped_column(String)
    result: Mapped[dict] = mapped_column(Json)
    computed_at: Mapped[str] = mapped_column(String, default=now)


class Budget(Base):
    __tablename__ = "collection_budgets"
    month: Mapped[str] = mapped_column(String, primary_key=True)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    llm_reserved: Mapped[int] = mapped_column(Integer, default=0)


class Skill(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    taxonomy_version: Mapped[str] = mapped_column(String, default="curated-v1")


class SkillAlias(Base):
    __tablename__ = "skill_aliases"
    alias: Mapped[str] = mapped_column(String, primary_key=True)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))


class SkillMention(Base):
    __tablename__ = "job_skill_mentions"
    __table_args__ = (UniqueConstraint("extraction_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    extraction_id: Mapped[str] = mapped_column(ForeignKey("extraction_results.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), index=True)
    evidence: Mapped[dict] = mapped_column(Json)


class Compensation(Base):
    __tablename__ = "compensation"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    extraction_id: Mapped[str] = mapped_column(ForeignKey("extraction_results.id"))
    disclosure: Mapped[dict] = mapped_column(Json)


class Insight(Base):
    __tablename__ = "insights"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    metric_id: Mapped[str] = mapped_column(ForeignKey("metric_snapshots.id"))
    content: Mapped[dict] = mapped_column(Json)
