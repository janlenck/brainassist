"""The config flow on Home Assistant OS: the Brain Call app announces itself, or is used, started or installed."""

import asyncio
from typing import Any

from aiohasupervisor import SupervisorError
from homeassistant.components.hassio import AddonError, AddonInfo, AddonState
from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.brainassist import config_flow
from custom_components.brainassist.api import BrainCallConnectionError, ServerInfo
from custom_components.brainassist.const import DOMAIN

from .conftest import INFO, TOKEN, URL

ANNOUNCED = {"host": "127.0.0.1", "port": 8099, "token": TOKEN}
KEY = "sk-ant-test"


def discovery(**config: Any) -> HassioServiceInfo:
    return HassioServiceInfo(
        config=config or ANNOUNCED, name="BrainCall", slug="98055a04_braincall", uuid="1234567890abcdef"
    )


class FakeApp:
    """Stands in for AddonManager: an app with a state, options, and the discovery message it sends when running."""

    def __init__(
        self, state: AddonState = AddonState.NOT_INSTALLED, options: dict | None = None, version: str = "1.13.0"
    ) -> None:
        self.state = state
        self.version = version
        self.slow = False
        self.options = options or {}
        self.announces: dict | None = ANNOUNCED
        self.calls: list[Any] = []
        self.fail: str | None = None

    async def async_get_addon_info(self) -> AddonInfo:
        return AddonInfo(
            available=True,
            hostname=None,
            options=self.options,
            state=self.state,
            update_available=False,
            version=None if self.state is AddonState.NOT_INSTALLED else self.version,
        )

    async def async_install_addon(self) -> None:
        self.calls.append("install")
        if self.fail == "install":
            raise AddonError("install failed")
        self.state = AddonState.NOT_RUNNING
        self.options = {"anthropic_api_key": "", "callers": [], "language": "de"}

    async def async_set_addon_options(self, options: dict) -> None:
        self.calls.append(("options", options))
        self.options = options

    async def async_start_addon(self) -> None:
        self.calls.append("start")
        if self.fail == "start":
            raise AddonError("start failed")
        self.state = AddonState.RUNNING

    async def async_get_addon_discovery_info(self) -> dict:
        if self.slow:  # a Supervisor that takes far longer than the whole wait
            await asyncio.sleep(3600)
        if self.state is not AddonState.RUNNING or self.announces is None:
            raise AddonError("no discovery")
        return self.announces


@pytest.fixture
def supervisor(monkeypatch) -> dict[str, Any]:
    """Home Assistant OS with an app store: which slug the store knows, the app, and what was added."""
    state: dict[str, Any] = {"slug": None, "app": FakeApp(), "added": []}

    async def find(hass):
        if isinstance(state["slug"], Exception):
            raise state["slug"]
        return state["slug"]

    async def add(hass):
        state["added"].append(config_flow.APP_REPOSITORY)
        state["slug"] = "98055a04_braincall"
        return state["slug"]

    def manager(hass, slug):
        state.setdefault("slugs", []).append(slug)
        return state["app"]

    monkeypatch.setattr(config_flow, "is_hassio", lambda hass: True)
    monkeypatch.setattr(config_flow, "async_find_app_slug", find)
    monkeypatch.setattr(config_flow, "async_add_repository", add)
    monkeypatch.setattr(config_flow, "get_app_manager", manager)
    monkeypatch.setattr(config_flow, "START_POLL_S", 0)
    monkeypatch.setattr(config_flow, "START_TIMEOUT_S", 0.2)
    return state


async def choose_app(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU and result["menu_options"] == ["app", "manual"]
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "app"})


async def run_progress(hass: HomeAssistant, result):
    """Let the tasks of the progress steps (install, start) finish and go on to the step after them."""
    while result["type"] in (FlowResultType.SHOW_PROGRESS, FlowResultType.SHOW_PROGRESS_DONE):
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


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


# The app on this system


async def test_without_supervisor_only_address_and_token(hass: HomeAssistant, brain_call) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"


async def test_manual_from_the_menu(hass: HomeAssistant, brain_call, supervisor) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"


async def test_a_running_app_is_used(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.RUNNING, {"anthropic_api_key": KEY})
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}
    assert supervisor["app"].calls == []  # nothing installed, started or changed


async def test_a_repository_added_under_another_url_is_found(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "e59904bc_braincall"
    supervisor["app"] = FakeApp(AddonState.RUNNING, {"anthropic_api_key": KEY})
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert supervisor["slugs"] == ["e59904bc_braincall"] * len(supervisor["slugs"])
    assert supervisor["added"] == []


async def test_an_app_too_old_to_announce_itself_leads_to_the_form(hass: HomeAssistant, brain_call, supervisor):
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.RUNNING, {"anthropic_api_key": KEY})
    supervisor["app"].announces = None
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"


async def test_a_stopped_app_too_old_to_announce_itself_is_started_and_leads_to_the_form(
    hass: HomeAssistant, brain_call, supervisor
) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": KEY}, version="1.12.0")
    supervisor["app"].announces = None
    result = await run_progress(hass, await choose_app(hass))
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "manual"
    assert supervisor["app"].calls == ["start"]


async def test_the_wait_after_the_start_includes_slow_requests(hass: HomeAssistant, brain_call, supervisor) -> None:
    """The time limit holds for the whole wait, not only for the pauses between the looks."""
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": KEY})
    supervisor["app"].slow = True
    result = await run_progress(hass, await choose_app(hass))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "app_start_failed"


async def test_a_stopped_app_is_started(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": KEY})
    result = await run_progress(hass, await choose_app(hass))
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}
    assert supervisor["app"].calls == ["start"]


async def test_a_stopped_app_without_claude_key_gets_one(hass: HomeAssistant, brain_call, supervisor) -> None:
    MockUser(name="Jan", is_owner=True).add_to_hass(hass)
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": "", "callers": []})
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "app_settings"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"anthropic_api_key": f" {KEY} "})
    result = await run_progress(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = {"anthropic_api_key": KEY, "callers": [{"ha_user": "Jan", "name": "Jan", "access": "admin"}]}
    assert supervisor["app"].calls == [("options", options), "start"]


async def test_install_needs_consent_and_adds_the_repository(hass: HomeAssistant, brain_call, supervisor) -> None:
    MockUser(name="Jan", is_owner=True).add_to_hass(hass)
    MockUser(name="Maryna").add_to_hass(hass)
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "add_repository_confirm"
    assert result["description_placeholders"] == {"repository": "https://github.com/janlenck/braincall"}
    assert supervisor["added"] == [] and supervisor["app"].calls == []  # nothing happens before the user agrees
    admin = next(k for k in result["data_schema"].schema if k == "admin")
    assert admin.default() == "Jan"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"anthropic_api_key": KEY, "admin": "Maryna"}
    )
    result = await run_progress(hass, result)  # install, then start
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}
    assert supervisor["added"] == ["https://github.com/janlenck/braincall"]
    assert supervisor["app"].calls == [
        "install",
        (
            "options",
            {
                "anthropic_api_key": KEY,
                "callers": [{"ha_user": "Maryna", "name": "Maryna", "access": "admin"}],
                "language": "de",
            },
        ),
        "start",
    ]


async def test_install_without_choosing_an_administrator(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "install_app_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"anthropic_api_key": KEY})
    result = await run_progress(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert supervisor["app"].options["callers"] == [] and supervisor["added"] == []


async def test_existing_callers_stay(hass: HomeAssistant, brain_call, supervisor) -> None:
    MockUser(name="Jan", is_owner=True).add_to_hass(hass)
    supervisor["slug"] = "98055a04_braincall"
    jan = {"number": "0151", "ha_user": "Jan", "name": "Jan", "access": "admin"}
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": "", "callers": [jan]})
    result = await choose_app(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"anthropic_api_key": KEY, "admin": "Jan"}
    )
    await run_progress(hass, result)
    assert supervisor["app"].options["callers"] == [jan]


async def test_install_fails(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"].fail = "install"
    result = await choose_app(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"anthropic_api_key": KEY})
    result = await run_progress(hass, result)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "app_install_failed"


async def test_start_fails(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": KEY})
    supervisor["app"].fail = "start"
    result = await run_progress(hass, await choose_app(hass))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "app_start_failed"


async def test_an_app_that_never_answers(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": KEY})
    brain_call.info = BrainCallConnectionError("still starting")
    result = await run_progress(hass, await choose_app(hass))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "app_start_failed"


async def test_an_app_with_another_api_version(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.NOT_RUNNING, {"anthropic_api_key": KEY})
    brain_call.info = ServerInfo(api=0, version="1.10.1", assistant="", phone=True, home_assistant=True)
    result = await run_progress(hass, await choose_app(hass))
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "too_old"


@pytest.mark.parametrize("state", [AddonState.INSTALLING, AddonState.UPDATING])
async def test_an_app_that_is_busy(hass: HomeAssistant, brain_call, supervisor, state) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(state)
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "app_busy"


async def test_the_app_store_cannot_be_read(hass: HomeAssistant, brain_call, supervisor) -> None:
    supervisor["slug"] = SupervisorError("down")
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "app_info_failed"


async def test_the_app_is_already_set_up(hass: HomeAssistant, brain_call, supervisor, entry: MockConfigEntry) -> None:
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.RUNNING, {"anthropic_api_key": KEY})
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_the_discovery_flow_ends_when_the_app_flow_creates_the_entry(
    hass: HomeAssistant, brain_call, supervisor
) -> None:
    """Started from the flow, the app announces itself: that second flow must not stay behind."""
    supervisor["slug"] = "98055a04_braincall"
    supervisor["app"] = FakeApp(AddonState.RUNNING, {"anthropic_api_key": KEY})
    await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery())
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1
    result = await choose_app(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


__all__ = ["INFO"]
