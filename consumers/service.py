"""
Business logic for the consumers module (global B2C identities).

A consumer account is platform-wide: one registration gives access to
every public storefront, with behavioral history kept in consumer_events
across all of them.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from consumers.models import ConsumerLogin, ConsumerRegister, ConsumerResponse, ConsumerTokenResponse
from consumers.repository import ConsumerRepository
from core.exceptions import ConsumerAlreadyExistsError, ConsumerNotFoundError, InvalidCredentialsError
from core.security import create_token, hash_password, verify_password
from db.models import Consumer
from notifications.service import NotificationService
from utils.helpers import generate_id

logger = logging.getLogger("kard.consumers")


class ConsumerService:
    def __init__(self, db: Session):
        self.db = db
        self.consumers = ConsumerRepository(db)

    def register(self, payload: ConsumerRegister) -> ConsumerTokenResponse:
        """Create a global consumer account and return a JWT immediately."""
        email = payload.email.lower()
        if self.consumers.get_by_email(email) is not None:
            raise ConsumerAlreadyExistsError(email)

        consumer = Consumer(
            id=generate_id("con", 20),
            email=email,
            hashed_password=hash_password(payload.password),
            name=payload.name,
            is_active=True,
        )

        try:
            self.consumers.create(consumer)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.warning("Consumer registration race for email=%s", email)
            raise ConsumerAlreadyExistsError(email)

        logger.info("New consumer registered: consumer_id=%s", consumer.id)

        NotificationService.consumer_welcome(email=consumer.email, name=consumer.name)

        return ConsumerTokenResponse(
            access_token=self._issue_token(consumer.id),
            consumer_id=consumer.id,
        )

    def login(self, payload: ConsumerLogin) -> ConsumerTokenResponse:
        """Authenticate a consumer by email/password."""
        email = payload.email.lower()
        consumer = self.consumers.get_by_email(email)

        if consumer is None or not consumer.is_active or not verify_password(payload.password, consumer.hashed_password):
            raise InvalidCredentialsError(email)

        return ConsumerTokenResponse(
            access_token=self._issue_token(consumer.id),
            consumer_id=consumer.id,
        )

    def get_me(self, consumer_id: str) -> ConsumerResponse:
        """Return the authenticated consumer's public profile."""
        consumer = self.consumers.get_by_id(consumer_id)
        if consumer is None or not consumer.is_active:
            raise ConsumerNotFoundError(consumer_id)
        return ConsumerResponse(
            consumer_id=consumer.id,
            email=consumer.email,
            name=consumer.name,
            is_active=consumer.is_active,
            created_at=consumer.created_at,
        )

    @staticmethod
    def _issue_token(consumer_id: str) -> str:
        """
        Consumer JWT carries { consumer_id, type: "consumer" }.

        The `type` claim is what distinguishes it from a B2B token that
        carries { user_id, tenant_id, business_type, role }.
        """
        return create_token({"consumer_id": consumer_id, "type": "consumer"})
