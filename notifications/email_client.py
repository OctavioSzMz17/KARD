"""
Thin wrapper around the Resend API.

Email sending is best-effort: any failure (missing key, network error,
Resend outage) is logged as a warning and the caller continues normally.
Email never blocks the main request path.

Configuration (all from .env via core/config.py):
  RESEND_API_KEY  — required for sending; leave empty to disable
  EMAIL_FROM      — sender address (e.g. noreply@yourdomain.com)
  EMAIL_FROM_NAME — display name shown in the inbox (default: BRAND_NAME)
"""

import logging

from core.config import settings

logger = logging.getLogger("kard.notifications")


def send_email(*, to: str, subject: str, html: str) -> bool:
    """
    Send a transactional email via Resend.

    Returns True on success, False if email is disabled or delivery failed.
    Never raises — callers must not catch exceptions from here.
    """
    if not settings.RESEND_API_KEY:
        logger.debug("Email sending disabled (RESEND_API_KEY not set); skipping '%s' to %s", subject, to)
        return False

    if not settings.EMAIL_FROM:
        logger.warning("EMAIL_FROM not configured; cannot send email to %s", to)
        return False

    try:
        import resend  # imported lazily so missing package never crashes the app at startup

        resend.api_key = settings.RESEND_API_KEY

        sender = (
            f"{settings.email_sender_name} <{settings.EMAIL_FROM}>"
            if settings.email_sender_name
            else settings.EMAIL_FROM
        )

        resend.Emails.send({
            "from": sender,
            "to": [to],
            "subject": subject,
            "html": html,
        })

        logger.info("Email sent: subject='%s' to=%s", subject, to)
        return True

    except ImportError:
        logger.warning("resend package not installed; run: pip install resend")
        return False
    except Exception:
        logger.warning("Failed to send email to %s (subject: %s)", to, subject, exc_info=True)
        return False
