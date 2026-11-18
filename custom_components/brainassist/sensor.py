"""What Brain Call's line is doing: idle, ringing, dialing or in a call, with the call as attributes."""

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BrainAssistConfigEntry, device_info
from .const import LINE_STATES
from .coordinator import StatusCoordinator

CALL_ATTRIBUTES = ("number", "caller_name", "direction", "since")


async def async_setup_entry(
    hass: HomeAssistant, entry: BrainAssistConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """The line, for a Brain Call with a phone."""
    status = entry.runtime_data.status
    assert status is not None
    if entry.runtime_data.info.phone:
        async_add_entities([BrainCallLine(entry, status)])


class BrainCallLine(CoordinatorEntity[StatusCoordinator], SensorEntity):
    """The line. Brain Call older than 1.19.0 only says busy: in a call or idle, without the call."""

    _attr_has_entity_name = True
    _attr_translation_key = "line"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(LINE_STATES)

    def __init__(self, entry: BrainAssistConfigEntry, coordinator: StatusCoordinator) -> None:
        """Belongs to the Brain Assist device of the entry."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_line"
        self._attr_device_info = device_info(entry)

    @property
    def available(self) -> bool:
        """Only while Brain Call answers."""
        return super().available and self.coordinator.data.answered

    @property
    def native_value(self) -> str | None:
        """idle, ringing, dialing or in_call."""
        return self.coordinator.data.line

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The current call: number, caller_name, direction (in/out), since; none while idle."""
        call = self.coordinator.data.call
        return {k: call.get(k) for k in CALL_ATTRIBUTES} if call else None
