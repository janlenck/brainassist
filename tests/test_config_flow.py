"""The config flow: address and token, the failures a user can run into, new token, changed address."""

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.brainassist.api import (
    BrainCallAuthError,
    BrainCallConnectionError,
    BrainCallError,
    BrainCallTooOldError,
    ServerInfo,
)
from custom_components.brainassist.const import DOMAIN

from .conftest import INFO, TOKEN, URL


async def start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual" and not result["errors"]
    return result


async def test_user_flow_creates_the_entry(hass: HomeAssistant, brain_call) -> None:
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "127.0.0.1:8099/", CONF_TOKEN: f" {TOKEN} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Claudine"
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == URL
    assert brain_call.created[0] == (URL, TOKEN)


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (BrainCallAuthError("no"), "invalid_auth"),
        (BrainCallConnectionError("down"), "cannot_connect"),
        (BrainCallTooOldError("old"), "too_old"),
        (BrainCallError("garbage"), "unknown"),
        (ServerInfo(api=0, version="1.10.1", assistant="", phone=True, home_assistant=True), "too_old"),
        (ServerInfo(api=2, version="2.0.0", assistant="", phone=True, home_assistant=True), "too_new"),
    ],
)
async def test_user_flow_says_what_is_wrong_and_recovers(hass: HomeAssistant, brain_call, failure, error) -> None:
    brain_call.info = failure
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}

    brain_call.info = INFO
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_brain_call_is_set_up_only_once(hass: HomeAssistant, brain_call, entry: MockConfigEntry) -> None:
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL + "/", CONF_TOKEN: "other"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_takes_a_new_token(hass: HomeAssistant, brain_call, entry: MockConfigEntry) -> None:
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["url"] == URL

    brain_call.info = BrainCallAuthError("still wrong")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    brain_call.info = INFO
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data == {CONF_URL: URL, CONF_TOKEN: "new-token"}


async def test_reconfigure_changes_address_and_token(hass: HomeAssistant, brain_call, entry: MockConfigEntry) -> None:
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://192.168.178.20:8099", CONF_TOKEN: "lan-token"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.data == {CONF_URL: "http://192.168.178.20:8099", CONF_TOKEN: "lan-token"}
    assert entry.unique_id == "http://192.168.178.20:8099"


async def test_reconfigure_keeps_the_address(hass: HomeAssistant, brain_call, entry: MockConfigEntry) -> None:
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_TOKEN: "new"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_TOKEN] == "new" and entry.unique_id == URL


async def test_reconfigure_not_onto_another_entry(hass: HomeAssistant, brain_call, entry: MockConfigEntry) -> None:
    other = MockConfigEntry(domain=DOMAIN, unique_id="http://other:8099", data={CONF_URL: "http://other:8099"})
    other.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://other:8099", CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
