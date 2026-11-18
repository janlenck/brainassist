"""Brain Assist: Brain Call as the conversation agent of Home Assistant Assist.

Brain Call is the server (the brain, its tools, access rules and memory). This integration is the thin client
inside Home Assistant: it provides the conversation agents and forwards each request to Brain Call. With planned
calls on in Brain Call it also has the action brainassist.call and a to-do list of the planned calls. Entities show
whether Brain Call answers and, with a phone, whether it is registered and what the line is doing.
"""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BrainCallAuthError, BrainCallClient, BrainCallError, ServerInfo
from .const import API_VERSION, DOMAIN, ISSUE_INCOMPATIBLE
from .coordinator import StatusCoordinator, TasksCoordinator
from .services import async_setup_services

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CONVERSATION, Platform.SENSOR, Platform.TODO]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class BrainAssistData:
    """What a config entry keeps while it is loaded."""

    client: BrainCallClient
    info: ServerInfo
    tasks: TasksCoordinator | None = None  # planned calls, if they are on in Brain Call
    status: StatusCoordinator | None = None  # reachable, phone registered, line


type BrainAssistConfigEntry = ConfigEntry[BrainAssistData]


def device_info(entry: BrainAssistConfigEntry) -> dr.DeviceInfo:
    """The Brain Assist device of an entry, the same for every entity: whichever platform is set up first makes it."""
    info = entry.runtime_data.info
    return dr.DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Brain Assist",
        manufacturer="Brain Call",
        model=info.assistant or None,
        sw_version=info.version or None,
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url=entry.runtime_data.client.url,
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The action brainassist.call, for every config entry."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BrainAssistConfigEntry) -> bool:
    """Connect to Brain Call and set up the conversation agents."""
    client = BrainCallClient(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    try:
        info = await client.async_version()
    except BrainCallAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except BrainCallError as err:
        raise ConfigEntryNotReady(str(err)) from err

    issue_id = f"{ISSUE_INCOMPATIBLE}_{entry.entry_id}"
    if info.api != API_VERSION:
        # Brain Call and Brain Assist are updated separately: say which one is behind, and try again later
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="brain_call_too_old" if info.api < API_VERSION else "brain_assist_too_old",
            translation_placeholders={
                "version": info.version or "?",
                "server_api": str(info.api),
                "client_api": str(API_VERSION),
            },
        )
        raise ConfigEntryNotReady(
            f"Brain Call {info.version} speaks API {info.api}, Brain Assist speaks API {API_VERSION}"
        )
    ir.async_delete_issue(hass, DOMAIN, issue_id)

    tasks = None
    if info.tasks:
        tasks = TasksCoordinator(hass, entry, client)
        # a list that cannot be read now is no reason to hold back the agents: it is asked again in a minute
        await tasks.async_refresh()
        tasks.async_listen_events()
    status = StatusCoordinator(hass, entry, client, line_api=info.line)
    await status.async_refresh()  # never fails: Brain Call not answering is a state of its own
    status.async_listen_events()
    entry.runtime_data = BrainAssistData(client=client, info=info, tasks=tasks, status=status)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BrainAssistConfigEntry) -> bool:
    """Unload the conversation agents and the to-do list."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BrainAssistConfigEntry) -> None:
    """Remove the repair issue of an entry that is deleted."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_INCOMPATIBLE}_{entry.entry_id}")
