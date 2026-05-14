"""Tests for packages.platform_adapters.ebay.notifications.subscribe_messages.

Mocks Trading API with respx to verify the request shape and the parsing of
Success / Warning / Failure / non-200 / malformed-XML / network-error paths.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from packages.platform_adapters.ebay import notifications
from packages.platform_adapters.ebay.notifications import subscribe_messages


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    monkeypatch.setattr(
        notifications,
        "settings",
        type(
            "FakeSettings",
            (),
            {"ebay_env": "production", "ebay_marketplace_id": "EBAY_GB"},
        )(),
    )


_SUCCESS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SetNotificationPreferencesResponse xmlns="urn:ebay:apis:eBLBaseComponents">
  <Ack>Success</Ack>
</SetNotificationPreferencesResponse>"""

_WARNING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SetNotificationPreferencesResponse xmlns="urn:ebay:apis:eBLBaseComponents">
  <Ack>Warning</Ack>
  <Errors>
    <ShortMessage>Some warning</ShortMessage>
    <SeverityCode>Warning</SeverityCode>
  </Errors>
</SetNotificationPreferencesResponse>"""

_FAILURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<SetNotificationPreferencesResponse xmlns="urn:ebay:apis:eBLBaseComponents">
  <Ack>Failure</Ack>
  <Errors>
    <ShortMessage>Invalid token</ShortMessage>
    <LongMessage>The auth token is invalid.</LongMessage>
  </Errors>
</SetNotificationPreferencesResponse>"""


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_success_sends_correct_request():
    route = respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=_SUCCESS_XML)
    )

    ok = await subscribe_messages("test_token")

    assert ok is True
    assert route.called

    req = route.calls[0].request
    body = req.content.decode("utf-8")
    assert "SetNotificationPreferencesRequest" in body
    assert "<ApplicationEnable>Enable</ApplicationEnable>" in body
    assert "MyMessageseBayMessage" in body
    assert "MyMessagesM2MMessage" in body
    assert "AskSellerQuestion" in body

    assert req.headers["X-EBAY-API-CALL-NAME"] == "SetNotificationPreferences"
    assert req.headers["X-EBAY-API-IAF-TOKEN"] == "test_token"
    assert req.headers["X-EBAY-API-SITEID"] == "3"  # EBAY_GB


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_treats_warning_as_success():
    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=_WARNING_XML)
    )
    assert await subscribe_messages("test_token") is True


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_failure_returns_false():
    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=_FAILURE_XML)
    )
    assert await subscribe_messages("test_token") is False


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_non_200_returns_false():
    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(500, text="boom")
    )
    assert await subscribe_messages("test_token") is False


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_invalid_xml_returns_false():
    respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text="not xml")
    )
    assert await subscribe_messages("test_token") is False


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_network_error_returns_false():
    respx.post("https://api.ebay.com/ws/api.dll").mock(side_effect=httpx.ConnectError("nope"))
    assert await subscribe_messages("test_token") is False


@pytest.mark.asyncio
@respx.mock
async def test_subscribe_messages_uses_marketplace_override():
    route = respx.post("https://api.ebay.com/ws/api.dll").mock(
        return_value=httpx.Response(200, text=_SUCCESS_XML)
    )
    await subscribe_messages("test_token", marketplace_id="EBAY_US")
    assert route.calls[0].request.headers["X-EBAY-API-SITEID"] == "0"
