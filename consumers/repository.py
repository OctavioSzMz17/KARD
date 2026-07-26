"""
Data-access layer for the consumers module.

Consumer emails are globally unique across the platform — lookups are
intentionally not tenant-scoped (consumers have no tenant).
"""

from typing import Optional

from sqlalchemy.orm import Session

from db.models import Consumer


class ConsumerRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> Optional[Consumer]:
        return self.db.query(Consumer).filter(Consumer.email == email).first()

    def get_by_id(self, consumer_id: str) -> Optional[Consumer]:
        return self.db.query(Consumer).filter(Consumer.id == consumer_id).first()

    def create(self, consumer: Consumer) -> Consumer:
        self.db.add(consumer)
        self.db.flush()
        return consumer
