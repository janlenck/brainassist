"""Setup of a config entry: the agents, a refused token, an unreachable or incompatible Brain Call, diagnostics."""

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brainassist.api import BrainCallAuthError, BrainCallConnectionError, ServerInfo
from custom_components.brainassist.const import DOMAIN
from custom_components.brainassist.diagnostics import async_get_config_entry_diagnostics

from .conftest import TOKEN, URL


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_both_agents(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert loaded.state is ConfigEntryState.LOADED
    entities = er.async_entries_for_config_entry(er.async_get(hass), loaded.entry_id)
    assert {e.unique_id: e.entity_id for e in entities} == {
        f"{loaded.entry_id}_voice": "conversation.brain_assist_voice",
        f"{loaded.entry_id}_text": "conversation.brain_assist_text",
    }
    assert hass.states.get("conversation.brain_assist_voice").name == "Brain Assist Voice"
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), loaded.entry_id)
    assert device.identifiers == {(DOMAIN, loaded.entry_id)}
    assert (device.name, device.manufacturer, device.model, device.sw_version) == (
        "Brain Assist",
        "Brain Call",
        "Claudine",
        "1.11.0",
    )
    assert device.configuration_url == URL


async def test_unload(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert loaded.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("conversation.brain_assist_text").state == "unavailable"


async def test_refused_token_asks_for_a_new_one(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    brain_call.info = BrainCallAuthError("refused")
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_unreachable_brain_call_is_tried_again(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    brain_call.info = BrainCallConnectionError("down")
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not ir.async_get(hass).issues


async def test_incompatible_api_raises_a_repair_issue(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    issues = ir.async_get(hass)
    issue_id = f"incompatible_api_{entry.entry_id}"

    brain_call.info = ServerInfo(api=2, version="2.0.0", assistant="Claudine", phone=True, home_assistant=True)
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = issues.async_get_issue(DOMAIN, issue_id)
    assert issue.translation_key == "brain_assist_too_old"
    assert issue.translation_placeholders == {"version": "2.0.0", "server_api": "2", "client_api": "1"}

    brain_call.info = ServerInfo(api=0, version="1.10.9", assistant="Claudine", phone=True, home_assistant=True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, issue_id).translation_key == "brain_call_too_old"

    brain_call.info = ServerInfo(api=1, version="1.11.0", assistant="Claudine", phone=True, home_assistant=True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert issues.async_get_issue(DOMAIN, issue_id) is None  # gone once the versions fit again


async def test_removing_the_entry_removes_its_issue(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    brain_call.info = ServerInfo(api=2, version="2.0.0", assistant="", phone=True, home_assistant=True)
    await setup(hass, entry)
    assert ir.async_get(hass).issues
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not ir.async_get(hass).issues


async def test_diagnostics_never_contain_the_token(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    data = await async_get_config_entry_diagnostics(hass, loaded)
    assert TOKEN not in str(data)
    assert data["entry"]["data"] == {"url": URL, "token": "**REDACTED**"}
    assert data["server"] == {
        "api": 1,
        "version": "1.11.0",
        "assistant": "Claudine",
        "phone": True,
        "home_assistant": True,
        "tasks": False,
    }
    assert data["client_api"] == 1
