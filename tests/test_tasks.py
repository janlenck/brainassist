"""Planned calls (Brain Call 1.14.0): the action brainassist.call and the to-do list of the tasks."""

from dataclasses import replace

from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser
import voluptuous as vol

from custom_components.brainassist.api import BrainCallConnectionError, BrainCallRefusedError
from custom_components.brainassist.const import DOMAIN

from .conftest import INFO

LIST = "todo.brain_assist_planned_calls"
TASKS = [
    {
        "id": "a1",
        "status": "done",
        "name": "Seeblick",
        "number": "045421234",
        "goal": "Tisch reservieren",
        "finished": "2026-11-14T19:00:00+01:00",
        "requester": {"name": "Jan"},
        "result": {"summary": "Tisch um 19 Uhr reserviert.", "answers": {"time": "19:00"}, "open_points": []},
    },
    {
        "id": "b2",
        "status": "planned",
        "name": "Mark",
        "number": "0170222",
        "goal": "Termin für Freitag",
        "next_try": "2026-11-16T18:00+01:00",
        "requester": {"name": "Jan"},
    },
    {
        "id": "c3",
        "status": "running",
        "name": "",
        "number": "0301234",
        "goal": "Frage nach der Rechnung",
        "next_try": "2026-11-16T10:00+01:00",
    },
    {
        "id": "d4",
        "status": "not_reached",
        "name": "Praxis",
        "number": "0401",
        "goal": "Termin",
        "finished": "2026-11-15T12:00:00+01:00",
        "result": {"summary": "Niemand erreicht."},
    },
]


@pytest.fixture
async def with_tasks(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> MockConfigEntry:
    """Brain Call with planned calls on, and four tasks."""
    brain_call.info = replace(INFO, tasks=True)
    brain_call.tasks = [dict(t) for t in TASKS]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def items(hass: HomeAssistant) -> list[dict]:
    result = await hass.services.async_call(
        TODO_DOMAIN, "get_items", {"entity_id": LIST}, blocking=True, return_response=True
    )
    return result[LIST]["items"]


# The to-do list


async def test_no_list_without_planned_calls(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    assert hass.states.get(LIST) is None


async def test_open_calls_first_then_the_latest_finished(hass: HomeAssistant, with_tasks) -> None:
    state = hass.states.get(LIST)
    assert state.name == "Brain Assist Planned calls" and state.state == "2"  # two open
    listed = await items(hass)
    assert [i["uid"] for i in listed] == ["c3", "b2", "d4", "a1"]
    assert listed[1]["summary"] == "Mark: Termin für Freitag" and listed[1]["status"] == "needs_action"
    assert listed[1]["due"] == "2026-11-16T18:00:00+01:00"
    assert listed[2]["summary"] == "Praxis: Termin" and listed[2]["status"] == "completed"
    assert listed[0]["summary"] == "0301234: Frage nach der Rechnung" and "due" not in listed[0]
    done = listed[3]["description"]
    assert "Status: done" in done and "Requested by: Jan" in done and "Tisch um 19 Uhr reserviert." in done
    assert "Answers: time: 19:00" in done


async def test_deleting_a_planned_call_cancels_it(hass: HomeAssistant, with_tasks, brain_call) -> None:
    await hass.services.async_call(TODO_DOMAIN, "remove_item", {"entity_id": LIST, "item": "b2"}, blocking=True)
    await hass.async_block_till_done()
    assert brain_call.cancelled == ["b2"]
    assert hass.states.get(LIST).state == "1"


@pytest.mark.parametrize(("uid", "key"), [("c3", "task_running"), ("a1", "task_finished")])
async def test_running_or_finished_calls_cannot_be_deleted(hass: HomeAssistant, with_tasks, brain_call, uid, key):
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(TODO_DOMAIN, "remove_item", {"entity_id": LIST, "item": uid}, blocking=True)
    assert err.value.translation_key == key and brain_call.cancelled == []


async def test_a_refused_cancel_says_why(hass: HomeAssistant, with_tasks, brain_call) -> None:
    brain_call.cancel_error = BrainCallRefusedError(409, "task is running")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(TODO_DOMAIN, "remove_item", {"entity_id": LIST, "item": "b2"}, blocking=True)
    assert err.value.translation_key == "cancel_refused"
    assert err.value.translation_placeholders == {"reason": "task is running"}


@pytest.mark.parametrize("event", ["braincall_task_updated", "braincall_task_finished"])
async def test_an_event_from_brain_call_updates_the_list(hass: HomeAssistant, with_tasks, brain_call, event) -> None:
    brain_call.tasks[1]["status"] = "running"
    hass.bus.async_fire(event, {"id": "b2", "status": "running"})
    await hass.async_block_till_done()
    listed = await items(hass)
    assert [i["uid"] for i in listed][:2] == ["c3", "b2"] and "due" not in listed[1]


async def test_a_list_that_cannot_be_read_leaves_the_agents_working(
    hass: HomeAssistant, entry: MockConfigEntry, brain_call
) -> None:
    brain_call.info = replace(INFO, tasks=True)
    brain_call.tasks = BrainCallConnectionError("down")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("conversation.brain_assist_text") is not None
    assert hass.states.get(LIST).state == "unavailable"


# The action brainassist.call


async def test_call_plans_a_task_for_the_user_who_runs_it(hass: HomeAssistant, with_tasks, brain_call) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    user = MockUser(name="Jan", is_owner=True).add_to_hass(hass)  # the owner is an administrator
    response = await hass.services.async_call(
        DOMAIN,
        "call",
        {
            "name": "Mark",
            "task": " Frag Mark nach Freitag ",
            "not_before": "2026-11-16 18:00:00",
            "retries": 1,
            "language": "de",
            "call_back": True,
        },
        blocking=True,
        return_response=True,
        context=Context(user_id=user.id),
    )
    assert brain_call.planned == [
        {
            "task": "Frag Mark nach Freitag",
            "name": "Mark",
            "not_before": "2026-11-16T18:00+01:00",
            "retries": 1,
            "language": "de",
            "call_back": True,
            "requester_user_id": user.id,
            "requester_user_name": "Jan",
        }
    ]
    assert response["id"] == "t1" and response["status"] == "planned"
    await hass.async_block_till_done()
    assert "t1" in [i["uid"] for i in await items(hass)]


async def test_call_is_only_for_administrators(hass: HomeAssistant, with_tasks, brain_call) -> None:
    """Brain Call trusts the token: Home Assistant draws the line. Automations (no user) may."""
    user = MockUser(name="Gast").add_to_hass(hass)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, "call", {"number": "0401", "task": "x"}, blocking=True, context=Context(user_id=user.id)
        )
    assert brain_call.planned == []


async def test_times_keep_their_utc_offset(hass: HomeAssistant, with_tasks, brain_call) -> None:
    """The hour that repeats at the end of daylight saving time, and a Brain Call in another time zone."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    await hass.services.async_call(
        DOMAIN,
        "call",
        {"number": "0401", "task": "x", "not_before": "2026-10-25T00:30:00+00:00", "not_after": "2026-10-25 12:00:00"},
        blocking=True,
    )
    assert brain_call.planned[0]["not_before"] == "2026-10-25T02:30+02:00"  # the first 02:30, summer time
    assert brain_call.planned[0]["not_after"] == "2026-10-25T12:00+01:00"  # local time without offset: winter time


async def test_with_two_servers_one_must_be_named(hass: HomeAssistant, with_tasks, brain_call) -> None:
    other = MockConfigEntry(
        domain=DOMAIN, unique_id="http://other:8099", data={"url": "http://other:8099", "token": "t"}
    )
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "call", {"name": "Mark", "task": "x"}, blocking=True)
    assert err.value.translation_key == "which_server" and brain_call.planned == []
    await hass.services.async_call(
        DOMAIN, "call", {"name": "Mark", "task": "x", "config_entry_id": other.entry_id}, blocking=True
    )
    assert len(brain_call.planned) == 1


async def test_open_calls_are_ordered_by_the_instant(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    brain_call.info = replace(INFO, tasks=True)
    brain_call.tasks = [
        {"id": "late", "status": "planned", "goal": "x", "number": "1", "next_try": "2026-11-16T18:00+00:00"},
        {"id": "early", "status": "planned", "goal": "x", "number": "2", "next_try": "2026-11-16T19:00+02:00"},
        {"id": "now", "status": "running", "goal": "x", "number": "3"},
    ]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert [i["uid"] for i in await items(hass)] == ["now", "early", "late"]


async def test_call_from_an_automation_has_no_requester(hass: HomeAssistant, with_tasks, brain_call) -> None:
    await hass.services.async_call(DOMAIN, "call", {"number": "04542 1234", "task": "Tisch"}, blocking=True)
    assert brain_call.planned == [{"task": "Tisch", "number": "04542 1234"}]


async def test_call_needs_a_number_or_a_name(hass: HomeAssistant, with_tasks) -> None:
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "call", {"task": "Tisch"}, blocking=True)


async def test_a_refused_call_says_why(hass: HomeAssistant, with_tasks, brain_call) -> None:
    brain_call.plan_error = BrainCallRefusedError(400, "Küche is not a person with a number in the callers list.")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "call", {"name": "Küche", "task": "x"}, blocking=True)
    assert err.value.translation_key == "call_refused"
    assert err.value.translation_placeholders == {"reason": "Küche is not a person with a number in the callers list."}


async def test_call_when_brain_call_is_down(hass: HomeAssistant, with_tasks, brain_call) -> None:
    brain_call.plan_error = BrainCallConnectionError("down")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "call", {"number": "0401", "task": "x"}, blocking=True)
    assert err.value.translation_key == "cannot_connect"


async def test_call_without_planned_calls_in_brain_call(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "call", {"number": "0401", "task": "x"}, blocking=True)
    assert err.value.translation_key == "tasks_off"


async def test_call_without_a_loaded_entry(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    brain_call.info = BrainCallConnectionError("down")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "call", {"number": "0401", "task": "x"}, blocking=True)
    assert err.value.translation_key == "not_loaded"
