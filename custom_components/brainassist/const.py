"""Constants of Brain Assist."""

import logging

DOMAIN = "brainassist"
LOGGER = logging.getLogger(__package__)

# Brain Call on the same host (Home Assistant OS: the app uses the host network)
DEFAULT_URL = "http://127.0.0.1:8099"

# The version of Brain Call's API (GET /api/version, "api") this integration speaks
API_VERSION = 1

# The two agents: a voice device reads the answer out, a chat shows it
CHANNEL_VOICE = "voice"
CHANNEL_TEXT = "text"
CHANNELS = (CHANNEL_VOICE, CHANNEL_TEXT)

ISSUE_INCOMPATIBLE = "incompatible_api"
