"""Brain Assist: Brain Call as the conversation agent of Home Assistant Assist.

Brain Call is the server (the brain, its tools, access rules and memory). This integration is the thin client
inside Home Assistant: it provides the conversation agents and forwards each request to Brain Call. With planned
calls on in Brain Call it also has the action brainassist.call and a to-do list of the planned calls.
"""

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BrainCallAuthError, BrainCallClient, BrainCallError, ServerInfo
from .const import API_VERSION, DOMAIN, ISSUE_INCOMPATIBLE
from .coordinator import TasksCoordinator
from .services import async_setup_services

PLATFORMS = [Platform.CONVERSATION, Platform.TODO]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class BrainAssistData:
    """What a config entry keeps while it is loaded."""

    client: BrainCallClient
    info: ServerInfo
    tasks: TasksCoordinator | None = None  # planned calls, if they are on in Brain Call


type BrainAssistConfigEntry = ConfigEntry[BrainAssistData]


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
    entry.runtime_data = BrainAssistData(client=client, info=info, tasks=tasks)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BrainAssistConfigEntry) -> bool:
    """Unload the conversation agents and the to-do list."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BrainAssistConfigEntry) -> None:
    """Remove the repair issue of an entry that is deleted."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_INCOMPATIBLE}_{entry.entry_id}")
