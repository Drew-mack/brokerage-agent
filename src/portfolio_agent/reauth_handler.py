"""AWS handlers for automated Schwab OAuth reminders and callback."""

import html
import logging
import time

from portfolio_agent.config import settings
from portfolio_agent.integrations.mailer.ses_sender import SESEmailSender
from portfolio_agent.integrations.schwab_auth import (
    build_authorization_url,
    exchange_code_for_tokens,
    save_initial_tokens,
)
from portfolio_agent.storage.reauth_storage import (
    consume_state,
    create_state,
    mark_reminder_sent,
    reminder_was_sent,
)

logger = logging.getLogger(__name__)
REFRESH_TOKEN_LIFETIME = 7 * 24 * 60 * 60
REMINDER_WINDOW = 24 * 60 * 60


def _html_page(title: str, message: str) -> str:
    return f"<!doctype html><html><head><title>{html.escape(title)}</title></head>" \
           f"<body><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>"


def _send_reauthorization_email(authorization_generation: int) -> None:
    """Send a fresh one-time authorization link for an expired callback."""

    state = create_state(authorization_generation)
    authorization_url = build_authorization_url(state=state)
    sender = SESEmailSender(settings.sender_email, settings.recipient_email, settings.aws_region)
    sender.send(
        subject="New Schwab authorization link",
        text=f"Your previous Schwab link expired. Reauthorize Schwab here: {authorization_url}",
        html=(
            "<p>Your previous Schwab authorization link expired.</p>"
            f'<p><a href="{html.escape(authorization_url)}">Reauthorize Schwab</a></p>'
            "<p>This link expires in 15 minutes.</p>"
        ),
    )
    mark_reminder_sent(authorization_generation)


def callback_handler(event, context):
    """Exchange a validated Schwab OAuth callback for new secret tokens."""

    params = event.get("queryStringParameters") or {}
    if params.get("error"):
        return {"statusCode": 400, "headers": {"Content-Type": "text/html"},
                "body": _html_page("Schwab authorization cancelled", "No tokens were changed.")}

    state = params.get("state")
    code = params.get("code")
    if not code:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "text/html"},
            "body": _html_page(
                "Schwab authorization was cancelled",
                "No tokens were changed. Request a new link and try again.",
            ),
        }

    if not consume_state(state):
        from portfolio_agent.integrations.schwab_auth import load_tokens

        tokens = load_tokens()
        created_at = (tokens or {}).get("refresh_token_created_at")
        authorization_generation = int(created_at or time.time())
        try:
            if not reminder_was_sent(authorization_generation):
                _send_reauthorization_email(authorization_generation)
                message = "That link expired. A fresh link was sent to your email."
            else:
                message = "That link expired. Check your email for the most recent fresh link."
        except Exception:
            logger.exception("Could not send replacement Schwab OAuth link")
            message = "That link expired. Request a new reminder email and try again."
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "text/html"},
            "body": _html_page("Authorization link expired", message),
        }

    try:
        tokens = exchange_code_for_tokens(code)
        save_initial_tokens(tokens)
    except Exception:
        logger.exception("Schwab OAuth callback failed")
        return {
            "statusCode": 502,
            "headers": {"Content-Type": "text/html"},
            "body": _html_page(
                "Authorization failed",
                "No tokens were changed. Request a new link and try again.",
            ),
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": _html_page(
            "Schwab successfully reauthorized",
            "Your portfolio job can run normally again.",
        ),
    }


def reminder_handler(event, context):
    """Email one reauthorization link when authorization needs attention.

    The scheduled reminder normally runs during the final 24 hours, but it
    must also recover from a missed schedule or an already-expired token.
    Schwab can still accept a new authorization-code exchange after the old
    refresh token expires.
    """

    from portfolio_agent.integrations.schwab_auth import load_tokens

    tokens = load_tokens()
    created_at = (tokens or {}).get("refresh_token_created_at")

    if created_at:
        expires_at = int(created_at) + REFRESH_TOKEN_LIFETIME
        if expires_at - int(time.time()) > REMINDER_WINDOW:
            return {"status": "not_due"}
        authorization_generation = int(created_at)
    else:
        # A missing/partially initialized secret still needs a way back in.
        logger.warning("Schwab authorization metadata is missing")
        authorization_generation = int(time.time())

    if reminder_was_sent(authorization_generation):
        return {"status": "already_sent"}

    _send_reauthorization_email(authorization_generation)
    return {"status": "sent"}


def lambda_handler(event, context):
    """Route API Gateway callbacks and scheduled reminder invocations."""

    if event.get("requestContext", {}).get("http"):
        return callback_handler(event, context)
    return reminder_handler(event, context)
