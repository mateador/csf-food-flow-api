"""
Email sending, abstracted behind one function so swapping providers later
(or adding a second one) touches this file only. Falls back to printing
the email to the console if RESEND_API_KEY isn't set -- lets local dev work
without ever needing a real Resend account.
"""
import os

import structlog

logger = structlog.get_logger()


async def send_magic_link_email(to_email: str, magic_link_url: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("EMAIL_FROM", "no-reply@example.org")

    if not api_key:
        # Local dev fallback -- no Resend account needed to develop against
        # the auth flow. The link is right here in the terminal.
        logger.info("magic_link_email_console_fallback", to=to_email, url=magic_link_url)
        print(f"\n{'=' * 60}\nMAGIC LINK (console fallback, no RESEND_API_KEY set)\nTo: {to_email}\nLink: {magic_link_url}\n{'=' * 60}\n")
        return

    import resend

    resend.api_key = api_key
    resend.Emails.send(
        {
            "from": from_email,
            "to": to_email,
            "subject": "Your CSF Food Flow sign-in link",
            "html": (
                f'<p>Click below to sign in. This link expires in 15 minutes '
                f'and can only be used once.</p>'
                f'<p><a href="{magic_link_url}">Sign in to CSF Food Flow</a></p>'
            ),
        }
    )
    logger.info("magic_link_email_sent", to=to_email)
