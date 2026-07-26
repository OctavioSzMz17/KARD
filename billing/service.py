"""
Billing business logic — Stripe integration.

NOT IMPLEMENTED — skeleton only.

When implementing:
  1. pip install stripe
  2. Add STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET to .env
  3. Implement create_checkout_session() using stripe.checkout.Session.create()
  4. Implement handle_webhook() with stripe.Webhook.construct_event()
     to verify signature and update tenant.subscription_status + tenant.plan
  5. Add DB migration 003 for the billing columns on tenants:
       stripe_customer_id, stripe_subscription_id,
       subscription_status, plan, trial_ends_at
"""

from sqlalchemy.orm import Session


class BillingService:
    def __init__(self, db: Session):
        self.db = db

    def create_checkout_session(self, tenant_id: str, plan: str) -> str:
        """
        Create a Stripe Checkout Session and return the URL.

        NOT IMPLEMENTED.
        """
        raise NotImplementedError("Stripe integration not yet implemented")

    def handle_webhook(self, payload: bytes, stripe_signature: str) -> None:
        """
        Verify Stripe webhook signature and update tenant subscription state.

        Events to handle:
          - checkout.session.completed  → set subscription_status=active, plan=X
          - customer.subscription.deleted → set subscription_status=canceled
          - invoice.payment_failed       → set subscription_status=past_due

        NOT IMPLEMENTED.
        """
        raise NotImplementedError("Stripe integration not yet implemented")

    def get_plan_info(self, tenant_id: str) -> dict:
        """
        Return current plan and subscription status for a tenant.

        NOT IMPLEMENTED.
        """
        raise NotImplementedError("Stripe integration not yet implemented")
