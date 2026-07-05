"""
Notification service — orchestrates email delivery for domain events.

Each method is a fire-and-forget call: it builds the template, calls
the email client, and returns immediately. Email failures never propagate
to callers (the client swallows all exceptions internally).

Usage — always call AFTER the DB commit so a failed email cannot
roll back a successful transaction:

    self.db.commit()
    NotificationService.tenant_welcome(email=tenant.email, ...)
"""

import logging

from notifications.email_client import send_email
from notifications import templates

logger = logging.getLogger("kard.notifications")


class NotificationService:

    @staticmethod
    def tenant_welcome(*, email: str, business_name: str, slug: str) -> None:
        """Send welcome email to a newly registered business_admin."""
        tpl = templates.tenant_welcome(business_name=business_name, slug=slug)
        send_email(to=email, subject=tpl["subject"], html=tpl["html"])

    @staticmethod
    def consumer_welcome(*, email: str, name: str | None) -> None:
        """Send welcome email to a newly registered consumer."""
        tpl = templates.consumer_welcome(name=name)
        send_email(to=email, subject=tpl["subject"], html=tpl["html"])

    @staticmethod
    def low_stock_alert(*, email: str, product_name: str, current_stock: int, business_name: str) -> None:
        """Notify a tenant's admin that a product is running low on stock."""
        tpl = templates.low_stock_alert(
            product_name=product_name,
            current_stock=current_stock,
            business_name=business_name,
        )
        send_email(to=email, subject=tpl["subject"], html=tpl["html"])

    @staticmethod
    def payment_confirmed(*, email: str, business_name: str, plan: str) -> None:
        """Notify tenant that a Stripe payment succeeded and their plan is active."""
        tpl = templates.payment_confirmed(business_name=business_name, plan=plan)
        send_email(to=email, subject=tpl["subject"], html=tpl["html"])

    @staticmethod
    def payment_failed(*, email: str, business_name: str, plan: str) -> None:
        """Notify tenant that a Stripe payment failed and they need to update billing."""
        tpl = templates.payment_failed(business_name=business_name, plan=plan)
        send_email(to=email, subject=tpl["subject"], html=tpl["html"])
