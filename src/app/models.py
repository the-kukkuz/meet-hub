"""SQLAlchemy models."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""

    pass



class Meeting(Base):
    """Meeting model to store processed results."""

    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    speakers: Mapped[str] = mapped_column(Text)  # JSON string of speakers
    transcript: Mapped[str] = mapped_column(Text)
    processed_data: Mapped[str] = mapped_column(Text)  # JSON string of all extracted results
    
    def __repr__(self) -> str:
        return f"<Meeting(id={self.id}, title={self.title})>"


class CalendarEvent(Base):
    """CalendarEvent model to store tasks and deadlines."""

    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date: Mapped[str] = mapped_column(String(100), nullable=False) # Store as string for flexibility (e.g. "2026-04-10" or "Friday")
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[str] = mapped_column(String(50), default="MEDIUM")
    source_meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE") # ACTIVE, RESOLVED, CANCELLED
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<CalendarEvent(id={self.id}, title={self.title}, date={self.date})>"


class KnowledgeEntity(Base):
    """KnowledgeEntity model representing a node (Person, Decision, Task, Blocker, Topic)."""

    __tablename__ = "knowledge_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # PERSON, DECISION, TASK, BLOCKER, TOPIC
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(500), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string for extra attributes (priority, confidence, etc.)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<KnowledgeEntity(id={self.id}, type={self.entity_type}, name={self.name})>"


class KnowledgeRelationship(Base):
    """KnowledgeRelationship model representing a directed edge with provenance and evidence."""

    __tablename__ = "knowledge_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    relation_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # OWNS, DECIDED_IN, RAISED_IN, BLOCKS, RESOLVES, PARTICIPATED_IN
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # Verbatim quote anchoring this relationship
    source_meeting_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<KnowledgeRelationship(id={self.id}, {self.source_id}-[{self.relation_type}]->{self.target_id})>"
