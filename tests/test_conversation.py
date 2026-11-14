"""The agents: what they send to Brain Call, how the answer reaches Assist, and what happens on failures."""

from typing import Any

from homeassistant.components import conversation
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    chat_session,
    device_registry as dr,
    entity_registry as er,
    intent,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.brainassist.api import BrainCallAuthError, BrainCallConnectionError
from custom_components.brainassist.const import DOMAIN

from .conftest import answer

VOICE = "conversation.brain_assist_voice"
TEXT = "conversation.brain_assist_text"


async def ask(hass: HomeAssistant, agent: str, text: str = "Wie warm ist es?", **kwargs: Any):
    return await conversation.async_converse(
        hass, text, kwargs.pop("conversation_id", None), kwargs.pop("context", Context()), agent_id=agent, **kwargs
    )


def speech(result: conversation.ConversationResult) -> str:
    return result.response.speech["plain"]["speech"]


async def test_text_agent_forwards_the_request_and_returns_the_answer(
    hass: HomeAssistant, loaded: MockConfigEntry, brain_call, hass_admin_user: MockUser
) -> None:
    brain_call.events = answer("**Wohnzimmer:** 21 °C", "\n- Küche: 19 °C")
    result = await ask(hass, TEXT, context=Context(user_id=hass_admin_user.id), language="de")

    assert result.response.response_type is intent.IntentResponseType.ACTION_DONE
    assert speech(result) == "**Wohnzimmer:** 21 °C\n- Küche: 19 °C"
    assert result.continue_conversation is False
    assert brain_call.requests == [
        {
            "text": "Wie warm ist es?",
            "channel": "text",
            "language": "de",
            "conversation_id": result.conversation_id,
            "user_id": hass_admin_user.id,
            "user_name": hass_admin_user.name,
        }
    ]


async def test_voice_agent_names_device_and_area(hass: HomeAssistant, loaded: MockConfigEntry, brain_call) -> None:
    area = ar.async_get(hass).async_create("Küche")
    other = MockConfigEntry(domain="satellite_test")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("satellite_test", "1")}, name="Satellite"
    )
    dr.async_get(hass).async_update_device(device.id, area_id=area.id, name_by_user="Küchen-Satellit")
    brain_call.events = answer("In der Küche sind es 19 Grad.")

    result = await ask(hass, VOICE, device_id=device.id, language="de")

    assert speech(result) == "In der Küche sind es 19 Grad."
    sent = brain_call.requests[0]
    assert sent["channel"] == "voice"
    assert (sent["device_id"], sent["device_name"]) == (device.id, "Küchen-Satellit")
    assert (sent["area_id"], sent["area_name"]) == (area.id, "Küche")
    assert "user_id" not in sent  # a voice device has no user


async def test_satellite_entity_leads_to_its_device(hass: HomeAssistant, loaded: MockConfigEntry, brain_call) -> None:
    other = MockConfigEntry(domain="satellite_test")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("satellite_test", "2")}, name="Flur Satellite"
    )
    satellite = er.async_get(hass).async_get_or_create(
        "assist_satellite", "satellite_test", "2", config_entry=other, device_id=device.id
    )
    brain_call.events = answer("Ja.")

    await ask(hass, VOICE, satellite_id=satellite.entity_id)

    assert brain_call.requests[0]["device_name"] == "Flur Satellite"


async def test_a_question_keeps_the_conversation_open(hass: HomeAssistant, loaded: MockConfigEntry, brain_call) -> None:
    brain_call.events = answer("Soll ich das Rollo wirklich schließen?", continue_conversation=True)
    first = await ask(hass, VOICE, "Mach das Rollo zu")
    assert first.continue_conversation is True

    brain_call.events = answer("Ist geschlossen.")
    second = await ask(hass, VOICE, "Ja", conversation_id=first.conversation_id)
    assert second.continue_conversation is False
    assert second.conversation_id == first.conversation_id
    assert [r["conversation_id"] for r in brain_call.requests] == [first.conversation_id] * 2


async def test_brain_call_decides_whether_to_keep_listening(
    hass: HomeAssistant, loaded: MockConfigEntry, brain_call
) -> None:
    """Not the question mark alone: Brain Call says it in the end event."""
    brain_call.events = answer("Warum? Darum.", continue_conversation=False)
    assert (await ask(hass, VOICE)).continue_conversation is False
    brain_call.events = answer("Welche Lampe meinst du?", continue_conversation=False)
    assert (await ask(hass, VOICE)).continue_conversation is False


async def test_tools_of_brain_call_show_in_the_chat_log(
    hass: HomeAssistant, loaded: MockConfigEntry, brain_call
) -> None:
    brain_call.events = [
        {"type": "start", "conversation_id": "c1", "access": "admin"},
        {"type": "tool_call", "name": "web_search", "server": True},
        {"type": "tool_call", "id": "t1", "name": "ha_state", "input": {"entity_id": "sensor.wohnzimmer"}},
        {"type": "tool_result", "id": "t1", "name": "ha_state", "result": "21.3 °C"},
        {"type": "tool_call", "id": "t2", "name": "ha_control", "input": {"entity_id": "light.flur"}},
        {"type": "tool_result", "id": "t2", "name": "ha_control", "result": "ok"},
        {"type": "text", "text": "Es sind 21 Grad, "},
        {"type": "text", "text": "und das Licht ist an."},
        {"type": "end", "conversation_id": "c1", "continue_conversation": False},
    ]
    result = await ask(hass, TEXT)

    assert speech(result) == "Es sind 21 Grad, und das Licht ist an."
    with (
        chat_session.async_get_chat_session(hass, result.conversation_id) as session,
        conversation.async_get_chat_log(hass, session) as chat_log,
    ):
        content = chat_log.content[1:]  # without the system prompt
    kinds = [
        (c.role, [t.tool_name for t in getattr(c, "tool_calls", None) or []] or getattr(c, "tool_name", None))
        for c in content
    ]
    assert kinds == [
        ("user", None),
        ("assistant", ["ha_state"]),
        ("tool_result", "ha_state"),
        ("assistant", ["ha_control"]),
        ("tool_result", "ha_control"),
        ("assistant", None),
    ]
    call = content[1].tool_calls[0]
    assert (call.id, call.tool_args) == ("t1", {"entity_id": "sensor.wohnzimmer"})
    assert call.external is True  # ran in Brain Call, never again in Home Assistant
    assert content[2].tool_call_id == "t1" and content[2].result.data == {"result": "21.3 °C"}
    assert content[5].content == "Es sind 21 Grad, und das Licht ist an."


@pytest.mark.parametrize(
    ("events", "message"),
    [
        ([{"type": "start"}, {"type": "error", "message": "RuntimeError: credit balance too low"}], "credit balance"),
        ([{"type": "start"}, {"type": "text", "text": "Es sind"}], "ended the answer early"),
        ([BrainCallConnectionError("connection reset")], "connection reset"),
        ([{"type": "start"}, {"type": "end", "continue_conversation": False}], "no answer"),
    ],
)
async def test_failures_become_an_error_answer(
    hass: HomeAssistant, loaded: MockConfigEntry, brain_call, events, message
) -> None:
    brain_call.events = events
    result = await ask(hass, TEXT)
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert message in speech(result)
    assert result.continue_conversation is False


async def test_refused_token_starts_the_reauth_flow(hass: HomeAssistant, loaded: MockConfigEntry, brain_call) -> None:
    brain_call.events = [BrainCallAuthError("refused")]
    result = await ask(hass, VOICE)
    await hass.async_block_till_done()
    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert "refused the token" in speech(result)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_agents_are_offered_for_every_language(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    for agent in (VOICE, TEXT):
        info = conversation.async_get_agent_info(hass, agent)
        assert info is not None and info.supports_streaming is True
        assert hass.states.get(agent).attributes["supported_features"] == conversation.ConversationEntityFeature.CONTROL
