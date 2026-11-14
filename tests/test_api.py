"""The client: what it sends to Brain Call and how it reads answers and failures."""

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.brainassist.api import (
    BrainCallAuthError,
    BrainCallClient,
    BrainCallConnectionError,
    BrainCallError,
    BrainCallTooOldError,
    ServerInfo,
    normalize_url,
)

from .conftest import TOKEN, URL


def client(hass: HomeAssistant, url: str = URL) -> BrainCallClient:
    return BrainCallClient(async_get_clientsession(hass), url, TOKEN)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("http://127.0.0.1:8099/", URL),
        (" 127.0.0.1:8099 ", URL),
        ("https://brain.example/", "https://brain.example"),
        ("", ""),
    ],
)
def test_normalize_url(typed: str, stored: str) -> None:
    assert normalize_url(typed) == stored


async def test_version(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(
        f"{URL}/api/version",
        json={"api": 1, "version": "1.11.0", "assistant": "Claudine", "phone": False, "home_assistant": True},
    )
    info = await client(hass, URL + "/").async_version()
    assert info == ServerInfo(api=1, version="1.11.0", assistant="Claudine", phone=False, home_assistant=True)
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 401, "json": {"error": "unauthorized"}}, BrainCallAuthError),
        ({"status": 404, "json": {"error": "not found"}}, BrainCallTooOldError),
        ({"status": 500, "text": "boom"}, BrainCallError),
        ({"text": "<html>router</html>"}, BrainCallError),
        ({"json": {"hello": "world"}}, BrainCallError),
        ({"exc": aiohttp.ClientConnectionError("refused")}, BrainCallConnectionError),
        ({"exc": TimeoutError()}, BrainCallConnectionError),
    ],
)
async def test_version_failures(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs, error) -> None:
    aioclient_mock.get(f"{URL}/api/version", **kwargs)
    with pytest.raises(error) as raised:
        await client(hass).async_version()
    assert type(raised.value) is error


async def test_converse_reads_the_stream_line_by_line(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        f"{URL}/api/converse",
        content=(
            b'{"type": "start", "conversation_id": "c1", "access": "admin"}\n'
            b'{"type": "text", "text": "Es sind 21 Grad \xc2\xb0C."}\n'
            b"\n"
            b'{"type": "end", "conversation_id": "c1", "continue_conversation": false}\n'
        ),
    )
    payload = {"text": "Wie warm ist es?", "channel": "voice"}
    events = [event async for event in client(hass).async_converse(payload)]
    assert [e["type"] for e in events] == ["start", "text", "end"]
    assert events[1]["text"] == "Es sind 21 Grad °C."
    method, _, sent, headers = aioclient_mock.mock_calls[0]
    assert (method, sent, headers["Authorization"]) == ("POST", payload, f"Bearer {TOKEN}")


@pytest.mark.parametrize(
    ("kwargs", "error", "text"),
    [
        ({"status": 401, "json": {"error": "unauthorized"}}, BrainCallAuthError, "token"),
        ({"status": 404, "json": {"error": "not found"}}, BrainCallTooOldError, "1.11.0"),
        ({"status": 400, "json": {"error": "no text"}}, BrainCallError, "400: no text"),
        ({"content": b"not json\n"}, BrainCallError, "not valid JSON"),
        ({"exc": aiohttp.ClientConnectionError("reset")}, BrainCallConnectionError, "reset"),
    ],
)
async def test_converse_failures(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs, error, text) -> None:
    aioclient_mock.post(f"{URL}/api/converse", **kwargs)
    with pytest.raises(error, match=text):
        [event async for event in client(hass).async_converse({"text": "x"})]
