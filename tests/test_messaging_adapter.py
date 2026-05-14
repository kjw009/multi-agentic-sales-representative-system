"""Tests for eBay messaging adapter.

Mocks eBay API calls using respx to verify:
  - XML body structure for send_message
  - Success and failure paths for get_conversation
"""

import uuid

import httpx
import pytest
import respx

from packages.platform_adapters.ebay.messaging import get_conversation, send_message


# Mock session and token
class FakeSession:
    async def scalar(self, stmt):
        return None


class FakeToken:
    access_token = "test_access_token"
    seller_id = uuid.uuid4()


# Patch get_seller_token to return our fake token
@pytest.fixture(autouse=True)
def _patch_get_seller_token(monkeypatch):
    async def _fake_get_seller_token(seller_id, session):
        return FakeToken()

    monkeypatch.setattr(
        "packages.platform_adapters.ebay.messaging.get_seller_token",
        _fake_get_seller_token,
    )
    # Ensure adapter uses production URL matching our respx mocks
    monkeypatch.setattr(
        "packages.platform_adapters.ebay.messaging.settings",
        type(
            "FakeSettings",
            (),
            {
                "ebay_env": "production",
                "ebay_marketplace_id": "EBAY_GB",
            },
        )(),
    )


@pytest.mark.asyncio
@respx.mock
async def test_send_message_success():
    """send_message should send AddMemberMessageAAQToPartner XML with all required fields."""
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <AddMemberMessageAAQToPartnerResponse xmlns="urn:ebay:apis:eBLBaseComponents">
        <Ack>Success</Ack>
    </AddMemberMessageAAQToPartnerResponse>"""

    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=xml_response)
    )

    result = await send_message(
        text="Thanks for your interest!",
        seller_id=uuid.uuid4(),
        session=FakeSession(),
        parent_message_id="msg999",
        recipient_id="buyer_test",
        item_id="123456789",
    )

    assert result["status"] == "success"
    assert result["parent_message_id"] == "msg999"

    call = respx.calls[0]
    body = call.request.content.decode("utf-8")
    assert "AddMemberMessageAAQToPartnerRequest" in body
    assert "Thanks for your interest!" in body
    assert "<ParentMessageID>msg999</ParentMessageID>" in body
    assert "<RecipientID>buyer_test</RecipientID>" in body
    assert "<ItemID>123456789</ItemID>" in body
    assert "<QuestionType>General</QuestionType>" in body
    assert "<MessageType>ContactEBayMember</MessageType>" in body
    assert dict(call.request.headers)["x-ebay-api-call-name"] == "AddMemberMessageAAQToPartner"


@pytest.mark.asyncio
@respx.mock
async def test_send_message_omits_parent_message_id_when_blank():
    """ParentMessageID is optional — when caller passes empty string, don't emit the tag."""
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <AddMemberMessageAAQToPartnerResponse xmlns="urn:ebay:apis:eBLBaseComponents">
        <Ack>Success</Ack>
    </AddMemberMessageAAQToPartnerResponse>"""

    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=xml_response)
    )

    await send_message(
        text="hi",
        seller_id=uuid.uuid4(),
        session=FakeSession(),
        parent_message_id="",
        recipient_id="buyer_test",
        item_id="123456789",
    )

    body = respx.calls[0].request.content.decode("utf-8")
    assert "ParentMessageID" not in body


@pytest.mark.asyncio
@respx.mock
async def test_send_message_failure():
    """send_message should raise RuntimeError on API failure."""
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <AddMemberMessageAAQToPartnerResponse xmlns="urn:ebay:apis:eBLBaseComponents">
        <Ack>Failure</Ack>
        <Errors>
            <ShortMessage>Invalid token</ShortMessage>
            <LongMessage>The auth token is invalid.</LongMessage>
        </Errors>
    </AddMemberMessageAAQToPartnerResponse>"""

    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=xml_response)
    )

    with pytest.raises(RuntimeError, match="send_message failed"):
        await send_message(
            text="Test message",
            seller_id=uuid.uuid4(),
            session=FakeSession(),
            parent_message_id="msg999",
            recipient_id="buyer_test",
            item_id="123456789",
        )


@pytest.mark.asyncio
@respx.mock
async def test_get_conversation_success():
    """get_conversation should parse messages from Trading API response."""
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <GetMyMessagesResponse xmlns="urn:ebay:apis:eBLBaseComponents">
        <Ack>Success</Ack>
        <Messages>
            <Message>
                <MessageID>msg001</MessageID>
                <Sender>buyer_test</Sender>
                <Text>Is this still available?</Text>
                <ReceiveDate>2026-05-10T12:00:00Z</ReceiveDate>
            </Message>
        </Messages>
    </GetMyMessagesResponse>"""

    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=xml_response)
    )

    result = await get_conversation(
        conversation_id="conv123",
        seller_id=uuid.uuid4(),
        session=FakeSession(),
    )

    assert result["conversation_id"] == "conv123"
    assert len(result["messages"]) == 1
    assert result["messages"][0]["message_id"] == "msg001"
    assert result["messages"][0]["text"] == "Is this still available?"


@pytest.mark.asyncio
@respx.mock
async def test_get_conversation_failure():
    """get_conversation should return empty messages on API failure."""
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <GetMyMessagesResponse xmlns="urn:ebay:apis:eBLBaseComponents">
        <Ack>Failure</Ack>
    </GetMyMessagesResponse>"""

    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=xml_response)
    )

    result = await get_conversation(
        conversation_id="conv123",
        seller_id=uuid.uuid4(),
        session=FakeSession(),
    )

    assert result["messages"] == []
    assert "error" in result
