from unittest.mock import MagicMock, patch

import pytest

from packages.config import settings
from packages.notifications import create_seller_topic, delete_seller_topic, notify_seller


@pytest.fixture(autouse=True)
def reset_sns():
    import packages.notifications as notif

    notif._sns = None
    yield
    notif._sns = None


def test_create_seller_topic():
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client
        mock_client.create_topic.return_value = {
            "TopicArn": "arn:aws:sns:eu-west-2:123:salesrep-seller-1"
        }

        arn = create_seller_topic("1", "test@example.com")

        assert arn == "arn:aws:sns:eu-west-2:123:salesrep-seller-1"
        mock_client.create_topic.assert_called_once_with(Name="salesrep-seller-1")
        mock_client.subscribe.assert_called_once_with(
            TopicArn="arn:aws:sns:eu-west-2:123:salesrep-seller-1",
            Protocol="email",
            Endpoint="test@example.com",
        )


def test_notify_seller_enabled(monkeypatch):
    monkeypatch.setattr(settings, "sns_enabled", True)
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client

        notify_seller("arn:aws:sns:123", "Subject", "Message")

        mock_client.publish.assert_called_once_with(
            TopicArn="arn:aws:sns:123", Subject="Subject", Message="Message"
        )


def test_notify_seller_disabled(monkeypatch):
    monkeypatch.setattr(settings, "sns_enabled", False)
    with patch("boto3.client") as mock_boto:
        notify_seller("arn:aws:sns:123", "Subject", "Message")
        mock_boto.assert_not_called()


def test_notify_seller_no_topic(monkeypatch):
    monkeypatch.setattr(settings, "sns_enabled", True)
    with patch("boto3.client") as mock_boto:
        notify_seller("", "Subject", "Message")
        notify_seller(None, "Subject", "Message")
        mock_boto.assert_not_called()


def test_delete_seller_topic():
    with patch("boto3.client") as mock_boto:
        mock_client = MagicMock()
        mock_boto.return_value = mock_client

        delete_seller_topic("arn:aws:sns:123")

        mock_client.delete_topic.assert_called_once_with(TopicArn="arn:aws:sns:123")


def test_delete_seller_topic_none():
    with patch("boto3.client") as mock_boto:
        delete_seller_topic(None)
        delete_seller_topic("")
        mock_boto.assert_not_called()
