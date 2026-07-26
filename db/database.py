"""
SQLAlchemy engine and session management.

Reads the connection string from `settings.DATABASE_URL` (Rules.md
section 13 — never hardcode credentials). Other modules should depend
on `get_db` to obtain a request-scoped session.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """
    Yield a database session for the duration of a request and ensure
    it is always closed afterwards, even if an exception is raised.

    Usage: `db: Session = Depends(get_db)`
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
