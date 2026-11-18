"""What a Brain Call server says, kept up to date: its planned calls for the to-do list, and its status and line.

Brain Call fires braincall_task_updated on every status change and braincall_task_finished at the end; either
makes the list ask again at once. Brain Call without a connection to Home Assistant fires nothing, so the list
also asks every minute. The line works the same way with braincall_state and every 30 seconds.
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BrainCallClient, BrainCallError
from .const import (
    DOMAIN,
    EVENT_STATE,
    EVENT_TASK_FINISHED,
    EVENT_TASK_UPDATED,
    LINE_STATES,
    LOGGER,
    SIP_REGISTERED,
    SIP_REGISTERING,
)

UPDATE_INTERVAL = timedelta(minutes=1)


class TasksCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """GET /api/tasks, after every event about a task and once a minute."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BrainCallClient) -> None:
        """Ask the server of this entry."""
        super().__init__(hass, LOGGER, config_entry=entry, name=f"{DOMAIN} tasks", update_interval=UPDATE_INTERVAL)
        self.client = client

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            return await self.client.async_tasks()
        except BrainCallError as err:
            raise UpdateFailed(str(err)) from err

    @callback
    def async_listen_events(self) -> None:
        """Ask again whenever Brain Call says a task changed; stops with the config entry."""

        @callback
        def changed(event: Event) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        assert self.config_entry is not None
        for name in (EVENT_TASK_UPDATED, EVENT_TASK_FINISHED):
            self.config_entry.async_on_unload(self.hass.bus.async_listen(name, changed))


STATUS_INTERVAL = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class Status:
    """What Brain Call says about itself and its line."""

    answered: bool  # /health answered at all
    healthy: bool = False  # ... with status ok
    problems: tuple[str, ...] = ()
    registered: bool | None = None  # at the FRITZ!Box
    line: str | None = None  # one of LINE_STATES
    call: dict[str, Any] | None = None  # number, caller_name, direction, since; only with the line API


class StatusCoordinator(DataUpdateCoordinator[Status]):
    """GET /health (and /api/status with Brain Call 1.19.0) every 30 s, and the event braincall_state at once.

    Brain Call that cannot be reached is no failed update: it is the state "not connected", the other entities
    become unavailable. Brain Call older than 1.19.0 has no line: `busy` is read as in_call or idle.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BrainCallClient, line_api: bool) -> None:
        """Ask the server of this entry."""
        super().__init__(hass, LOGGER, config_entry=entry, name=f"{DOMAIN} status", update_interval=STATUS_INTERVAL)
        self.client = client
        self.line_api = line_api
        self._pushed = 0  # braincall_state events taken over; a poll that started before one keeps its line

    def _registered(self, sip: Any) -> bool:
        """REGISTERING right after registered is pyVoIP registering again (every 2 minutes): still registered."""
        if sip == SIP_REGISTERING and self.data is not None and self.data.registered:
            return True
        return sip == SIP_REGISTERED

    async def _async_update_data(self) -> Status:
        pushed = self._pushed
        try:
            health = await self.client.async_health()
        except BrainCallError as err:
            LOGGER.debug("Brain Call status: %s", err)
            return Status(answered=False)
        status = None
        if self.line_api:
            try:
                status = await self.client.async_status()
            except BrainCallError as err:  # e.g. a new token: the line from /health, without the call
                LOGGER.debug("Brain Call line: %s", err)
        line = (status or health).get("line")
        if line not in LINE_STATES:
            line = "in_call" if health.get("busy") else "idle"
        call = (status or {}).get("call")
        polled = Status(
            answered=True,
            healthy=health.get("status") == "ok",
            problems=tuple(str(p) for p in health.get("problems") or ()),
            registered=self._registered((status or health).get("sip")),
            line=line,
            call=call if isinstance(call, dict) and line != "idle" else None,
        )
        if self._pushed != pushed and self.data is not None and self.data.answered:
            # an event came while the poll waited for its answers: it is newer than what the poll read
            return replace(polled, registered=self.data.registered, line=self.data.line, call=self.data.call)
        return polled

    @callback
    def async_listen_events(self) -> None:
        """Take over braincall_state at once; stops with the config entry.

        The event does not say which Brain Call sent it: with more than one set up (also a disabled or failed
        entry, its Brain Call may still fire it), each asks its own. The event says nothing about health: after a
        failed poll it asks /health again instead of taking the event over."""

        @callback
        def changed(event: Event) -> None:
            data = event.data
            if (
                self.data is None
                or not self.data.answered
                or data.get("line") not in LINE_STATES
                or len(self.hass.config_entries.async_entries(DOMAIN)) > 1
            ):
                self.hass.async_create_task(self.async_request_refresh())
                return
            self._pushed += 1
            call = data.get("call")
            self.async_set_updated_data(
                replace(
                    self.data,
                    registered=self._registered(data.get("sip")),
                    line=data["line"],
                    call=call if isinstance(call, dict) and data["line"] != "idle" else None,
                )
            )

        assert self.config_entry is not None
        self.config_entry.async_on_unload(self.hass.bus.async_listen(EVENT_STATE, changed))
