"""The Brain Call app on Home Assistant OS: its repository in the app store, and one AddonManager per app.

Nothing here changes the system on its own: the config flow adds the repository and installs the app only after
the user agreed to it.
"""

from aiohasupervisor.models import StoreAddRepository
from homeassistant.components.hassio import AddonManager
from homeassistant.components.hassio.handler import get_supervisor_client
from homeassistant.core import HomeAssistant

from .const import LOGGER

APP_NAME = "Brain Call"
APP_REPOSITORY = "https://github.com/janlenck/braincall"
# The Supervisor names an app "<first 8 hex digits of sha1(repository URL)>_<slug in config.yaml>"
APP_SLUG_SUFFIX = "_braincall"
APP_SLUG = "98055a04" + APP_SLUG_SUFFIX

DATA_MANAGERS = "brainassist_app_managers"


def _same_repository(source: str) -> bool:
    """The Brain Call repository, however it was typed when it was added (case, .git, trailing slash)."""
    return source.strip().rstrip("/").lower().removesuffix(".git") == APP_REPOSITORY


async def async_find_app_slug(hass: HomeAssistant) -> str | None:
    """The slug of the Brain Call app if its repository is in the app store, else None.

    Added under another spelling of the URL the repository has another prefix, so the slug comes from the store.
    Raises SupervisorError if the Supervisor cannot be asked.
    """
    for repository in await get_supervisor_client(hass).store.repositories_list():
        if _same_repository(repository.source):
            return repository.slug + APP_SLUG_SUFFIX
    return None


async def async_add_repository(hass: HomeAssistant) -> str:
    """Add the Brain Call repository to the app store; returns the slug of the app. Raises SupervisorError."""
    await get_supervisor_client(hass).store.add_repository(StoreAddRepository(repository=APP_REPOSITORY))
    return await async_find_app_slug(hass) or APP_SLUG


def get_app_manager(hass: HomeAssistant, slug: str) -> AddonManager:
    """One AddonManager per app, so a second flow sees an install or start that is still running."""
    managers: dict[str, AddonManager] = hass.data.setdefault(DATA_MANAGERS, {})
    if slug not in managers:
        managers[slug] = AddonManager(hass, LOGGER, APP_NAME, slug)
    return managers[slug]
