"""Shared fixtures: a config entry and a Brain Call that answers from a script instead of the network."""

from collections.abc import AsyncIterator
from typing import Any

from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brainassist.api import BrainCallError, ServerInfo
from custom_components.brainassist.const import DOMAIN

URL = "http://127.0.0.1:8099"
TOKEN = "secret-token"
INFO = ServerInfo(api=1, version="1.11.0", assistant="Claudine", phone=True, home_assistant=True)
# GET /health of a Brain Call older than 1.19.0: no line yet
HEALTH = {"status": "ok", "problems": [], "sip": "REGISTERED", "busy": False, "phone": True}


@pytest.fixture(autouse=True)
async def home_assistant(hass: HomeAssistant, enable_custom_integrations) -> None:
    """Let Home Assistant load the integration from custom_components/.

    The core component comes first: `conversation`, which Brain Assist depends on, needs it.
    """
    assert await async_setup_component(hass, "homeassistant", {})


class FakeBrainCall:
    """Stands in for BrainCallClient: version() answers with `info` or raises it, converse() plays `events`."""

    info: ServerInfo | Exception = INFO
    events: list[dict[str, Any] | Exception] = []
    requests: list[dict[str, Any]] = []
    created: list[tuple[str, str]] = []
    tasks: list[dict[str, Any]] | Exception = []  # what GET /api/tasks answers
    planned: list[dict[str, Any]] = []  # bodies of POST /api/tasks
    plan_error: Exception | None = None
    cancelled: list[str] = []
    cancel_error: Exception | None = None
    health: dict[str, Any] | Exception = {}  # what GET /health answers
    status: dict[str, Any] | Exception = {}  # what GET /api/status answers
    messages: list[dict[str, Any]] | Exception = []  # the entries GET /api/messages answers, newest first
    message_changes: list[tuple[str, dict[str, bool]]] = []  # PATCH /api/messages/<id>
    deleted_messages: list[str] = []

    def __init__(self, session, url: str, token: str) -> None:
        self.url = url
        FakeBrainCall.created.append((url, token))

    async def async_version(self) -> ServerInfo:
        if isinstance(FakeBrainCall.info, Exception):
            raise FakeBrainCall.info
        return FakeBrainCall.info

    async def async_health(self) -> dict[str, Any]:
        if isinstance(FakeBrainCall.health, Exception):
            raise FakeBrainCall.health
        return dict(FakeBrainCall.health)

    async def async_status(self) -> dict[str, Any]:
        if isinstance(FakeBrainCall.status, Exception):
            raise FakeBrainCall.status
        return dict(FakeBrainCall.status)

    async def async_tasks(self) -> list[dict[str, Any]]:
        if isinstance(FakeBrainCall.tasks, Exception):
            raise FakeBrainCall.tasks
        return [dict(t) for t in FakeBrainCall.tasks]

    async def async_plan_task(self, body: dict[str, Any]) -> dict[str, Any]:
        FakeBrainCall.planned.append(body)
        if FakeBrainCall.plan_error is not None:
            raise FakeBrainCall.plan_error
        task = {"id": f"t{len(FakeBrainCall.planned)}", "status": "planned", "goal": body["task"], **body}
        if isinstance(FakeBrainCall.tasks, list):
            FakeBrainCall.tasks.append(task)
        return task

    async def async_cancel_task(self, task_id: str) -> None:
        if FakeBrainCall.cancel_error is not None:
            raise FakeBrainCall.cancel_error
        FakeBrainCall.cancelled.append(task_id)
        for task in FakeBrainCall.tasks if isinstance(FakeBrainCall.tasks, list) else []:
            if task["id"] == task_id:
                task["status"] = "cancelled"

    async def async_messages(self) -> dict[str, Any]:
        if isinstance(FakeBrainCall.messages, Exception):
            raise FakeBrainCall.messages
        items = [dict(m) for m in FakeBrainCall.messages]
        open_ = [m for m in items if not m.get("done") and (not m.get("heard") or m.get("callback"))]
        return {
            "messages": items,
            "open": len(open_),
            "new": sum(1 for m in open_ if not m.get("heard")),
            "callbacks": sum(1 for m in open_ if m.get("callback")),
        }

    async def async_update_message(self, message_id: str, **change: bool) -> dict[str, Any]:
        if isinstance(FakeBrainCall.messages, Exception):
            raise FakeBrainCall.messages
        FakeBrainCall.message_changes.append((message_id, change))
        for m in FakeBrainCall.messages:
            if m["id"] == message_id:
                for key, value in change.items():
                    if value:
                        m[key] = "2026-11-19T20:00:00+00:00"
                    else:
                        m.pop(key, None)
                return dict(m)
        raise BrainCallError("Brain Call answered 404: not found")

    async def async_delete_message(self, message_id: str) -> None:
        FakeBrainCall.deleted_messages.append(message_id)
        if isinstance(FakeBrainCall.messages, list):
            FakeBrainCall.messages = [m for m in FakeBrainCall.messages if m["id"] != message_id]

    async def async_converse(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        FakeBrainCall.requests.append(payload)
        for event in FakeBrainCall.events:
            if isinstance(event, Exception):
                raise event
            yield event


@pytest.fixture
def brain_call(monkeypatch) -> type[FakeBrainCall]:
    """Brain Call without network, for setup, config flow and conversations."""
    FakeBrainCall.info = INFO
    FakeBrainCall.events = []
    FakeBrainCall.requests = []
    FakeBrainCall.created = []
    FakeBrainCall.tasks = []
    FakeBrainCall.planned = []
    FakeBrainCall.plan_error = None
    FakeBrainCall.cancelled = []
    FakeBrainCall.cancel_error = None
    FakeBrainCall.health = dict(HEALTH)
    FakeBrainCall.status = {"line": "idle", "sip": "REGISTERED", "phone": True, "call": None}
    FakeBrainCall.messages = []
    FakeBrainCall.message_changes = []
    FakeBrainCall.deleted_messages = []
    monkeypatch.setattr("custom_components.brainassist.BrainCallClient", FakeBrainCall)
    monkeypatch.setattr("custom_components.brainassist.config_flow.BrainCallClient", FakeBrainCall)
    return FakeBrainCall


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry for Brain Call on this host, not set up yet."""
    entry = MockConfigEntry(domain=DOMAIN, title="Claudine", unique_id=URL, data={CONF_URL: URL, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> MockConfigEntry:
    """The config entry, set up."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def answer(*texts: str, continue_conversation: bool = False) -> list[dict[str, Any]]:
    """The events of a plain answer."""
    return [
        {"type": "start", "conversation_id": "c1", "access": "admin"},
        *({"type": "text", "text": t} for t in texts),
        {"type": "end", "conversation_id": "c1", "continue_conversation": continue_conversation},
    ]


__all__ = ["HEALTH", "INFO", "TOKEN", "URL", "BrainCallError", "FakeBrainCall", "answer"]
