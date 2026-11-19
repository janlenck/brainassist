"""Two to-do lists of Brain Call: the planned calls, and the owner's messages.

Planned calls: open ones first, then the finished ones of the last 30 days. A task may take several calls (Brain
Call 1.16.0): its steps are listed in the description, and a task waiting for a call back is open until the end of
its time window. Deleting an item cancels a planned or waiting task. A call that is running cannot be cancelled, and
a finished one stays in the list until Brain Call drops it after 30 days.

Messages (Brain Call 1.20.0): the calls Brain Call took for the owner. Open is what the owner has not heard yet and
every call back that is not done; ticking an item off marks it done in Brain Call, so Claudine stops mentioning it
on the phone, and unticking opens it again. Deleting removes it from Brain Call.
"""

from datetime import datetime
from typing import Any

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BrainAssistConfigEntry, device_info
from .api import BrainCallError, BrainCallRefusedError
from .const import DOMAIN, OPEN_STATUSES
from .coordinator import MessagesCoordinator, TasksCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: BrainAssistConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """The planned calls, if they are on in Brain Call; the messages, if Brain Call has them."""
    entities: list[TodoListEntity] = []
    if entry.runtime_data.tasks is not None:
        entities.append(BrainCallTasks(entry, entry.runtime_data.tasks))
    if entry.runtime_data.messages is not None:
        entities.append(BrainCallMessages(entry, entry.runtime_data.messages))
    async_add_entities(entities)


def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _step(step: dict[str, Any]) -> str:
    """One step of a task's history in a line: when, which way, what came of it."""
    when = str(step.get("time") or "")[:16].replace("T", " ")
    if step.get("kind") == "requester":
        return f"{when} {step.get('by') or 'Requester'} added: {step.get('text', '')}"
    way = "Called back from" if step.get("kind") == "call_in" else "Called"
    line = f"{when} {way} {step.get('number', '')}: {step.get('outcome', '')}"
    return line + (f" – {step['result']}" if step.get("result") else "")


def _description(task: dict[str, Any]) -> str:
    """Status, who asked, the steps so far and the result in a few lines."""
    lines = [f"Status: {task.get('status', '?')}"]
    if task.get("status") == "waiting" and task.get("not_after"):
        lines[0] += f" (for a call back until {str(task['not_after'])[:16].replace('T', ' ')})"
    requester = (task.get("requester") or {}).get("name")
    if requester:
        lines.append(f"Requested by: {requester}")
    lines.append(f"Number: {task.get('number', '')}")
    steps = [s for s in task.get("steps") or [] if isinstance(s, dict)]
    if steps:
        lines.append("Steps:")
        lines += [f"{i}. {_step(s)}" for i, s in enumerate(steps, 1)]
    result = task.get("result") or {}
    if result.get("summary"):
        lines.append(str(result["summary"]))
    answers = result.get("answers") or {}
    if answers:
        lines.append("Answers: " + "; ".join(f"{k}: {v}" for k, v in answers.items()))
    if result.get("open_points"):
        lines.append("Open: " + "; ".join(str(p) for p in result["open_points"]))
    return "\n".join(lines)


def _due(task: dict[str, Any]) -> datetime | None:
    """A planned task: its next call; a waiting one: the end of the wait for a call back."""
    if task.get("status") == "planned":
        return _time(task.get("next_try"))
    if task.get("status") == "waiting":
        return _time(task.get("not_after"))
    return None


def item(task: dict[str, Any]) -> TodoItem:
    """One planned call as a to-do item: whom, what for, and until it is over when it is due."""
    open_ = task.get("status") in OPEN_STATUSES
    return TodoItem(
        uid=str(task["id"]),
        summary=f"{task.get('name') or task.get('number') or '?'}: {task.get('goal') or task.get('task') or ''}",
        status=TodoItemStatus.NEEDS_ACTION if open_ else TodoItemStatus.COMPLETED,
        due=_due(task),
        description=_description(task),
    )


def _order(task: dict[str, Any]) -> tuple:
    """Open tasks first, the next one on top (by the instant, whatever the UTC offset; without a time first);
    then the finished ones, the latest on top."""
    if task.get("status") in OPEN_STATUSES:
        due = _time(task.get("not_after") if task.get("status") == "waiting" else task.get("next_try"))
        return (0, due.timestamp() if due and due.tzinfo else float("-inf"))
    finished = _time(task.get("finished") or task.get("created"))
    return (1, -(finished.timestamp() if finished and finished.tzinfo else 0))


class BrainCallTasks(CoordinatorEntity[TasksCoordinator], TodoListEntity):
    """The planned calls of one Brain Call server."""

    _attr_has_entity_name = True
    _attr_translation_key = "tasks"
    _attr_supported_features = TodoListEntityFeature.DELETE_TODO_ITEM

    def __init__(self, entry: BrainAssistConfigEntry, coordinator: TasksCoordinator) -> None:
        """Belongs to the Brain Assist device of the entry."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tasks"
        self._attr_device_info = device_info(entry)

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """The planned calls, open ones first."""
        if self.coordinator.data is None:
            return None
        return [item(t) for t in sorted(self.coordinator.data, key=_order)]

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Cancel planned or waiting calls. Running or finished ones cannot be taken back."""
        by_id = {str(t["id"]): t for t in self.coordinator.data or []}
        for uid in uids:
            status = (by_id.get(uid) or {}).get("status")
            if status == "running":
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="task_running")
            if status is not None and status not in OPEN_STATUSES:
                raise HomeAssistantError(translation_domain=DOMAIN, translation_key="task_finished")
        try:
            for uid in uids:
                await self.coordinator.client.async_cancel_task(uid)
        except BrainCallRefusedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cancel_refused",
                translation_placeholders={"reason": err.reason},
            ) from err
        except BrainCallError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()


def message_open(message: dict[str, Any]) -> bool:
    """Something for the owner: not heard yet, or a call back that is not done (as Brain Call counts it)."""
    return not message.get("done") and (not message.get("heard") or bool(message.get("callback")))


def _message_description(message: dict[str, Any]) -> str:
    """When, who, what they want, the call back and what Brain Call did about the caller."""
    when = str(message.get("time") or "")[:16].replace("T", " ")
    lines = [f"{when} {message.get('number') or 'number withheld'}"]
    if message.get("kind") == "missed":
        lines[0] += " (missed: the line was busy)"
    elif message.get("duration_s") is not None:
        lines[0] += f" ({message['duration_s']} s)"
    if message.get("summary"):
        lines.append(str(message["summary"]))
    if message.get("callback"):
        lines.append("Call back wanted" + (f": {message['when']}" if message.get("when") else ""))
    if message.get("note"):
        lines.append(str(message["note"]))
    return "\n".join(lines)


def message_item(message: dict[str, Any]) -> TodoItem:
    """One message as a to-do item: who called and, for a call back, that one is wanted."""
    who = message.get("name") or message.get("number") or "?"
    what = "call back" if message.get("callback") else ("missed call" if message.get("kind") == "missed" else "")
    return TodoItem(
        uid=str(message["id"]),
        summary=f"{who}: {what}" if what else str(who),
        status=TodoItemStatus.NEEDS_ACTION if message_open(message) else TodoItemStatus.COMPLETED,
        description=_message_description(message),
    )


class BrainCallMessages(CoordinatorEntity[MessagesCoordinator], TodoListEntity):
    """The calls Brain Call took for the owner: open ones first, the newest on top."""

    _attr_has_entity_name = True
    _attr_translation_key = "messages"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM | TodoListEntityFeature.DELETE_TODO_ITEM

    def __init__(self, entry: BrainAssistConfigEntry, coordinator: MessagesCoordinator) -> None:
        """Belongs to the Brain Assist device of the entry."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_messages"
        self._attr_device_info = device_info(entry)

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Open messages first, then the others; newest first in each (Brain Call sends them newest first)."""
        if self.coordinator.data is None:
            return None
        messages = self.coordinator.data["messages"]
        return [message_item(m) for m in sorted(messages, key=lambda m: not message_open(m))]

    async def _change(self, call) -> None:
        try:
            await call
        except BrainCallError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        finally:
            await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticked off: done (and heard). Unticked: open again, as if it was new."""
        assert item.uid is not None
        client = self.coordinator.client
        if item.status == TodoItemStatus.COMPLETED:
            await self._change(client.async_update_message(item.uid, heard=True, done=True))
        else:
            await self._change(client.async_update_message(item.uid, heard=False, done=False))

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Remove messages from Brain Call."""

        async def delete() -> None:
            for uid in uids:
                await self.coordinator.client.async_delete_message(uid)

        await self._change(delete())
