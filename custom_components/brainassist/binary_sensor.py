"""Whether Brain Call answers, and whether its phone is registered at the FRITZ!Box."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BrainAssistConfigEntry, device_info
from .coordinator import StatusCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: BrainAssistConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Connection always; Phone only for a Brain Call with a phone."""
    status = entry.runtime_data.status
    assert status is not None
    entities: list[BinarySensorEntity] = [BrainCallConnection(entry, status)]
    if entry.runtime_data.info.phone:
        entities.append(BrainCallPhone(entry, status))
    async_add_entities(entities)


class _StatusEntity(CoordinatorEntity[StatusCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, entry: BrainAssistConfigEntry, coordinator: StatusCoordinator) -> None:
        """Belongs to the Brain Assist device of the entry."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{self._attr_translation_key}"
        self._attr_device_info = device_info(entry)


class BrainCallConnection(_StatusEntity):
    """On while Brain Call answers /health and is healthy; the problems while it is not."""

    _attr_translation_key = "connection"

    @property
    def is_on(self) -> bool:
        """Brain Call answers and says it is ok."""
        return self.coordinator.data.healthy

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Why Brain Call is unhealthy, e.g. its SIP watchdog hangs."""
        problems = self.coordinator.data.problems
        return {"problems": list(problems)} if problems else None


class BrainCallPhone(_StatusEntity):
    """On while Brain Call's phone is registered at the FRITZ!Box; unavailable while Brain Call does not answer."""

    _attr_translation_key = "phone"

    @property
    def available(self) -> bool:
        """Only Brain Call itself knows."""
        return super().available and self.coordinator.data.answered

    @property
    def is_on(self) -> bool | None:
        """Registered at the FRITZ!Box."""
        return self.coordinator.data.registered
