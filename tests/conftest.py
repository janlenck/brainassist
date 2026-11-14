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

    def __init__(self, session, url: str, token: str) -> None:
        self.url = url
        FakeBrainCall.created.append((url, token))

    async def async_version(self) -> ServerInfo:
        if isinstance(FakeBrainCall.info, Exception):
            raise FakeBrainCall.info
        return FakeBrainCall.info

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


__all__ = ["INFO", "TOKEN", "URL", "BrainCallError", "FakeBrainCall", "answer"]
