"""The config flow on Home Assistant OS: the running Brain Call app announces itself, the user confirms.

Brain Assist never adds, installs, configures or starts the app itself.
"""

from pathlib import Path
from typing import Any

from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from custom_components.brainassist import config_flow
from custom_components.brainassist.api import BrainCallConnectionError
from custom_components.brainassist.const import DOMAIN

from .conftest import TOKEN, URL

ANNOUNCED = {"host": "127.0.0.1", "port": 8099, "token": TOKEN}


def discovery(**config: Any) -> HassioServiceInfo:
    return HassioServiceInfo(
        config=config or ANNOUNCED, name="BrainCall", slug="98055a04_braincall", uuid="1234567890abcdef"
    )


# Discovery


async def test_discovery_asks_and_creates_the_entry(hass: HomeAssistant, brain_call) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery())
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "hassio_confirm"
    assert result["description_placeholders"] == {"name": "BrainCall"}
    assert brain_call.created == []  # nothing is asked before the user agrees

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Claudine"
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == URL


async def test_discovery_with_a_new_token_updates_the_entry(hass: HomeAssistant, brain_call, entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery(host="127.0.0.1", port=8099, token="new-token")
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data == {CONF_URL: URL, CONF_TOKEN: "new-token"}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_discovery_without_token_is_refused(hass: HomeAssistant, brain_call) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery(host="127.0.0.1", port=8099)
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "invalid_discovery_info"


async def test_discovery_of_an_app_that_does_not_answer(hass: HomeAssistant, brain_call) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery())
    brain_call.info = BrainCallConnectionError("down")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "cannot_connect"


# Adding the integration by hand


async def test_adding_by_hand_shows_the_form_also_on_home_assistant_os(hass: HomeAssistant, brain_call) -> None:
    """No menu and no app path: the form for address and token at once."""
    hass.config.components.add("hassio")  # what is_hassio looks at: Home Assistant OS with the Supervisor
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"


async def test_adding_by_hand_while_the_app_waits_as_discovered(hass: HomeAssistant, brain_call) -> None:
    """Typing the address of the discovered app is not refused, and the discovered flow does not stay behind."""
    await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery())
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL, CONF_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


def test_nothing_reaches_the_supervisor() -> None:
    """The flow has no way to add a repository, install, configure or start an app."""
    source = Path(config_flow.__file__).read_text(encoding="utf-8")
    for name in (
        "AddonManager",
        "get_supervisor_client",
        "add_repository",
        "async_install_addon",
        "async_set_addon_options",
        "async_start_addon",
    ):
        assert name not in source, name
    assert not (Path(config_flow.__file__).parent / "addon.py").exists()
