# Brain Assist

<img src="custom_components/brainassist/brand/logo.png" alt="Brain Assist" width="300">

**[Brain Call](https://github.com/janlenck/braincall) as the conversation agent of Home Assistant's own assistant.**

Brain Call is a voice agent built on Claude that answers the phone and can read and control Home Assistant.
Brain Assist brings the same brain into Assist: the chat in the app and on dashboards, and voice devices. Phone
and Assist then share one assistant, one set of tools and one list of who may do what.

```
Assist (app, dashboard, voice device)
   │ conversation agent "Brain Assist Voice" / "Brain Assist Text"
   ▼
Brain Assist (this integration, inside Home Assistant)
   │ HTTP POST /api/converse, answer as a stream
   ▼
Brain Call (app on Home Assistant OS, or on another computer)
   └─ Claude with the tools the person may use (Home Assistant, calendar, shopping list, web)
```

Brain Assist is a thin client. Everything that decides something lives in Brain Call: the prompt, the tools, the
access rules. Speech recognition and voice stay with Home Assistant.

## Requirements

- Home Assistant 2026.11 or newer
- Brain Call 1.11.0 or newer, running and reachable from Home Assistant. It needs no phone for this: without a
  SIP account Brain Call only answers Brain Assist.

## Installation

Until Brain Assist is in the catalog of the Home Assistant Marketplace, add it as a custom repository:

1. Settings → Marketplace → ⋮ → Custom repositories: `https://github.com/janlenck/brainassist`, type
   *Integration*. This needs a GitHub connection in the Marketplace.
2. Install *Brain Assist* and restart Home Assistant.

By hand: copy `custom_components/brainassist` into `custom_components` of your configuration folder and restart.

## Setup

1. **Brain Call:** update to 1.11.0 or newer and add the people and devices that may use Assist to *Callers*
   (see below).
2. **API token:** every request to Brain Call needs it. With the default settings Brain Call generates one and
   keeps it in `api-token.txt` in the folder of the app (`/app_configs/98055a04_braincall`, Samba or File
   editor). If you set *API token* in Brain Call yourself, use that one.
3. **Integration:** Settings → Devices & services → Add integration → *Brain Assist*. Address of Brain Call
   (as an app on the same system: `http://127.0.0.1:8099`) and the token.
4. **Voice assistants:** Settings → Voice assistants → your assistant → *Conversation agent*:
   - **Brain Assist Voice** for assistants used by voice devices: short spoken sentences without Markdown.
   - **Brain Assist Text** for the assistant you type to: lists, tables and links allowed.

   Home Assistant does not reliably say whether a request was spoken or typed, hence two agents. Speech-to-text
   and text-to-speech of the assistant stay as they are.

### Who may do what

Brain Call decides, from its *Callers*. An entry for Assist has `ha_user` and/or `ha_device` instead of or next
to the phone number:

```yaml
- number: "0151xxxxxxx"   # the same person on the phone
  ha_user: Jan            # name or id of the Home Assistant user
  name: Jan
  access: admin
- ha_device: Küche        # name or id of a device, or of its area: voice devices have no user
  access: read
- ha_device: Flur Tablet
  access: shopping
```

- Users and devices that are not listed get a fixed sentence ("not set up") and cause no costs. The Brain Call
  log names the user, device and area that asked: copy the name from there.
- `*` as `ha_user` or `ha_device` stands for everyone else.
- With an entry for the user and one for the device the stricter access wins: a hallway tablet with `shopping`
  stays at the shopping list even while an administrator is logged in on it.

Brain Assist tells Brain Call who asks (user id and name) and from where (device, area). The area is also what
"here" means: "turn on the light" on the kitchen device means the kitchen.

## What to know

- **Prefer handling commands locally** (a setting of the voice assistant): if it is on, Home Assistant answers
  simple commands like "turn on the light" itself and Brain Call never sees them. That is faster, but Brain
  Call's access rules do not apply to those commands.
- **Questions back:** when the answer ends with a question, a voice device keeps listening.
- **Memory:** with *Memory per person* on in Brain Call (1.12.0 or newer, off by default), phone and Assist
  share what is remembered of a person: an entry in *Callers* with number and `ha_user` has one memory. A
  conversation is summarized when it is over (15 minutes after the last request, or when the same user starts
  a new one on the same device), which costs one more short request to Claude. A voice device without a user
  has its own memory.
- **Tools** Brain Call used show up in the debug view of the assistant (Settings → Voice assistants → ⋮ → Debug).
- **Timers, media control by area and announcements** of Home Assistant's own assistant are not part of Brain
  Call. With local handling off they stop working for an assistant that uses Brain Assist.
- Brain Call trusts what Brain Assist says about user and device. Whoever has the API token can act as any user:
  treat it like a Home Assistant access token. It is stored in the config entry and left out of diagnostics.

## Troubleshooting

| What you see | Why |
|---|---|
| "This user or device is not set up for …" | No entry in *Callers* matches. The Brain Call log shows the names it got |
| Setup: "cannot be reached" | Brain Call is not running, or the address is wrong. Without an own *API token* Brain Call only listens on `127.0.0.1` |
| Setup: "refused the token" | The token is not the one in `api-token.txt` or in *API token* |
| Setup: "too old" | Brain Call is older than 1.11.0 |
| Repair "Brain Call is too old / Brain Assist is too old" | The two were updated apart and no longer speak the same API version. Update the one named; Brain Assist reconnects by itself |
| "Brain Call could not answer: …" | Brain Call reported a failure, e.g. from the Claude API. The rest of the sentence and the Brain Call log say which |

## Not yet

Finding and installing the Brain Call app from the config flow, limits per person, planned calls from Home
Assistant (`brainassist.call`, a to-do list of tasks). See
[braincall#50](https://github.com/janlenck/braincall/issues/50).

## Development

```bash
python -m pip install -r requirements-test.txt
python -m pip install $(python scripts/ha_requirements.py conversation)
python -m pytest -q
ruff check . && ruff format --check .
```

The tests run inside a real Home Assistant (`pytest-homeassistant-custom-component`) with Brain Call replaced by
a script of events. Releases: a new `version` in `manifest.json` on `main` creates the GitHub release.

## License

MIT, see [LICENSE](LICENSE).
