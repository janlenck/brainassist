"""The conversation agents: Brain Assist Voice and Brain Assist Text.

Home Assistant does not reliably say whether a request was spoken or typed, so there is one agent per kind of
answer, chosen per voice assistant (pipeline): "Voice" for voice devices (short spoken sentences), "Text" for the
chat in the app and on dashboards (Markdown allowed). Both forward the request to the same Brain Call.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, intent, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BrainAssistConfigEntry
from .api import BrainCallAuthError, BrainCallError
from .const import CHANNELS, DOMAIN, LOGGER


async def async_setup_entry(
    hass: HomeAssistant, entry: BrainAssistConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the two conversation agents of a Brain Call server."""
    async_add_entities(BrainAssistEntity(entry, channel) for channel in CHANNELS)


class BrainAssistEntity(conversation.ConversationEntity):
    """A conversation agent that lets Brain Call answer."""

    _attr_has_entity_name = True
    _attr_supports_streaming = True
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: BrainAssistConfigEntry, channel: str) -> None:
        """Set up the agent for one channel ("voice" or "text")."""
        self.entry = entry
        self.channel = channel
        info = entry.runtime_data.info
        self._attr_unique_id = f"{entry.entry_id}_{channel}"
        self._attr_translation_key = channel
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Brain Assist",
            manufacturer="Brain Call",
            model=info.assistant or None,
            sw_version=info.version or None,
            entry_type=dr.DeviceEntryType.SERVICE,
            configuration_url=entry.runtime_data.client.url,
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Brain Call answers in the language of the person or the pipeline."""
        return MATCH_ALL

    async def _who(self, user_input: conversation.ConversationInput) -> dict[str, str]:
        """Who asks and from where: Brain Call decides from it what the request may do."""
        who: dict[str, str] = {}
        if (user_id := user_input.context.user_id) and (user := await self.hass.auth.async_get_user(user_id)):
            who["user_id"] = user.id
            who["user_name"] = user.name or ""
        device_id = user_input.device_id
        if not device_id and user_input.satellite_id:
            # a voice satellite names its entity; the device behind it is what callers list
            if satellite := er.async_get(self.hass).async_get(user_input.satellite_id):
                device_id = satellite.device_id
        if device_id and (device := dr.async_get(self.hass).async_get(device_id)):
            who["device_id"] = device.id
            who["device_name"] = device.name_by_user or device.name or ""
            if device.area_id and (area := ar.async_get(self.hass).async_get_area(device.area_id)):
                who["area_id"] = area.id
                who["area_name"] = area.name
        return who

    async def _deltas(self, payload: dict[str, Any], end: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Brain Call's answer as deltas for the chat log: text, and the tools Brain Call used with their results.

        The tools ran in Brain Call, so they are external for Home Assistant: shown in the debug view, not run again.
        """
        yield {"role": "assistant"}
        async for event in self.entry.runtime_data.client.async_converse(payload):
            kind = event.get("type")
            if kind == "text":
                yield {"content": str(event.get("text") or "")}
            elif kind == "tool_call":
                if event.get("server") or not event.get("id"):
                    continue  # a web search or fetch at Anthropic: no result comes back for it
                yield {
                    "tool_calls": [
                        llm.ToolInput(
                            id=str(event["id"]),
                            tool_name=str(event.get("name") or ""),
                            tool_args=event.get("input") if isinstance(event.get("input"), dict) else {},
                            external=True,
                        )
                    ]
                }
            elif kind == "tool_result":
                yield {
                    "role": "tool_result",
                    "tool_call_id": str(event.get("id") or ""),
                    "tool_name": str(event.get("name") or ""),
                    "result": llm.ToolResult(data={"result": event.get("result")}),
                }
                yield {"role": "assistant"}
            elif kind == "end":
                end["continue_conversation"] = bool(event.get("continue_conversation"))
                return
            elif kind == "error":
                raise BrainCallError(str(event.get("message") or "Brain Call reported an error"))
        raise BrainCallError("Brain Call ended the answer early")

    def _failed(self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog, message: str):
        response = intent.IntentResponse(language=user_input.language)
        response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, message)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)

    async def _async_handle_message(
        self, user_input: conversation.ConversationInput, chat_log: conversation.ChatLog
    ) -> conversation.ConversationResult:
        """Forward the request to Brain Call and stream its answer into the chat log."""
        payload = {
            "text": user_input.text,
            "channel": self.channel,
            "language": user_input.language,
            "conversation_id": chat_log.conversation_id,
            **await self._who(user_input),
        }
        end: dict[str, Any] = {}
        try:
            async for _ in chat_log.async_add_delta_content_stream(self.entity_id, self._deltas(payload, end)):
                pass
        except BrainCallAuthError as err:
            LOGGER.error("Brain Call refused the token: %s", err)
            self.entry.async_start_reauth(self.hass)
            return self._failed(user_input, chat_log, "Brain Call refused the token of Brain Assist.")
        except BrainCallError as err:
            LOGGER.error("Request to Brain Call failed: %s", err)
            return self._failed(user_input, chat_log, f"Brain Call could not answer: {err}")

        last = chat_log.content[-1]
        if not isinstance(last, conversation.AssistantContent) or not last.content:
            LOGGER.error("Brain Call ended without an answer")
            return self._failed(user_input, chat_log, "Brain Call gave no answer.")
        result = conversation.async_get_result_from_chat_log(user_input, chat_log)
        # Brain Call says whether its answer asks something back: a voice device then keeps listening
        result.continue_conversation = bool(end.get("continue_conversation"))
        return result
