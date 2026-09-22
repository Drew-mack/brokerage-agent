from unittest.mock import MagicMock, patch

from portfolio_agent.integrations.schwab_auth import build_authorization_url
from portfolio_agent.reauth_handler import callback_handler, reminder_handler
from portfolio_agent.storage import reauth_storage


def test_authorization_url_contains_state():
    with patch(
        "portfolio_agent.integrations.schwab_auth.validate_config",
        return_value={
            "client_id": "client id",
            "client_secret": "secret",
            "callback_url": "https://example.com/callback",
        },
    ):
        url = build_authorization_url(state="state-value")

    assert "client_id=client%20id" in url
    assert "redirect_uri=https%3A%2F%2Fexample.com%2Fcallback" in url
    assert "state=state-value" in url


def test_callback_rejects_missing_or_invalid_state():
    with (
        patch("portfolio_agent.reauth_handler.consume_state", return_value=False),
        patch("portfolio_agent.integrations.schwab_auth.load_tokens", return_value=None),
        patch("portfolio_agent.reauth_handler.reminder_was_sent", return_value=False),
        patch("portfolio_agent.reauth_handler._send_reauthorization_email") as send_email,
    ):
        response = callback_handler(
            {"queryStringParameters": {"code": "code", "state": "invalid"}},
            None,
        )

    assert response["statusCode"] == 400
    assert "expired" in response["body"]
    send_email.assert_called_once()


def test_reminder_sends_recovery_link_after_expiration():
    expired = {"refresh_token_created_at": 1}
    with (
        patch("portfolio_agent.integrations.schwab_auth.load_tokens", return_value=expired),
        patch(
            "portfolio_agent.integrations.schwab_auth.refresh_token_is_valid",
            return_value=False,
        ),
        patch("portfolio_agent.reauth_handler.reminder_was_sent", return_value=False),
        patch("portfolio_agent.reauth_handler.create_state", return_value="state"),
        patch("portfolio_agent.reauth_handler.build_authorization_url", return_value="https://example.com/auth"),
        patch("portfolio_agent.reauth_handler.SESEmailSender") as sender_class,
        patch("portfolio_agent.reauth_handler.mark_reminder_sent") as mark_sent,
    ):
        response = reminder_handler({}, None)

    assert response == {"status": "sent"}
    sender_class.return_value.send.assert_called_once()
    mark_sent.assert_called_once_with(1)


def test_reminder_sends_recovery_link_when_token_metadata_is_missing():
    with (
        patch("portfolio_agent.integrations.schwab_auth.load_tokens", return_value=None),
        patch("portfolio_agent.reauth_handler.reminder_was_sent", return_value=False),
        patch("portfolio_agent.reauth_handler.create_state", return_value="state"),
        patch("portfolio_agent.reauth_handler.build_authorization_url", return_value="https://example.com/auth"),
        patch("portfolio_agent.reauth_handler.SESEmailSender") as sender_class,
        patch("portfolio_agent.reauth_handler.mark_reminder_sent") as mark_sent,
    ):
        response = reminder_handler({}, None)

    assert response == {"status": "sent"}
    sender_class.return_value.send.assert_called_once()
    mark_sent.assert_called_once()


def test_reminder_marker_expires_with_the_oauth_link():
    table = MagicMock()
    table.get_item.return_value = {"Item": {"refresh_token_created_at": 10, "sent_at": 100}}
    with patch("portfolio_agent.storage.reauth_storage._table", return_value=table), patch(
        "portfolio_agent.storage.reauth_storage.time.time", return_value=100 + 15 * 60
    ):
        assert not reauth_storage.reminder_was_sent(10)


def test_reminder_marker_is_active_for_a_fresh_oauth_link():
    table = MagicMock()
    table.get_item.return_value = {"Item": {"refresh_token_created_at": 10, "sent_at": 100}}
    with patch("portfolio_agent.storage.reauth_storage._table", return_value=table), patch(
        "portfolio_agent.storage.reauth_storage.time.time", return_value=100 + 60
    ):
        assert reauth_storage.reminder_was_sent(10)
