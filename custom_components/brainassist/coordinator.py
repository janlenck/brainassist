"""The planned calls of a Brain Call server, kept up to date for the to-do list.

Brain Call fires braincall_task_updated on every status change and braincall_task_finished at the end; either
makes the list ask again at once. Brain Call without a connection to Home Assistant fires nothing, so the list
also asks every minute.
"""

from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BrainCallClient, BrainCallError
from .const import DOMAIN, EVENT_TASK_FINISHED, EVENT_TASK_UPDATED, LOGGER

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
