"""Database configuration and session management."""

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

# Resolve app.db relative to the project root (meet-hub/app.db)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "app.db"
DEFAULT_DB_URL = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB_URL)

# For SQLite, we need check_same_thread=False for async usage
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Get database session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
