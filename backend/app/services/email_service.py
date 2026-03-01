import smtplib
import ssl
import logging
import httpx
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)


def _effective_sender() -> str:

    sender = settings.SMTP_USER.strip()
    from_override = settings.EMAIL_FROM.strip()
    if from_override and from_override != sender:
        # Log a warning — do not use mismatched address
        logger.debug(
            "EMAIL_FROM (%s) differs from SMTP_USER (%s). "
            "Using SMTP_USER as sender to avoid Gmail rejection.",
            from_override, sender,
        )
    return sender


# ── Core sender (sync, thread-safe) ──────────────────────────────────────────

def send_email_sync(to: str, subject: str, html_body: str) -> bool:

    provider = settings.EMAIL_PROVIDER.strip().lower()

    # ── Log provider: always active, never tries network ─────────────────
    if provider == "log" or not settings.SMTP_USER.strip():
        logger.info(
            "[EMAIL LOG] To: %s | Subject: %s\n%s",
            to, subject, html_body[:400],
        )
        return True
    if provider == "sendgrid":
        api_key = settings.SENDGRID_API_KEY.strip()
        sender = (settings.SENDGRID_FROM_EMAIL or settings.EMAIL_FROM or settings.SMTP_USER).strip()
        if not api_key or not sender:
            logger.error("SendGrid misconfigured: SENDGRID_API_KEY or sender missing")
            return False
        try:
            payload = {
                "personalizations": [{"to": [{"email": to}], "subject": subject}],
                "from": {"email": sender},
                "content": [{"type": "text/html", "value": html_body}],
            }
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code in (200, 202):
                logger.info("SendGrid email sent -> %s | %s", to, subject)
                return True
            logger.error("SendGrid send failed status=%s body=%s", resp.status_code, resp.text[:500])
            return False
        except Exception as e:
            logger.error("SendGrid error sending to %s: %s", to, e)
            return False

    # SMTP provider ─────────────────────────────────────────────────────
    sender = _effective_sender()

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender          # MUST match SMTP_USER for Gmail
        msg["To"]      = to
        msg.attach(MIMEText(html_body, "html"))

        context = ssl.create_default_context()

        # Mirror test_mail.py exactly: plain SMTP → ehlo → starttls → ehlo → login → send
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        try:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(sender, [to], msg.as_string())
            logger.info("✅ Email sent → %s | %s", to, subject)
            return True
        finally:
            try:
                server.quit()
            except Exception:
                pass  # already disconnected is fine

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "❌ SMTP auth failed for '%s'. "
            "Ensure SMTP_PASSWORD is a Gmail App Password (16 chars, no spaces). "
            "Generate one at: Google Account → Security → App passwords",
            settings.SMTP_USER,
        )
        return False

    except smtplib.SMTPSenderRefused as e:
        logger.error(
            "❌ SMTP sender refused: %s. "
            "Sender must match the authenticated Gmail account (%s).",
            e, settings.SMTP_USER,
        )
        return False

    except (smtplib.SMTPConnectError, ConnectionRefusedError, OSError) as e:
        logger.error(
            "❌ SMTP connection to %s:%s failed: %s",
            settings.SMTP_HOST, settings.SMTP_PORT, e,
        )
        return False

    except smtplib.SMTPException as e:
        logger.error("❌ SMTP error sending to %s: %s", to, e)
        return False

    except Exception as e:
        logger.error("❌ Unexpected email error to %s: %s", to, e)
        return False


# ── Async wrapper (safe to await from async endpoints) ───────────────────────

async def send_email(to: str, subject: str, html_body: str) -> bool:
    """Thin async wrapper — delegates synchronously (safe; no asyncio.run)."""
    return send_email_sync(to, subject, html_body)


# ── Email templates ───────────────────────────────────────────────────────────

def send_interview_invite_sync(
    candidate_email: str,
    candidate_name: str,
    interview_title: str,
    scheduled_at: str,
    interview_link: str,
    temp_password: str = None,
) -> bool:
    subject = f"Interview Invitation: {interview_title}"
    login_section = ""
    if temp_password:
        login_section = f"""
            <div style="background:#FEF3C7;border:1px solid #F59E0B;border-radius:8px;padding:16px;margin:16px 0;">
                <p style="margin:0 0 8px;font-weight:700;color:#92400E;">🔑 Your Login Credentials</p>
                <p style="margin:4px 0;color:#78350F;">Email: <strong>{candidate_email}</strong></p>
                <p style="margin:4px 0;color:#78350F;">Password: <strong>{temp_password}</strong></p>
                <p style="margin:8px 0 0;font-size:12px;color:#92400E;">
                    Log in at <a href="{interview_link.split('/interview/')[0]}">{interview_link.split('/interview/')[0]}</a> to see your interview details.
                </p>
            </div>"""
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#f9fafb;padding:32px;border-radius:12px;">
        <div style="text-align:center;margin-bottom:24px;">
            <span style="font-size:40px">🤖</span>
            <h1 style="color:#4F46E5;margin:8px 0 0">HirE.AI</h1>
        </div>
        <div style="background:white;border-radius:10px;padding:24px;
                    border:1px solid #e5e7eb;">
            <h2 style="color:#111827;margin-top:0">Interview Invitation</h2>
            <p>Hello <strong>{candidate_name}</strong>,</p>
            <p>You have been invited to an AI-powered technical interview for
               <strong>{interview_title}</strong>.</p>
            <table style="width:100%;border-collapse:collapse;margin:20px 0;
                          background:#f3f4f6;border-radius:8px;overflow:hidden;">
                <tr>
                    <td style="padding:12px 16px;font-weight:600;
                               color:#374151;width:40%">Interview</td>
                    <td style="padding:12px 16px;color:#111827">{interview_title}</td>
                </tr>
                <tr style="background:#e9eaf0;">
                    <td style="padding:12px 16px;font-weight:600;color:#374151">Scheduled</td>
                    <td style="padding:12px 16px;color:#111827">{scheduled_at}</td>
                </tr>
            </table>
            <p style="color:#6b7280;font-size:14px;">
                The interview uses AI voice interaction, live coding, and webcam analysis.
                Please ensure your camera and microphone are working before joining.
            </p>
            <div style="text-align:center;margin:28px 0;">
                <a href="{interview_link}"
                   style="background:#4F46E5;color:white;padding:14px 36px;
                          text-decoration:none;border-radius:8px;font-size:16px;
                          font-weight:600;display:inline-block;">
                     Join Interview
                </a>
            </div>
{login_section}
            <p style="color:#9ca3af;font-size:12px;word-break:break-all;">
                Direct link:
                <a href="{interview_link}" style="color:#6366f1">{interview_link}</a>
            </p>
        </div>
        <p style="text-align:center;color:#d1d5db;font-size:12px;margin-top:16px">
            Sent by HirE.AI · AI-Powered Interviews
        </p>
    </div>
    """
    return send_email_sync(candidate_email, subject, html)


def send_interviewer_notification_sync(interviewer_email: str,interviewer_name: str,interview_title: str,scheduled_at: str,dashboard_link: str) -> bool:
    subject = f"Interview Assignment: {interview_title}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;
                background:#f9fafb;padding:32px;border-radius:12px;">
        <div style="text-align:center;margin-bottom:24px;">
            <span style="font-size:40px">🤖</span>
            <h1 style="color:#4F46E5;margin:8px 0 0">HirE.AI</h1>
        </div>
        <div style="background:white;border-radius:10px;padding:24px;
                    border:1px solid #e5e7eb;">
            <h2 style="color:#111827;margin-top:0">Interview Assignment</h2>
            <p>Hello <strong>{interviewer_name}</strong>,</p>
            <p>You have been assigned as an observer for
               <strong>{interview_title}</strong>.</p>
            <p style="background:#f3f4f6;padding:12px 16px;border-radius:8px;">
                <strong>Scheduled:</strong> {scheduled_at}
            </p>
            <div style="text-align:center;margin:28px 0;">
                <a href="{dashboard_link}"
                   style="background:#4F46E5;color:white;padding:14px 36px;
                          text-decoration:none;border-radius:8px;font-size:16px;
                          font-weight:600;display:inline-block;">
                    View Dashboard
                </a>
            </div>
        </div>
    </div>
    """
    return send_email_sync(interviewer_email, subject, html)


# ── Async wrappers (kept for backward compatibility) ─────────────────────────

async def send_interview_invite(
    candidate_email: str, candidate_name: str,
    interview_title: str, scheduled_at: str, interview_link: str,
) -> bool:
    return send_interview_invite_sync(
        candidate_email, candidate_name, interview_title, scheduled_at, interview_link
    )


async def send_interviewer_notification(
    interviewer_email: str, interviewer_name: str,
    interview_title: str, scheduled_at: str, dashboard_link: str,
) -> bool:
    return send_interviewer_notification_sync(
        interviewer_email, interviewer_name, interview_title, scheduled_at, dashboard_link
    )

