"""Status entities: Connection, Phone and Line, from /health, /api/status and the event braincall_state."""

import asyncio
from dataclasses import replace

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.brainassist.api import BrainCallAuthError, BrainCallConnectionError
from custom_components.brainassist.coordinator import STATUS_INTERVAL

from .conftest import INFO

CONNECTION = "binary_sensor.brain_assist_connection"
PHONE = "binary_sensor.brain_assist_phone"
LINE = "sensor.brain_assist_line"
CALL = {"number": "0151111", "caller_name": "Jan", "direction": "in", "since": "2026-11-18T19:00:00+00:00"}


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
async def new(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> MockConfigEntry:
    """Brain Call 1.19.0: with /api/status and the event."""
    brain_call.info = replace(INFO, version="1.19.0", line=True)
    brain_call.health["line"] = "idle"
    return await setup(hass, entry)


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(STATUS_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_entities_of_an_idle_brain_call(hass: HomeAssistant, new: MockConfigEntry) -> None:
    assert hass.states.get(CONNECTION).state == "on"
    assert hass.states.get(CONNECTION).attributes["device_class"] == "connectivity"
    assert "problems" not in hass.states.get(CONNECTION).attributes
    assert hass.states.get(PHONE).state == "on"
    line = hass.states.get(LINE)
    assert line.state == "idle" and line.attributes["options"] == ["idle", "ringing", "dialing", "in_call"]
    assert "number" not in line.attributes
    assert line.attributes["friendly_name"] == "Brain Assist Line"


async def test_the_event_shows_a_ringing_call_at_once(hass: HomeAssistant, new: MockConfigEntry, brain_call) -> None:
    hass.bus.async_fire("braincall_state", {"line": "ringing", "sip": "REGISTERED", "call": CALL})
    await hass.async_block_till_done()
    line = hass.states.get(LINE)
    assert line.state == "ringing"
    assert {k: line.attributes[k] for k in CALL} == CALL

    hass.bus.async_fire("braincall_state", {"line": "idle", "sip": "REGISTERED", "call": None})
    await hass.async_block_till_done()
    assert hass.states.get(LINE).state == "idle" and "number" not in hass.states.get(LINE).attributes


async def test_the_event_says_the_phone_lost_its_registration(hass: HomeAssistant, new: MockConfigEntry) -> None:
    hass.bus.async_fire("braincall_state", {"line": "idle", "sip": "FAILED", "call": None})
    await hass.async_block_till_done()
    assert hass.states.get(PHONE).state == "off"
    assert hass.states.get(CONNECTION).state == "on"


async def test_polling_reads_the_call_from_the_status(
    hass: HomeAssistant, new: MockConfigEntry, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    brain_call.status = {"line": "dialing", "sip": "REGISTERED", "phone": True, "call": {**CALL, "direction": "out"}}
    await poll(hass, freezer)
    line = hass.states.get(LINE)
    assert line.state == "dialing" and line.attributes["direction"] == "out"


async def test_a_new_token_still_shows_the_line_from_health(
    hass: HomeAssistant, new: MockConfigEntry, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    brain_call.status = BrainCallAuthError("refused")
    brain_call.health["line"] = "in_call"
    await poll(hass, freezer)
    line = hass.states.get(LINE)
    assert line.state == "in_call" and "number" not in line.attributes


async def test_registering_again_is_still_registered(
    hass: HomeAssistant, new: MockConfigEntry, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    """pyVoIP registers again every 2 minutes; a poll in that moment is no lost registration."""
    brain_call.status["sip"] = "REGISTERING"
    await poll(hass, freezer)
    assert hass.states.get(PHONE).state == "on"
    brain_call.status["sip"] = "FAILED"
    await poll(hass, freezer)
    assert hass.states.get(PHONE).state == "off"
    brain_call.status["sip"] = "REGISTERING"  # registering after a failure: not registered yet
    await poll(hass, freezer)
    assert hass.states.get(PHONE).state == "off"


async def test_brain_call_down(
    hass: HomeAssistant, new: MockConfigEntry, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    brain_call.health = BrainCallConnectionError("down")
    await poll(hass, freezer)
    assert hass.states.get(CONNECTION).state == "off"
    assert hass.states.get(PHONE).state == "unavailable"
    assert hass.states.get(LINE).state == "unavailable"

    # an event says nothing about health: Brain Assist asks /health again, still down
    hass.bus.async_fire("braincall_state", {"line": "idle", "sip": "REGISTERED", "call": None})
    await hass.async_block_till_done()
    assert hass.states.get(CONNECTION).state == "off" and hass.states.get(LINE).state == "unavailable"


async def test_an_event_after_a_failed_poll_asks_health_again(
    hass: HomeAssistant, new: MockConfigEntry, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    """Brain Call is back but unhealthy: the event must not turn Connection on."""
    brain_call.health = BrainCallConnectionError("down")
    await poll(hass, freezer)
    brain_call.health = {
        "status": "unhealthy",
        "problems": ["SIP watchdog stopped"],
        "sip": "REGISTERED",
        "line": "ringing",
    }
    brain_call.status = {"line": "ringing", "sip": "REGISTERED", "phone": True, "call": CALL}
    hass.bus.async_fire("braincall_state", {"line": "ringing", "sip": "REGISTERED", "call": CALL})
    await hass.async_block_till_done()
    assert hass.states.get(CONNECTION).state == "off"
    assert hass.states.get(CONNECTION).attributes["problems"] == ["SIP watchdog stopped"]
    assert hass.states.get(LINE).state == "ringing"


async def test_a_poll_older_than_an_event_keeps_the_line_of_the_event(
    hass: HomeAssistant, new: MockConfigEntry, brain_call
) -> None:
    """/api/status read idle, then braincall_state said ringing before the poll's answer came: ringing stays."""
    coordinator = new.runtime_data.status
    answered = asyncio.Event()

    async def slow_status():
        await answered.wait()
        return {"line": "idle", "sip": "REGISTERED", "phone": True, "call": None}

    brain_call.health.update(status="unhealthy", problems=["SIP watchdog stopped"])
    coordinator.client.async_status = slow_status
    refresh = hass.async_create_task(coordinator.async_refresh())
    for _ in range(5):  # the poll is waiting for /api/status now
        await asyncio.sleep(0)
    hass.bus.async_fire("braincall_state", {"line": "ringing", "sip": "REGISTERED", "call": CALL})
    for _ in range(5):
        await asyncio.sleep(0)
    assert coordinator.data.line == "ringing"
    answered.set()
    await refresh
    await hass.async_block_till_done()
    line = hass.states.get(LINE)
    assert line.state == "ringing" and line.attributes["number"] == CALL["number"]
    assert hass.states.get(CONNECTION).state == "off"  # health from the poll


async def test_unhealthy_brain_call_says_why(
    hass: HomeAssistant, new: MockConfigEntry, brain_call, freezer: FrozenDateTimeFactory
) -> None:
    brain_call.health.update(status="unhealthy", problems=["SIP watchdog stuck for 700 s"])
    await poll(hass, freezer)
    connection = hass.states.get(CONNECTION)
    assert connection.state == "off" and connection.attributes["problems"] == ["SIP watchdog stuck for 700 s"]
    assert hass.states.get(LINE).state == "idle"


@pytest.mark.parametrize(("busy", "state"), [(True, "in_call"), (False, "idle")])
async def test_older_brain_call_shows_busy_as_in_call(
    hass: HomeAssistant, entry: MockConfigEntry, brain_call, busy: bool, state: str
) -> None:
    """Brain Call before 1.19.0: no /api/status, no line in /health; busy is all it says."""
    brain_call.health["busy"] = busy
    brain_call.status = AssertionError("not asked")
    await setup(hass, entry)
    assert hass.states.get(LINE).state == state
    assert "number" not in hass.states.get(LINE).attributes


async def test_no_phone_no_phone_entities(hass: HomeAssistant, entry: MockConfigEntry, brain_call) -> None:
    brain_call.info = replace(INFO, phone=False, line=True)
    brain_call.health.update(phone=False, line="idle", sip="INACTIVE")
    await setup(hass, entry)
    entities = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert CONNECTION in entities and PHONE not in entities and LINE not in entities
    assert hass.states.get(CONNECTION).state == "on"


async def test_unknown_line_in_the_event_asks_again(hass: HomeAssistant, new: MockConfigEntry, brain_call) -> None:
    brain_call.status = {"line": "in_call", "sip": "REGISTERED", "phone": True, "call": CALL}
    hass.bus.async_fire("braincall_state", {"line": "on_hold", "sip": "REGISTERED"})
    await hass.async_block_till_done()
    assert hass.states.get(LINE).state == "in_call"


async def test_a_disabled_second_entry_still_counts(hass: HomeAssistant, new: MockConfigEntry, brain_call) -> None:
    """Its Brain Call may still fire braincall_state: the event is not taken over, each entry asks its own."""
    MockConfigEntry(
        domain="brainassist",
        title="Other",
        unique_id="http://other:8099",
        data={"url": "http://other:8099", "token": "t"},
        disabled_by=ConfigEntryDisabler.USER,
    ).add_to_hass(hass)
    hass.bus.async_fire("braincall_state", {"line": "ringing", "sip": "REGISTERED", "call": CALL})
    await hass.async_block_till_done()
    assert hass.states.get(LINE).state == "idle"


async def test_with_two_brain_calls_each_asks_its_own(hass: HomeAssistant, new: MockConfigEntry, brain_call) -> None:
    """The event does not say which Brain Call sent it."""
    other = MockConfigEntry(
        domain="brainassist",
        title="Other",
        unique_id="http://other:8099",
        data={"url": "http://other:8099", "token": "t"},
    )
    other.add_to_hass(hass)
    await setup(hass, other)
    brain_call.status = {"line": "idle", "sip": "REGISTERED", "phone": True, "call": None}
    hass.bus.async_fire("braincall_state", {"line": "ringing", "sip": "REGISTERED", "call": CALL})
    await hass.async_block_till_done()
    assert {s.state for s in hass.states.async_all("sensor")} == {"idle"}


async def test_entities_unload_with_the_entry(hass: HomeAssistant, new: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(new.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(LINE).state == "unavailable"
    hass.bus.async_fire("braincall_state", {"line": "ringing", "sip": "REGISTERED", "call": CALL})
    await hass.async_block_till_done()
    assert hass.states.get(LINE).state == "unavailable"
