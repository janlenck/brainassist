"""What Brain Call's line is doing: idle, ringing, dialing or in a call, with the call as attributes; and, with
Brain Call 1.20.0, how many messages are open for the owner and the latest one."""

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BrainAssistConfigEntry, device_info
from .const import LINE_STATES
from .coordinator import MessagesCoordinator, StatusCoordinator

CALL_ATTRIBUTES = ("number", "caller_name", "direction", "since")


async def async_setup_entry(
    hass: HomeAssistant, entry: BrainAssistConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """The line, for a Brain Call with a phone; the messages, if Brain Call has them."""
    status = entry.runtime_data.status
    assert status is not None
    entities: list[SensorEntity] = []
    if entry.runtime_data.info.phone:
        entities.append(BrainCallLine(entry, status))
    messages = entry.runtime_data.messages
    if messages is not None:
        entities += [BrainCallOpenMessages(entry, messages), BrainCallLastMessage(entry, messages)]
    async_add_entities(entities)


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


MESSAGE_ATTRIBUTES = ("name", "number", "summary", "callback", "when", "kind")


class _MessagesSensor(CoordinatorEntity[MessagesCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: BrainAssistConfigEntry, coordinator: MessagesCoordinator) -> None:
        """Belongs to the Brain Assist device of the entry."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{self._attr_translation_key}"
        self._attr_device_info = device_info(entry)


class BrainCallOpenMessages(_MessagesSensor):
    """How many messages are open: not heard yet, or a call back not done. new and callbacks as attributes."""

    _attr_translation_key = "open_messages"

    @property
    def native_value(self) -> int:
        """The open messages."""
        return self.coordinator.data["open"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """new: not heard yet; callbacks: call backs not done."""
        return {"new": self.coordinator.data["new"], "callbacks": self.coordinator.data["callbacks"]}


class BrainCallLastMessage(_MessagesSensor):
    """When the latest call for the owner ended; who it was and what they wanted as attributes."""

    _attr_translation_key = "last_message"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def _latest(self) -> dict[str, Any] | None:
        messages = self.coordinator.data["messages"]
        return messages[0] if messages else None

    @property
    def native_value(self) -> datetime | None:
        """The end of the latest call (none while there is no message)."""
        latest = self._latest()
        try:
            when = datetime.fromisoformat(str(latest["time"])) if latest else None
        except ValueError:
            return None
        return when if when is not None and when.tzinfo is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """name, number, summary, callback, when (a call back suits them), kind (message or missed)."""
        latest = self._latest()
        return {k: latest.get(k) for k in MESSAGE_ATTRIBUTES} if latest else None
