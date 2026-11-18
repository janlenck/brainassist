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
    BrainCallRefusedError,
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


# Planned calls (Brain Call 1.14.0)


async def test_version_says_whether_planned_calls_are_on(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{URL}/api/version", json={"api": 1, "version": "1.14.0", "tasks": True})
    assert (await client(hass).async_version()).tasks is True


async def test_version_says_whether_the_line_api_exists(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{URL}/api/version", json={"api": 1, "version": "1.19.0", "line": True})
    assert (await client(hass).async_version()).line is True


@pytest.mark.parametrize("status", [200, 503])
async def test_health_answers_also_when_unhealthy(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status):
    body = {"status": "ok" if status == 200 else "unhealthy", "problems": [], "sip": "REGISTERED", "line": "idle"}
    aioclient_mock.get(f"{URL}/health", status=status, json=body)
    assert await client(hass).async_health() == body


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 500, "text": "boom"}, BrainCallError),
        ({"json": {"hello": "world"}}, BrainCallError),
        ({"text": "<html>router</html>"}, BrainCallError),
        ({"exc": aiohttp.ClientConnectionError("refused")}, BrainCallConnectionError),
        ({"exc": TimeoutError()}, BrainCallConnectionError),
    ],
)
async def test_health_failures(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, kwargs, error) -> None:
    aioclient_mock.get(f"{URL}/health", **kwargs)
    with pytest.raises(error):
        await client(hass).async_health()


async def test_status(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    body = {"line": "in_call", "sip": "REGISTERED", "phone": True, "call": {"number": "0151", "direction": "in"}}
    aioclient_mock.get(f"{URL}/api/status", json=body)
    assert await client(hass).async_status() == body
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_status_without_a_line(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{URL}/api/status", json={"error": "nothing"})
    with pytest.raises(BrainCallError):
        await client(hass).async_status()


async def test_tasks(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{URL}/api/tasks", json={"tasks": [{"id": "a1", "status": "planned"}, {"no": "id"}]})
    assert await client(hass).async_tasks() == [{"id": "a1", "status": "planned"}]
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_tasks_not_a_list(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{URL}/api/tasks", json={"error": "x"})
    with pytest.raises(BrainCallError):
        await client(hass).async_tasks()


async def test_plan_task(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/api/tasks", status=201, json={"id": "b2", "status": "planned"})
    body = {"name": "Mark", "task": "Frag nach Freitag"}
    assert await client(hass).async_plan_task(body) == {"id": "b2", "status": "planned"}
    assert aioclient_mock.mock_calls[0][2] == body


async def test_planning_may_take_as_long_as_claude_needs(hass: HomeAssistant, monkeypatch) -> None:
    """Without a plan Brain Call asks Claude before it answers: 15 s would report a stored task as failed."""
    from custom_components.brainassist import api

    used = {}

    async def fake_json(method, path, body=None, limit=api.TIMEOUT):
        used[method] = limit
        return {"id": "x"} if method == "POST" else {"tasks": []}

    c = client(hass)
    monkeypatch.setattr(c, "_json", fake_json)
    await c.async_plan_task({"number": "0401", "task": "x"})
    await c.async_tasks()
    assert used["POST"] is api.PLAN_TIMEOUT and used["POST"].total >= 60
    assert used["GET"] is api.TIMEOUT


async def test_plan_task_refused_with_the_reason(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/api/tasks", status=400, json={"error": "numbers starting with 112 are blocked"})
    with pytest.raises(BrainCallRefusedError) as err:
        await client(hass).async_plan_task({"number": "112", "task": "x"})
    assert (err.value.status, err.value.reason) == (400, "numbers starting with 112 are blocked")


async def test_a_server_error_is_no_refusal(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{URL}/api/tasks", status=502, json={"error": "planning failed"})
    with pytest.raises(BrainCallError) as err:
        await client(hass).async_plan_task({"number": "0401", "task": "x"})
    assert not isinstance(err.value, BrainCallRefusedError) and "planning failed" in str(err.value)


async def test_cancel_task(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.delete(f"{URL}/api/tasks/a%2F1", json={"status": "cancelled"})
    await client(hass).async_cancel_task("a/1")
    assert aioclient_mock.call_count == 1


async def test_cancel_task_refused(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.delete(f"{URL}/api/tasks/c3", status=409, json={"error": "task is running"})
    with pytest.raises(BrainCallRefusedError) as err:
        await client(hass).async_cancel_task("c3")
    assert err.value.reason == "task is running"
