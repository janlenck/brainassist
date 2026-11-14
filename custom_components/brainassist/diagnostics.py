"""Diagnostics download: the connection settings without the token, and what the server says about itself."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant

from . import BrainAssistConfigEntry
from .const import API_VERSION

TO_REDACT = {CONF_TOKEN}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BrainAssistConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "client_api": API_VERSION,
        "server": asdict(entry.runtime_data.info),
    }
