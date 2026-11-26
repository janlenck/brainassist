"""Config flow: where Brain Call is and its API token.

Two ways in:
- by hand: address and token of a Brain Call server;
- discovery: the running Brain Call app announces address and token (Brain Call 1.13.0 or newer), the user only
  confirms.

Brain Assist never adds, installs, configures or starts the Brain Call app: that is done by hand (README).
"""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import voluptuous as vol

from .api import (
    BrainCallAuthError,
    BrainCallClient,
    BrainCallConnectionError,
    BrainCallError,
    BrainCallTooOldError,
    ServerInfo,
    normalize_url,
)
from .const import API_VERSION, DEFAULT_URL, DOMAIN, LOGGER

APP_NAME = "Brain Call"

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _schema(url: str) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_URL, default=url): URL_SELECTOR, vol.Required(CONF_TOKEN): TOKEN_SELECTOR})


def _connection(config: Mapping[str, Any]) -> tuple[str, str] | None:
    """Address and token from what the app announces (host, port, token); None if something is missing."""
    try:
        host, port, token = str(config["host"]), int(config["port"]), str(config["token"])
    except KeyError, TypeError, ValueError:
        return None
    if not host or not token:
        return None
    return normalize_url(f"{host}:{port}"), token


class BrainAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect Home Assistant to a Brain Call server."""

    VERSION = 1

    def __init__(self) -> None:
        """Nothing found yet."""
        self._found: tuple[str, str] | None = None  # address and token the app announced
        self._app_name = APP_NAME

    async def _check(self, url: str, token: str, errors: dict[str, str]) -> ServerInfo | None:
        """Ask the server for its version; fills errors and returns None if that fails."""
        client = BrainCallClient(async_get_clientsession(self.hass), url, token)
        try:
            info = await client.async_version()
        except BrainCallAuthError:
            errors["base"] = "invalid_auth"
        except BrainCallConnectionError:
            errors["base"] = "cannot_connect"
        except BrainCallTooOldError:
            errors["base"] = "too_old"
        except BrainCallError as err:
            LOGGER.warning("Brain Call at %s: %s", url, err)
            errors["base"] = "unknown"
        else:
            if info.api == API_VERSION:
                return info
            errors["base"] = "too_old" if info.api < API_VERSION else "too_new"
        return None

    def _create(self, url: str, token: str, info: ServerInfo) -> ConfigFlowResult:
        return self.async_create_entry(title=info.assistant or "Brain Call", data={CONF_URL: url, CONF_TOKEN: token})

    # By hand

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Address and token; a running Brain Call app shows up as discovered by itself."""
        return await self.async_step_manual()

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address and the token of Brain Call."""
        errors: dict[str, str] = {}
        url = DEFAULT_URL
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            # a discovered flow of the same app may be waiting: typing address and token wins, and that flow ends
            # when this one creates the entry
            await self.async_set_unique_id(url, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            if info := await self._check(url, token, errors):
                return self._create(url, token, info)
        return self.async_show_form(step_id="manual", data_schema=_schema(url), errors=errors)

    # Discovery: the running app announced itself

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The Brain Call app says where it is. A known address only gets the token, which may have changed."""
        if not (found := _connection(discovery_info.config)):
            return self.async_abort(reason="invalid_discovery_info")
        url, token = found
        await self.async_set_unique_id(url)
        self._abort_if_unique_id_configured(updates={CONF_TOKEN: token})
        self._found = found
        self._app_name = discovery_info.name or APP_NAME
        self.context["title_placeholders"] = {"name": self._app_name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask before connecting: the app announces itself at every start."""
        assert self._found is not None
        if user_input is None:
            return self.async_show_form(step_id="hassio_confirm", description_placeholders={"name": self._app_name})
        url, token = self._found
        errors: dict[str, str] = {}
        if info := await self._check(url, token, errors):
            return self._create(url, token, info)
        return self.async_abort(reason=errors["base"])

    # Existing entries

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the address or the token of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        url = entry.data[CONF_URL]
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            if url != entry.unique_id:  # moved to another address: that one must not be set up already
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
            if await self._check(url, token, errors):
                return self.async_update_reload_and_abort(
                    entry, unique_id=url, data_updates={CONF_URL: url, CONF_TOKEN: token}
                )
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(url), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Brain Call refused the stored token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the new token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if await self._check(entry.data[CONF_URL], token, errors):
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: token})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            description_placeholders={"url": entry.data[CONF_URL]},
            errors=errors,
        )
