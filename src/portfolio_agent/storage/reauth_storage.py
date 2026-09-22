"""One-time state storage for the Schwab reauthorization flow."""

import secrets
import time

import boto3
from botocore.exceptions import ClientError

from portfolio_agent.config import settings

STATE_TTL_SECONDS = 15 * 60
REMINDER_KEY = "reminder"


class ReauthStateError(Exception):
    """Raised when OAuth state cannot be created or consumed."""


def _table():
    return boto3.resource("dynamodb", region_name=settings.aws_region).Table(
        settings.reauth_table_name
    )


def create_state(refresh_token_created_at: int) -> str:
    """Create a short-lived, unpredictable OAuth state value."""

    state = secrets.token_urlsafe(32)
    now = int(time.time())
    try:
        _table().put_item(
            Item={
                "key": state,
                "kind": "oauth_state",
                "created_at": now,
                "expires_at": now + STATE_TTL_SECONDS,
                "refresh_token_created_at": int(refresh_token_created_at),
            },
            ConditionExpression="attribute_not_exists(#key)",
            ExpressionAttributeNames={"#key": "key"},
        )
    except Exception as error:
        raise ReauthStateError("Could not create OAuth state.") from error
    return state


def consume_state(state: str) -> bool:
    """Consume valid OAuth state exactly once."""

    if not state:
        return False
    try:
        _table().update_item(
            Key={"key": state},
            UpdateExpression="SET #kind = :consumed",
            ConditionExpression=(
                "#kind = :oauth_state AND expires_at > :now"
            ),
            ExpressionAttributeNames={"#kind": "kind"},
            ExpressionAttributeValues={
                ":oauth_state": "oauth_state",
                ":consumed": "consumed",
                ":now": int(time.time()),
            },
        )
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise ReauthStateError("Could not consume OAuth state.") from error


def reminder_was_sent(refresh_token_created_at: int) -> bool:
    """Return whether an active link was recently sent for this authorization.

    DynamoDB TTL cleanup is asynchronous, so the marker must be checked by
    timestamp as well. Once the 15-minute OAuth state expires, the next
    scheduled run may issue a replacement link even if the old marker still
    exists in the table.
    """

    item = _table().get_item(Key={"key": REMINDER_KEY}).get("Item")
    if not item:
        return False

    sent_at = int(item.get("sent_at", 0))
    return (
        int(item.get("refresh_token_created_at", 0)) == int(refresh_token_created_at)
        and sent_at + STATE_TTL_SECONDS > int(time.time())
    )


def mark_reminder_sent(refresh_token_created_at: int) -> None:
    """Record the authorization generation that received a reminder."""

    _table().put_item(
        Item={
            "key": REMINDER_KEY,
            "kind": "reminder",
            "refresh_token_created_at": int(refresh_token_created_at),
            "sent_at": int(time.time()),
            "expires_at": int(time.time()) + 14 * 24 * 60 * 60,
        }
    )
