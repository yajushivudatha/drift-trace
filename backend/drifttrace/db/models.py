"""Postgres schema (spec section 9), plus the few tables the pipeline needs to run."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from drifttrace.config import get_settings


def uid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    doi: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    openalex_id: Mapped[str | None] = mapped_column(String, index=True)
    s2_id: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSONB, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    work_type: Mapped[str | None] = mapped_column(String)
    language: Mapped[str | None] = mapped_column(String)
    is_oa: Mapped[bool] = mapped_column(Boolean, default=False)
    oa_url: Mapped[str | None] = mapped_column(Text)
    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False)
    retraction_note: Mapped[str | None] = mapped_column(Text)
    related_versions: Mapped[list] = mapped_column(JSONB, default=list)
    # None means full-text acquisition has not been attempted yet.
    fulltext_status: Mapped[str | None] = mapped_column(String)
    fulltext_source: Mapped[str | None] = mapped_column(String)
    fulltext_attempt: Mapped[str | None] = mapped_column(String)
    fulltext_note: Mapped[str | None] = mapped_column(Text)
    tei_xml: Mapped[str | None] = mapped_column(Text)
    fulltext: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    parse_quality: Mapped[str | None] = mapped_column(String)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String, index=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="paper", cascade="all, delete")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str | None] = mapped_column(Text)
    section_kind: Mapped[str | None] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    # Citation markers inside this chunk: [{"marker": "[3]", "ref_keys": ["b2"], "start": 10}]
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    embedding = mapped_column(Vector(get_settings().embedding_dim), nullable=True)

    paper: Mapped[Paper] = relationship(back_populates="chunks")


class Reference(Base):
    __tablename__ = "references"
    __table_args__ = (Index("ix_references_citing_key", "citing_paper_id", "ref_key", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    citing_paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"))
    ref_index: Mapped[int] = mapped_column(Integer)
    ref_key: Mapped[str] = mapped_column(String)
    raw_text: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[list] = mapped_column(JSONB, default=list)
    resolved_doi: Mapped[str | None] = mapped_column(String)
    resolved_paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    resolution_tried: Mapped[bool] = mapped_column(Boolean, default=False)


class Trace(Base):
    __tablename__ = "traces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    input_type: Mapped[str] = mapped_column(String)
    input_claim: Mapped[str] = mapped_column(Text)
    start_paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    fidelity_score: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    share_token: Mapped[str | None] = mapped_column(String, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    nodes: Mapped[list["TraceNode"]] = relationship(back_populates="trace", cascade="all, delete", passive_deletes=True)
    hops: Mapped[list["Hop"]] = relationship(back_populates="trace", cascade="all, delete", passive_deletes=True)


class TraceNode(Base):
    __tablename__ = "trace_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    trace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"), index=True)
    # Null for a free-text claim and for a citation that could not be resolved to a paper.
    paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id", ondelete="SET NULL"))
    label: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    is_origin: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_reason: Mapped[str | None] = mapped_column(String)
    claim_frame: Mapped[dict | None] = mapped_column(JSONB)
    claim_text: Mapped[str | None] = mapped_column(Text)

    trace: Mapped[Trace] = relationship(back_populates="nodes")
    paper: Mapped[Paper | None] = relationship()


class Hop(Base):
    __tablename__ = "hops"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    trace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"), index=True)
    from_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_nodes.id", ondelete="CASCADE"))
    to_node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_nodes.id", ondelete="CASCADE"))
    marker: Mapped[str | None] = mapped_column(String)
    claimed_quote: Mapped[str | None] = mapped_column(Text)
    claimed_location: Mapped[dict | None] = mapped_column(JSONB)
    source_quotes: Mapped[list] = mapped_column(JSONB, default=list)
    primary_label: Mapped[str] = mapped_column(String, index=True)
    secondary_labels: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[str] = mapped_column(String, index=True)
    confidence_reason: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    field_comparison: Mapped[dict] = mapped_column(JSONB, default=dict)
    highlights: Mapped[dict] = mapped_column(JSONB, default=dict)
    quotes_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_status: Mapped[str | None] = mapped_column(String)
    is_self_citation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_cycle: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieval_score: Mapped[float | None] = mapped_column(Float)
    prompt_version: Mapped[str | None] = mapped_column(String)
    gold_label: Mapped[str | None] = mapped_column(String)
    gold_note: Mapped[str | None] = mapped_column(Text)

    trace: Mapped[Trace] = relationship(back_populates="hops")
    feedback: Mapped[list["Feedback"]] = relationship(cascade="all, delete", passive_deletes=True)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    hop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hops.id", ondelete="CASCADE"), index=True)
    agrees: Mapped[bool] = mapped_column(Boolean, default=True)
    user_label: Mapped[str] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uid)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("traces.id", ondelete="SET NULL"), index=True)
    purpose: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCache(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
