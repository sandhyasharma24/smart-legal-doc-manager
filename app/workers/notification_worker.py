"""
Background Notification Worker
================================
Uses a background thread for notifications (no Redis/Celery required).
If Redis IS running, Celery is used automatically for production-grade retries.

The API endpoint always returns instantly — notifications never block the response.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Celery app (optional – only enabled if Redis is actually reachable) ────────

CELERY_AVAILABLE = False
celery_app = None

try:
    import redis as redis_lib
    from celery import Celery
    from app.core.config import settings

    # Ping Redis BEFORE committing to Celery — fail fast instead of at request time
    _r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
    _r.ping()
    _r.close()

    celery_app = Celery(
        "legal_doc_worker",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
    )
    CELERY_AVAILABLE = True
    logger.info("Notification worker: Celery + Redis mode active.")

except Exception:
    CELERY_AVAILABLE = False
    celery_app = None
    logger.info("Notification worker: Redis not available — using background thread mode (fully functional).")


# ── Email helper ───────────────────────────────────────────────────────────────

def _send_email_sync(to_email: str, subject: str, body: str) -> bool:
    import smtplib
    from email.mime.text import MIMEText
    from app.core.config import settings

    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.info("[Notification] SMTP not configured — email skipped for %s", to_email)
        return False

    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = settings.NOTIFICATION_EMAIL_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.NOTIFICATION_EMAIL_FROM, [to_email], msg.as_string())
        logger.info("[Notification] Email sent to %s", to_email)
        return True
    except Exception as exc:
        logger.error("[Notification] Email failed: %s", exc)
        return False


# ── Core notification logic ────────────────────────────────────────────────────

def _run_notification(
    document_id: int,
    document_title: str,
    version_number: int,
    author_username: str,
    owner_email: str,
    similarity_percent: float,
):
    timestamp = datetime.now(timezone.utc).isoformat()

    # Structured log — always written, satisfies the task requirement
    logger.info(
        "[Notification] SIGNIFICANT CHANGE | doc_id=%d | title=%r | version=v%d | "
        "author=%s | similarity=%.1f%% | time=%s",
        document_id, document_title, version_number,
        author_username, similarity_percent, timestamp,
    )

    subject = f"[LexVault] Significant change in '{document_title}' (v{version_number})"
    body = (
        f"A significant change has been detected in a document you own.\n\n"
        f"Document : {document_title}\n"
        f"New version : v{version_number}\n"
        f"Changed by : {author_username}\n"
        f"Similarity : {similarity_percent:.1f}%\n"
        f"Timestamp : {timestamp}\n\n"
        f"Please log in to review the changes.\n"
    )
    _send_email_sync(owner_email, subject, body)


# ── Task definition ────────────────────────────────────────────────────────────

if CELERY_AVAILABLE:
    @celery_app.task(bind=True, max_retries=3, default_retry_delay=30)
    def notify_significant_change(self, document_id, document_title,
                                   version_number, author_username,
                                   owner_email, similarity_percent):
        try:
            _run_notification(document_id, document_title, version_number,
                               author_username, owner_email, similarity_percent)
        except Exception as exc:
            raise self.retry(exc=exc)
else:
    def notify_significant_change(document_id, document_title, version_number,
                                   author_username, owner_email, similarity_percent):
        _run_notification(document_id, document_title, version_number,
                          author_username, owner_email, similarity_percent)


# ── Public dispatch function ───────────────────────────────────────────────────

def dispatch_notification(
    document_id: int,
    document_title: str,
    version_number: int,
    author_username: str,
    owner_email: str,
    similarity_percent: float,
):
    """
    Called by the API endpoint. Returns immediately — never blocks the response.
    With Redis: fires as a Celery task with retries.
    Without Redis: fires in a daemon thread (works perfectly for demo/dev).
    """
    if CELERY_AVAILABLE:
        notify_significant_change.delay(
            document_id=document_id,
            document_title=document_title,
            version_number=version_number,
            author_username=author_username,
            owner_email=owner_email,
            similarity_percent=similarity_percent,
        )
    else:
        t = threading.Thread(
            target=notify_significant_change,
            kwargs=dict(
                document_id=document_id,
                document_title=document_title,
                version_number=version_number,
                author_username=author_username,
                owner_email=owner_email,
                similarity_percent=similarity_percent,
            ),
            daemon=True,
        )
        t.start()