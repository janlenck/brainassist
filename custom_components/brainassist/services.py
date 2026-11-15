"""The action brainassist.call: Brain Call calls someone with a task, from an automation, a script or by hand.

Only administrators of Home Assistant may run it, and automations and scripts. The requester is the Home Assistant
user who runs the action (Brain Call finds their entry in Callers by id or name);
an automation has no user, then it is the owner. The answer is the planned task (with its id), the result follows
as event braincall_task_finished and in the to-do list.
"""

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import BrainCallError, BrainCallRefusedError
from .const import DOMAIN, SERVICE_CALL

ATTR_CONFIG_ENTRY = "config_entry_id"
ATTR_NUMBER = "number"
ATTR_NAME = "name"
ATTR_TASK = "task"
ATTR_NOT_BEFORE = "not_before"
ATTR_NOT_AFTER = "not_after"
ATTR_RETRIES = "retries"
ATTR_LANGUAGE = "language"
ATTR_CALL_BACK = "call_back"
ATTR_LEAVE_MESSAGE = "leave_message"
LANGUAGES = ("de", "en", "uk", "auto")

CALL_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
            vol.Optional(ATTR_NUMBER): cv.string,
            vol.Optional(ATTR_NAME): cv.string,
            vol.Required(ATTR_TASK): vol.All(cv.string, vol.Length(min=1, max=2000)),
            vol.Optional(ATTR_NOT_BEFORE): cv.datetime,
            vol.Optional(ATTR_NOT_AFTER): cv.datetime,
            vol.Optional(ATTR_RETRIES): vol.All(vol.Coerce(int), vol.Range(min=0, max=10)),
            vol.Optional(ATTR_LANGUAGE): vol.In(LANGUAGES),
            vol.Optional(ATTR_CALL_BACK): cv.boolean,
            vol.Optional(ATTR_LEAVE_MESSAGE): cv.boolean,
        }
    ),
    cv.has_at_least_one_key(ATTR_NUMBER, ATTR_NAME),
)


def _local(value: datetime) -> str:
    """A time as Brain Call reads it: to the minute, with its UTC offset, so it means the same instant wherever
    Brain Call runs and also in the hour that repeats at the end of daylight saving time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(value).isoformat(timespec="minutes")


def _entry(hass: HomeAssistant, entry_id: str | None):
    """The loaded entry to ask: the one named, or the only one with planned calls. With several the name of a
    person could mean someone else on each server, so one of them must be named."""
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    with_tasks = [e for e in entries if e.runtime_data.tasks is not None]
    if not with_tasks:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tasks_off")
    if len(with_tasks) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="which_server")
    return with_tasks[0]


async def _async_call(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    # A planned call speaks for the owner and costs money: only administrators of Home Assistant, and automations
    # and scripts (no user; whoever may change those is an administrator anyway). Brain Call trusts the API token,
    # so this is where the line is drawn.
    user = await hass.auth.async_get_user(call.context.user_id) if call.context.user_id else None
    if call.context.user_id and (user is None or not user.is_admin):
        raise Unauthorized(context=call.context)
    entry = _entry(hass, call.data.get(ATTR_CONFIG_ENTRY))
    body: dict[str, Any] = {"task": call.data[ATTR_TASK].strip()}
    for key in (ATTR_NUMBER, ATTR_NAME):
        if str(call.data.get(key) or "").strip():
            body[key] = call.data[key].strip()
    for key in (ATTR_NOT_BEFORE, ATTR_NOT_AFTER):
        if key in call.data:
            body[key] = _local(call.data[key])
    for key in (ATTR_RETRIES, ATTR_LANGUAGE, ATTR_CALL_BACK, ATTR_LEAVE_MESSAGE):
        if key in call.data:
            body[key] = call.data[key]
    if user is not None:
        body["requester_user_id"] = user.id
        body["requester_user_name"] = user.name or ""
    try:
        task = await entry.runtime_data.client.async_plan_task(body)
    except BrainCallRefusedError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="call_refused", translation_placeholders={"reason": err.reason}
        ) from err
    except BrainCallError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="cannot_connect", translation_placeholders={"error": str(err)}
        ) from err
    if entry.runtime_data.tasks is not None:
        await entry.runtime_data.tasks.async_request_refresh()
    return task


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register brainassist.call once, for all entries."""
    hass.services.async_register(
        DOMAIN, SERVICE_CALL, _async_call, schema=CALL_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
