"""The owner's messages (Brain Call 1.20.0): the to-do list Messages and the sensors, ticked off in both places."""

from dataclasses import replace

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.brainassist.api import BrainCallConnectionError
from custom_components.brainassist.coordinator import UPDATE_INTERVAL

from .conftest import INFO

LIST = "todo.brain_assist_messages"
OPEN = "sensor.brain_assist_open_messages"
LAST = "sensor.brain_assist_last_message"
MESSAGES = [  # newest first, as Brain Call sends them
    {
        "id": "c3",
        "time": "2026-11-19T18:54:27+00:00",
        "number": "04541999",
        "kind": "message",
        "name": "Erika Muster",
        "summary": "Erika Muster wartete auf einen Termin und bittet um Rückruf.",
        "callback": True,
        "when": "Sonntag oder Montag",
        "note": "saved as a new contact",
        "duration_s": 42,
    },
    {
        "id": "b2",
        "time": "2026-11-19T12:00:00+00:00",
        "number": "0170222",
        "kind": "missed",
        "name": "Max",
        "summary": "missed call: the line was busy, no message",
        "callback": False,
    },
    {
        "id": "a1",
        "time": "2026-11-18T09:00:00+00:00",
        "number": "0170333",
        "kind": "message",
        "name": "Moritz",
        "summary": "Wollte nur Bescheid sagen.",
        "callback": False,
        "heard": "2026-11-18T10:00:00+00:00",
    },
]


@pytest.fixture
async def with_messages(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> MockConfigEntry:
    """Brain Call 1.20.0 with three messages: a call back, a missed call, and one already heard."""
    brain_call.info = replace(INFO, version="1.20.0", line=True, messages=True)
    brain_call.messages = [dict(m) for m in MESSAGES]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def items(hass: HomeAssistant) -> list[dict]:
    result = await hass.services.async_call(
        TODO_DOMAIN, "get_items", {"entity_id": LIST}, blocking=True, return_response=True
    )
    return result[LIST]["items"]


async def test_no_messages_from_an_older_brain_call(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert hass.states.get(LIST) is None and hass.states.get(OPEN) is None and hass.states.get(LAST) is None
    assert loaded.runtime_data.messages is None


async def test_the_list_shows_what_is_open_first(hass: HomeAssistant, with_messages) -> None:
    state = hass.states.get(LIST)
    assert state.name == "Brain Assist Messages" and state.state == "2"
    listed = await items(hass)
    assert [i["uid"] for i in listed] == ["c3", "b2", "a1"]
    assert listed[0]["summary"] == "Erika Muster: call back" and listed[0]["status"] == "needs_action"
    description = listed[0]["description"]
    assert description.startswith("2026-11-19 18:54 04541999 (42 s)")
    assert "bittet um Rückruf." in description and "Call back wanted: Sonntag oder Montag" in description
    assert "saved as a new contact" in description
    assert listed[1]["summary"] == "Max: missed call" and "(missed: the line was busy)" in listed[1]["description"]
    assert listed[2]["summary"] == "Moritz" and listed[2]["status"] == "completed"


async def test_the_sensors_count_and_show_the_latest(hass: HomeAssistant, with_messages) -> None:
    open_ = hass.states.get(OPEN)
    assert open_.state == "2" and open_.attributes["new"] == 2 and open_.attributes["callbacks"] == 1
    assert open_.name == "Brain Assist Open messages"
    last = hass.states.get(LAST)
    assert last.state == "2026-11-19T18:54:27+00:00" and last.attributes["device_class"] == "timestamp"
    assert last.attributes["name"] == "Erika Muster" and last.attributes["callback"] is True
    assert last.attributes["when"] == "Sonntag oder Montag" and last.attributes["kind"] == "message"


async def test_ticking_a_call_back_off_marks_it_done_in_brain_call(
    hass: HomeAssistant, with_messages, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    await hass.services.async_call(
        TODO_DOMAIN, "update_item", {"entity_id": LIST, "item": "c3", "status": "completed"}, blocking=True
    )
    await hass.async_block_till_done()
    assert brain_call.message_changes == [("c3", {"heard": True, "done": True})]
    assert hass.states.get(LIST).state == "1" and hass.states.get(OPEN).attributes["callbacks"] == 0
    await hass.services.async_call(
        TODO_DOMAIN, "update_item", {"entity_id": LIST, "item": "c3", "status": "needs_action"}, blocking=True
    )
    await hass.async_block_till_done()
    assert brain_call.message_changes[-1] == ("c3", {"heard": False, "done": False})
    freezer.tick(11)  # the refresh after a change is debounced
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(LIST).state == "2"


async def test_deleting_removes_the_message_in_brain_call(hass: HomeAssistant, with_messages, brain_call) -> None:
    await hass.services.async_call(TODO_DOMAIN, "remove_item", {"entity_id": LIST, "item": ["b2"]}, blocking=True)
    await hass.async_block_till_done()
    assert brain_call.deleted_messages == ["b2"]
    assert [i["uid"] for i in await items(hass)] == ["c3", "a1"]


async def test_a_failed_change_says_why(hass: HomeAssistant, with_messages, brain_call) -> None:
    brain_call.messages = BrainCallConnectionError("refused")
    with pytest.raises(HomeAssistantError, match="cannot be reached: refused"):
        await hass.services.async_call(
            TODO_DOMAIN, "update_item", {"entity_id": LIST, "item": "c3", "status": "completed"}, blocking=True
        )


async def test_the_event_brings_a_new_message_at_once(hass: HomeAssistant, with_messages, brain_call) -> None:
    brain_call.messages.insert(0, {"id": "d4", "time": "2026-11-19T19:30:00+00:00", "number": "0151", "callback": True})
    hass.bus.async_fire("braincall_messages_updated", {"open": 3, "new": 3, "callbacks": 2})
    await hass.async_block_till_done()
    assert hass.states.get(OPEN).state == "3"
    assert hass.states.get(LAST).attributes["number"] == "0151"
    assert (await items(hass))[0]["summary"] == "0151: call back"


async def test_the_list_asks_again_every_minute(
    hass: HomeAssistant, with_messages, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    brain_call.messages = []
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(LIST).state == "0" and hass.states.get(OPEN).state == "0"
    assert hass.states.get(LAST).state == "unknown"


async def test_unreadable_messages_make_the_entities_unavailable(
    hass: HomeAssistant, with_messages, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    brain_call.messages = BrainCallConnectionError("refused")
    freezer.tick(UPDATE_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(LIST).state == "unavailable" and hass.states.get(OPEN).state == "unavailable"


async def test_the_entities_belong_to_the_device(hass: HomeAssistant, with_messages) -> None:
    entities = {
        e.entity_id: e.unique_id for e in er.async_entries_for_config_entry(er.async_get(hass), with_messages.entry_id)
    }
    assert entities[LIST] == f"{with_messages.entry_id}_messages"
    assert entities[OPEN] == f"{with_messages.entry_id}_open_messages"
    assert entities[LAST] == f"{with_messages.entry_id}_last_message"
