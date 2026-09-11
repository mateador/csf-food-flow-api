"""
Email sending, abstracted behind one function so swapping providers later
(or adding a second one) touches this file only. Falls back to printing
the email to the console if RESEND_API_KEY isn't set -- lets local dev work
without ever needing a real Resend account.
"""
import os

import structlog

logger = structlog.get_logger()


async def send_login_code_email(to_email: str, code: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("EMAIL_FROM", "no-reply@example.org")

    if not api_key:
        # Local dev fallback -- no Resend account needed to develop against
        # the auth flow. The code is right here in the terminal.
        logger.info("login_code_email_console_fallback", to=to_email, code=code)
        print(f"\n{'=' * 60}\nLOGIN CODE (console fallback, no RESEND_API_KEY set)\nTo: {to_email}\nCode: {code}\n{'=' * 60}\n")
        return

    import resend

    resend.api_key = api_key
    try:
        resend.Emails.send(
            {
                "from": from_email,
                "to": to_email,
                "subject": "Your CSF Food Flow sign-in code",
                "html": (
                    f'<p>Your sign-in code is:</p>'
                    f'<p style="font-size: 32px; font-weight: bold; letter-spacing: 4px;">{code}</p>'
                    f'<p>This code expires in 10 minutes and can only be used once.</p>'
                ),
            }
        )
        logger.info("login_code_email_sent", to=to_email)
    except Exception:
        # The code was already created and stored before this function was
        # called -- it's valid whether or not the email actually arrives.
        # A Resend-side failure (bad key, outage, rate limit) should never
        # crash the request/verify endpoint, which promises to always
        # return the same generic response regardless of what happened
        # internally. Logged at error level so this is actually visible
        # and alertable in production, not silently swallowed.
        logger.error("login_code_email_send_failed", to=to_email, exc_info=True)