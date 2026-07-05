"""Data-access layer for the orders module."""

from typing import Optional
from sqlalchemy.orm import Session, joinedload

from db.models import Order


class OrderRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, order_id: str, consumer_id: str) -> Optional[Order]:
        """
        Fetch a single order. consumer_id guard ensures a consumer can
        never see another consumer's order — returns None (→ 404) instead
        of 403 to avoid leaking existence.
        """
        return (
            self.db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.tenant))
            .filter(Order.id == order_id, Order.consumer_id == consumer_id)
            .first()
        )

    def list_by_consumer(
        self, consumer_id: str, page: int, page_size: int
    ) -> tuple[list[Order], int]:
        q = (
            self.db.query(Order)
            .options(joinedload(Order.items), joinedload(Order.tenant))
            .filter(Order.consumer_id == consumer_id)
        )
        total = q.count()
        items = (
            q.order_by(Order.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total
