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

# Planned calls (Brain Call 1.14.0): fired by Brain Call into Home Assistant on every status change and at the end
EVENT_TASK_UPDATED = "braincall_task_updated"
EVENT_TASK_FINISHED = "braincall_task_finished"
# waiting (Brain Call 1.16.0): open, waiting for a call back until not_after
OPEN_STATUSES = ("planned", "running", "waiting")
SERVICE_CALL = "call"

# Line state (Brain Call 1.19.0): GET /api/status and the event braincall_state on every change of line or SIP
EVENT_STATE = "braincall_state"
LINE_STATES = ("idle", "ringing", "dialing", "in_call")
SIP_REGISTERED = "REGISTERED"
SIP_REGISTERING = "REGISTERING"  # also for a moment every 2 minutes while the phone registers again
