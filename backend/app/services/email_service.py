import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.core.config import settings

logger = logging.getLogger(__name__)


def _sender_email() -> str:
    sender = (settings.SENDGRID_FROM_EMAIL or settings.EMAIL_FROM).strip()
    return sender


def _format_utc(dt: datetime) -> str:
    dt_utc = _as_utc(dt)
    return dt_utc.strftime("%Y-%m-%d %H:%M UTC")


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_scheduled_at(scheduled_at: datetime | str) -> datetime:
    if isinstance(scheduled_at, datetime):
        return _as_utc(scheduled_at)

    raw = str(scheduled_at).strip()
    # Backward compatibility with old formatted values
    if raw.endswith(" UTC"):
        raw = raw[:-4]
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    return _as_utc(parsed)


def send_email_sync(to: str, subject: str, html_body: str) -> bool:
    """
    Send email through official SendGrid SDK.
    In local/dev, EMAIL_PROVIDER=log can be used to avoid external delivery.
    """
    provider = settings.EMAIL_PROVIDER.strip().lower()
    if provider == "log":
        logger.info("[EMAIL LOG] to=%s subject=%s body=%s", to, subject, html_body[:500])
        return True

    api_key = settings.SENDGRID_API_KEY.strip()
    sender = _sender_email()
    if not api_key or not sender:
        logger.error("SendGrid misconfigured: SENDGRID_API_KEY and SENDGRID_FROM_EMAIL/EMAIL_FROM are required")
        return False

    try:
        message = Mail(
            from_email=sender,
            to_emails=to,
            subject=subject,
            html_content=html_body,
        )
        resp = SendGridAPIClient(api_key).send(message)
        if resp.status_code in (200, 202):
            logger.info("SendGrid email sent to=%s subject=%s", to, subject)
            return True
        logger.error("SendGrid send failed to=%s status=%s body=%s", to, resp.status_code, (resp.body or b"")[:500])
        return False
    except Exception as exc:
        logger.exception("SendGrid send error to=%s: %s", to, exc)
        return False


async def send_email(to: str, subject: str, html_body: str) -> bool:
    return send_email_sync(to, subject, html_body)


def _candidate_schedule_email_html(
    candidate_name: str,
    candidate_email: str,
    interview_title: str,
    scheduled_label: str,
    temp_password: Optional[str] = None,
) -> str:
    login_section = ""
    if temp_password:
        login_section = f"""
        <div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:8px;padding:16px;margin:16px 0;">
            <p style="margin:0 0 8px;font-weight:700;color:#92400E;">Your Login Credentials</p>
            <p style="margin:4px 0;color:#78350F;">Email: <strong>{candidate_email}</strong></p>
            <p style="margin:4px 0;color:#78350F;">Password: <strong>{temp_password}</strong></p>
            <p style="margin:8px 0 0;font-size:12px;color:#92400E;">
                Log in to your dashboard before the interview start time.
            </p>
        </div>
        """

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;background:#f8fafc;padding:24px;border-radius:12px;">
        <h2 style="margin:0 0 12px;color:#1f2937;">Interview Scheduled</h2>
        <p>Hello <strong>{candidate_name}</strong>,</p>
        <p>Your interview is scheduled. Details are below:</p>
        <table style="width:100%;border-collapse:collapse;margin:14px 0;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
            <tr>
                <td style="padding:10px 12px;font-weight:600;color:#374151;width:36%;">Interview</td>
                <td style="padding:10px 12px;color:#111827;">{interview_title}</td>
            </tr>
            <tr style="background:#f3f4f6;">
                <td style="padding:10px 12px;font-weight:600;color:#374151;">Scheduled Time</td>
                <td style="padding:10px 12px;color:#111827;">{scheduled_label}</td>
            </tr>
        </table>
        <p style="color:#4b5563;font-size:14px;margin-top:12px;">
            The interview access link will be shared 5 minutes before the interview start time.
        </p>
        {login_section}
    </div>
    """


def _candidate_link_email_html(
    candidate_name: str,
    interview_title: str,
    scheduled_label: str,
    interview_link: str,
) -> str:
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;background:#f8fafc;padding:24px;border-radius:12px;">
        <h2 style="margin:0 0 12px;color:#1f2937;">Interview Link - Join Now</h2>
        <p>Hello <strong>{candidate_name}</strong>,</p>
        <p>Your interview is about to start.</p>
        <table style="width:100%;border-collapse:collapse;margin:14px 0;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
            <tr>
                <td style="padding:10px 12px;font-weight:600;color:#374151;width:36%;">Interview</td>
                <td style="padding:10px 12px;color:#111827;">{interview_title}</td>
            </tr>
            <tr style="background:#f3f4f6;">
                <td style="padding:10px 12px;font-weight:600;color:#374151;">Scheduled Time</td>
                <td style="padding:10px 12px;color:#111827;">{scheduled_label}</td>
            </tr>
        </table>
        <div style="text-align:center;margin:20px 0 6px;">
            <a href="{interview_link}" style="background:#4F46E5;color:#fff;padding:12px 28px;text-decoration:none;border-radius:8px;font-weight:700;display:inline-block;">
                Join Interview
            </a>
        </div>
        <p style="font-size:12px;color:#6b7280;word-break:break-all;">If button does not work: {interview_link}</p>
    </div>
    """


def _dispatch_link_email_after_delay(
    *,
    delay_seconds: float,
    candidate_email: str,
    candidate_name: str,
    interview_title: str,
    scheduled_label: str,
    interview_link: str,
) -> None:
    """
    In-process deferred dispatch.
    Celery/Redis-ready seam: replace this body with queue enqueue later.
    """
    if delay_seconds > 0:
        logger.info("Delaying candidate link email by %.1fs for %s", delay_seconds, candidate_email)
        time.sleep(delay_seconds)

    subject = f"Interview Link: {interview_title}"
    html = _candidate_link_email_html(
        candidate_name=candidate_name,
        interview_title=interview_title,
        scheduled_label=scheduled_label,
        interview_link=interview_link,
    )
    send_email_sync(candidate_email, subject, html)


def send_interview_invite_sync(
    candidate_email: str,
    candidate_name: str,
    interview_title: str,
    scheduled_at: datetime | str,
    interview_link: str,
    temp_password: Optional[str] = None,
) -> bool:
    """
    Candidate mail flow:
    1) Send schedule details immediately (without link)
    2) Send link at ETA = scheduled_at - 5 minutes
       - If ETA is in the past, send immediately.
    """
    schedule_dt_utc = _parse_scheduled_at(scheduled_at)
    schedule_label = _format_utc(schedule_dt_utc)

    details_subject = f"Interview Scheduled: {interview_title}"
    details_html = _candidate_schedule_email_html(
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        interview_title=interview_title,
        scheduled_label=schedule_label,
        temp_password=temp_password,
    )
    details_ok = send_email_sync(candidate_email, details_subject, details_html)

    link_eta = schedule_dt_utc - timedelta(minutes=5)
    now_utc = datetime.now(timezone.utc)
    delay_seconds = max(0.0, (link_eta - now_utc).total_seconds())

    # Use a daemon thread so API background task returns immediately.
    t = threading.Thread(
        target=_dispatch_link_email_after_delay,
        kwargs={
            "delay_seconds": delay_seconds,
            "candidate_email": candidate_email,
            "candidate_name": candidate_name,
            "interview_title": interview_title,
            "scheduled_label": schedule_label,
            "interview_link": interview_link,
        },
        daemon=True,
    )
    t.start()
    logger.info(
        "Scheduled candidate link email thread started candidate=%s eta=%s delay=%.1fs",
        candidate_email,
        link_eta.isoformat(),
        delay_seconds,
    )
    return details_ok


def send_interviewer_notification_sync(
    interviewer_email: str,
    interviewer_name: str,
    interview_title: str,
    scheduled_at: datetime | str,
    dashboard_link: str,
) -> bool:
    schedule_dt_utc = _parse_scheduled_at(scheduled_at)
    schedule_label = _format_utc(schedule_dt_utc)
    subject = f"Interview Assignment: {interview_title}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#f8fafc;padding:24px;border-radius:12px;">
        <h2 style="margin:0 0 12px;color:#1f2937;">Interview Assignment</h2>
        <p>Hello <strong>{interviewer_name}</strong>,</p>
        <p>You have been assigned to interview session: <strong>{interview_title}</strong>.</p>
        <p style="background:#f3f4f6;padding:10px 12px;border-radius:8px;">
            <strong>Scheduled:</strong> {schedule_label}
        </p>
        <div style="text-align:center;margin:18px 0 6px;">
            <a href="{dashboard_link}" style="background:#4F46E5;color:#fff;padding:12px 28px;text-decoration:none;border-radius:8px;font-weight:700;display:inline-block;">
                Open Dashboard
            </a>
        </div>
    </div>
    """
    return send_email_sync(interviewer_email, subject, html)


async def send_interview_invite(
    candidate_email: str,
    candidate_name: str,
    interview_title: str,
    scheduled_at: datetime | str,
    interview_link: str,
) -> bool:
    return send_interview_invite_sync(
        candidate_email=candidate_email,
        candidate_name=candidate_name,
        interview_title=interview_title,
        scheduled_at=scheduled_at,
        interview_link=interview_link,
    )


async def send_interviewer_notification(
    interviewer_email: str,
    interviewer_name: str,
    interview_title: str,
    scheduled_at: datetime | str,
    dashboard_link: str,
) -> bool:
    return send_interviewer_notification_sync(
        interviewer_email=interviewer_email,
        interviewer_name=interviewer_name,
        interview_title=interview_title,
        scheduled_at=scheduled_at,
        dashboard_link=dashboard_link,
    )
