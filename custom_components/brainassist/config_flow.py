"""Config flow: where Brain Call is and its API token.

Three ways in:
- by hand: address and token of a Brain Call server (always offered);
- the Brain Call app on this system (Home Assistant OS): used if it runs, started if it is installed, installed
  with its repository after the user agreed to it; it then tells Home Assistant address and token itself;
- discovery: the running app announces address and token (Brain Call 1.13.0 or newer), the user only confirms.
"""

import asyncio
from collections.abc import Mapping
from typing import Any

from aiohasupervisor import SupervisorError
from awesomeversion import AwesomeVersion
from homeassistant.components.hassio import AddonError, AddonInfo, AddonManager, AddonState
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.hassio import is_hassio
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
import voluptuous as vol

from .addon import APP_NAME, APP_REPOSITORY, APP_SLUG, async_add_repository, async_find_app_slug, get_app_manager
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

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

# Options of the Brain Call app the flow sets for a new app: Claude is needed to start, and one administrator,
# so that someone may use Assist at all
CONF_CLAUDE_KEY = "anthropic_api_key"
CONF_ADMIN = "admin"

# After the start of the app: how often to look whether it announced itself and answers, and for how long at most
# (the requests in between included)
START_POLL_S = 3
START_TIMEOUT_S = 120
# Brain Call announces itself to Home Assistant since this version; an older app gets address and token typed
ANNOUNCES_SINCE = AwesomeVersion("1.13.0")


def _announces(version: str | None) -> bool:
    """Whether an app of this version announces itself; an unknown version is taken to be new."""
    if not version:
        return True
    parsed = AwesomeVersion(version)
    return not parsed.valid or parsed >= ANNOUNCES_SINCE


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


class AppNotReady(Exception):
    """The app was started but did not announce itself or answer in time."""


class BrainAssistConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect Home Assistant to a Brain Call server."""

    VERSION = 1

    def __init__(self) -> None:
        """Nothing found yet."""
        self._slug = APP_SLUG
        self._repository_missing = False
        self._app_options: dict[str, Any] = {}
        self._task: asyncio.Task | None = None
        self._found: tuple[str, str] | None = None  # address and token the app announced
        self._info: ServerInfo | None = None
        self._errors: dict[str, str] = {}  # why the app that announced itself did not answer as expected
        self._app_name = APP_NAME
        self._announces = True  # False for an installed app older than 1.13.0

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
        """On Home Assistant OS: the app on this system or another server. Elsewhere: address and token."""
        if is_hassio(self.hass):
            return self.async_show_menu(step_id="user", menu_options=["app", "manual"])
        return await self.async_step_manual()

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the address and the token of Brain Call."""
        errors: dict[str, str] = {}
        url = DEFAULT_URL
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            await self.async_set_unique_id(url)
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

    # The app on this system

    async def _app_info(self) -> AddonInfo:
        try:
            return await get_app_manager(self.hass, self._slug).async_get_addon_info()
        except AddonError as err:
            LOGGER.error("Brain Call app: %s", err)
            raise AbortFlow("app_info_failed") from err

    async def async_step_app(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Use the app if it runs, start it if it is installed, otherwise offer to install it."""
        try:
            slug = await async_find_app_slug(self.hass)
        except SupervisorError as err:
            LOGGER.error("Brain Call app: the app store cannot be read: %s", err)
            return self.async_abort(reason="app_info_failed")
        if slug is None:
            self._repository_missing = True
            return await self.async_step_add_repository_confirm()
        self._slug = slug
        info = await self._app_info()
        self._announces = _announces(info.version)
        if info.state is AddonState.RUNNING:
            return await self.async_step_finish_app()
        if info.state is AddonState.NOT_INSTALLED:
            return await self.async_step_install_app_confirm()
        if info.state is AddonState.NOT_RUNNING:
            if not info.options.get(CONF_CLAUDE_KEY):  # it would not start
                return await self.async_step_app_settings()
            return await self.async_step_start_app()
        return self.async_abort(reason="app_busy")  # installing or updating right now

    async def _settings_schema(self) -> vol.Schema:
        """Claude API key and the Home Assistant user who becomes administrator in Brain Call."""
        users = [u for u in await self.hass.auth.async_get_users() if not u.system_generated and u.is_active]
        owner = next((u.name for u in users if u.is_owner and u.name), None)
        options = [SelectOptionDict(value=u.name, label=u.name) for u in users if u.name]
        schema: dict[Any, Any] = {vol.Required(CONF_CLAUDE_KEY): TOKEN_SELECTOR}
        if options:
            admin = vol.Optional(CONF_ADMIN, default=owner) if owner else vol.Optional(CONF_ADMIN)
            schema[admin] = SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))
        return vol.Schema(schema)

    def _remember_settings(self, user_input: dict[str, Any]) -> None:
        self._app_options = {CONF_CLAUDE_KEY: user_input[CONF_CLAUDE_KEY].strip()}
        if admin := user_input.get(CONF_ADMIN):
            self._app_options["callers"] = [{"ha_user": admin, "name": admin, "access": "admin"}]

    async def _set_app_options(self) -> None:
        """Merge what the user typed into the options the app has; callers already there stay."""
        manager = get_app_manager(self.hass, self._slug)
        options = dict((await manager.async_get_addon_info()).options)
        options[CONF_CLAUDE_KEY] = self._app_options[CONF_CLAUDE_KEY]
        for caller in self._app_options.get("callers", []):
            callers = list(options.get("callers") or [])
            if not any(c.get("ha_user") == caller["ha_user"] for c in callers):
                options["callers"] = [*callers, caller]
        await manager.async_set_addon_options(options)

    async def async_step_add_repository_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nothing is added or installed without consent: the repository is not in the app store yet."""
        return await self._confirm_install("add_repository_confirm", user_input)

    async def async_step_install_app_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nothing is installed without consent: the repository is there, the app is not installed."""
        return await self._confirm_install("install_app_confirm", user_input)

    async def _confirm_install(self, step_id: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        """Say what happens, and ask for what the app needs to start."""
        if user_input is not None:
            self._remember_settings(user_input)
            return await self.async_step_install_app()
        return self.async_show_form(
            step_id=step_id,
            data_schema=await self._settings_schema(),
            description_placeholders={"repository": APP_REPOSITORY},
        )

    async def _install(self) -> None:
        if self._repository_missing:
            try:
                self._slug = await async_add_repository(self.hass)
            except SupervisorError as err:
                raise AddonError(f"Adding {APP_REPOSITORY} to the app store failed: {err}") from err
            self._repository_missing = False
        manager = get_app_manager(self.hass, self._slug)
        await manager.async_install_addon()
        await self._set_app_options()

    async def async_step_install_app(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add the repository if needed, install the app and give it the settings."""
        if self._task is None:
            self._task = self.hass.async_create_task(self._install())
        if not self._task.done():
            return self.async_show_progress(
                step_id="install_app", progress_action="install_app", progress_task=self._task
            )
        try:
            await self._task
        except AddonError as err:
            LOGGER.error("Brain Call app: %s", err)
            return self.async_show_progress_done(next_step_id="install_failed")
        finally:
            self._task = None
        return self.async_show_progress_done(next_step_id="start_app")

    async def async_step_install_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Installing failed; the log says why."""
        return self.async_abort(reason="app_install_failed", description_placeholders={"repository": APP_REPOSITORY})

    async def async_step_app_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The app is installed but has no Claude API key: it would not start without one."""
        if user_input is not None:
            self._remember_settings(user_input)
            try:
                await self._set_app_options()
            except AddonError as err:
                LOGGER.error("Brain Call app: %s", err)
                return self.async_abort(reason="app_info_failed")
            return await self.async_step_start_app()
        return self.async_show_form(step_id="app_settings", data_schema=await self._settings_schema())

    async def _start(self) -> None:
        """Start the app and wait until it announced itself and answers, at most START_TIMEOUT_S in all.

        An app older than 1.13.0 never announces itself: it is only started, and the address form follows.
        """
        manager = get_app_manager(self.hass, self._slug)
        await manager.async_start_addon()
        if not self._announces:
            return
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with asyncio.timeout(START_TIMEOUT_S):
                while True:  # the Supervisor and the app tell nothing by themselves: look again and again
                    if await self._found_app(manager):
                        return
                    await asyncio.sleep(START_POLL_S)
        except TimeoutError:
            last = "did not announce itself" if self._found is None else "announced itself but did not answer"
            raise AppNotReady(f"the app {last} within {loop.time() - started:.0f} s") from None

    async def _found_app(self, manager: AddonManager) -> bool:
        """True once the app announced address and token and answers with a version this integration speaks."""
        try:
            found = _connection(await manager.async_get_addon_discovery_info())
        except AddonError:
            return False
        if not found:
            return False
        self._found = found
        self._errors = {}
        self._info = await self._check(*found, self._errors)
        if self._errors.get("base") in ("too_old", "too_new"):
            raise AbortFlow(self._errors["base"])
        return self._info is not None

    async def async_step_start_app(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start the app and wait for it."""
        if self._task is None:
            self._task = self.hass.async_create_task(self._start())
        if not self._task.done():
            return self.async_show_progress(step_id="start_app", progress_action="start_app", progress_task=self._task)
        try:
            await self._task
        except (AddonError, AppNotReady) as err:
            LOGGER.error("Brain Call app: %s", err)
            return self.async_show_progress_done(next_step_id="start_failed")
        except AbortFlow:
            return self.async_show_progress_done(next_step_id="app_incompatible")
        finally:
            self._task = None
        return self.async_show_progress_done(next_step_id="finish_app")

    async def async_step_start_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The app did not start or did not answer; its log says why."""
        return self.async_abort(reason="app_start_failed")

    async def async_step_app_incompatible(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The app answers, but with an API version this integration does not speak."""
        return self.async_abort(reason=self._errors["base"])

    async def async_step_finish_app(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Connect to the running app with what it announced; an app too old to announce itself: by hand."""
        manager = get_app_manager(self.hass, self._slug)
        if self._info is None and not (self._announces and await self._found_app(manager)):
            if self._found is None:  # Brain Call before 1.13.0 does not announce itself
                return await self.async_step_manual()
            return self.async_abort(reason=self._errors.get("base", "cannot_connect"))
        assert self._found is not None and self._info is not None
        url, token = self._found
        # the app announced itself when it started: that discovery flow ends when this one creates the entry
        await self.async_set_unique_id(url, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates={CONF_TOKEN: token})
        return self._create(url, token, self._info)

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
