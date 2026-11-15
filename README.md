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
access rules, the memory. Speech recognition and voice stay with Home Assistant.

## Requirements

- Home Assistant 2026.11 or newer
- Brain Call, running and reachable from Home Assistant. It needs no phone for this: without a SIP account Brain
  Call only answers Brain Assist.
  - 1.11.0 or newer for the agents
  - 1.12.0 for one memory per person on the phone and in Assist
  - 1.13.0 for discovery (no address or token to type) and for Assist without notifications
  - 1.14.0 for [planned calls](#planned-calls) from Home Assistant

## Installation

Until Brain Assist is in the catalog of the Home Assistant Marketplace, add it as a custom repository:

1. Settings → Marketplace → ⋮ → Custom repositories: `https://github.com/janlenck/brainassist`, type
   *Integration*. This needs a GitHub connection in the Marketplace.
2. Install *Brain Assist* and restart Home Assistant.

By hand: copy `custom_components/brainassist` into `custom_components` of your configuration folder and restart.

## Two agents: Voice and Text

Home Assistant does not tell the agent whether a request was spoken or typed: it gets text either way. The answer
has to fit the one or the other, so Brain Assist has two agents and each assistant picks one.

| Agent | English | German | Ukrainian | Answers |
|---|---|---|---|---|
| Voice | Brain Assist Voice | Brain Assist Sprache | Brain Assist Голос | short spoken sentences, no lists, tables or Markdown |
| Text | Brain Assist Text | Brain Assist Text | Brain Assist Текст | lists, tables, links and longer answers |

The names follow the language of the Home Assistant interface.

- **Voice** for every assistant whose answer is read out: speech input in the companion app, voice satellites.
  A typed question to a Voice assistant gets the short answer; that does no harm.
- **Text** for the assistant you type to: dashboard chat, browser. Behind speech input it gives long answers,
  and a request to speak slower gets "I only output text": the speech rate is a setting of the text-to-speech
  engine, not of Brain Call.

## Setup

1. **Brain Call:** add the people and devices that may use Assist to *Callers* (see
   [Who may do what](#who-may-do-what)).
2. **Integration:** Settings → Devices & services → Add integration → *Brain Assist*. On Home Assistant OS it asks
   where Brain Call runs:
   - **As an app on this system:** a running Brain Call app is used, a stopped one is started. If it is not
     installed, Brain Assist says what it will do and does it only after you agree: add the repository
     `https://github.com/janlenck/braincall` to the app store, install the app, put the Claude API key and one
     administrator (a Home Assistant user) into its configuration, start it. Phone, memory and everything else
     stay in the app configuration.
   - **On another computer, or by hand:** address and token. As an app on the same system:
     `http://127.0.0.1:8099`. The token is in `api-token.txt` in the folder of the app
     (`/app_configs/98055a04_braincall`, Samba or File editor), or it is the one you set as *API token* in Brain
     Call.

   Without the Supervisor (Home Assistant Container or Core) only the second way exists.

   With Brain Call 1.13.0 or newer as an app, Home Assistant also shows *Brain Assist* as **discovered** under
   Settings → Devices & services as soon as the app runs: confirm, nothing to type. A new token reaches the
   integration the same way at the next start of the app.
3. **Voice assistants:** set them up as below.

### Recommended setup: two assistants

Settings → Voice assistants → Add assistant, twice:

| | Voice assistant | Text assistant |
|---|---|---|
| Language | the household's language | the household's language |
| Conversation agent | Brain Assist Voice | Brain Assist Text |
| Speech-to-text | your speech-to-text | None |
| Text-to-speech | your text-to-speech (speech rate here, if the engine has it) | None, otherwise tables get read out |
| Prefer handling commands locally | on | off |
| Used by | companion app microphone, voice satellites | dashboard chat, browser |

- **Preferred assistant** (the star in the list): the one the app uses when nothing else is chosen; usually the
  Voice assistant. In the Assist dialog of the app the assistant can be switched at the top.
- **Voice satellites:** device page → *Assistant* → the Voice assistant.
- **Language of the assistant:** set it to the household's language. With an English assistant Brain Call
  answered German questions in English.

### Prefer handling commands locally

A setting of each assistant. If it is on, Home Assistant answers simple commands itself, and Brain Call never sees
them.

| | on | off |
|---|---|---|
| Speed and cost | simple commands ("turn on the light", "timer 5 minutes") at once and free | everything goes through Claude: 1–3 s and a few cents |
| Timers on satellites, media by area, announcements | work | do not work: Brain Call has no such tools |
| Memory | commands handled locally are missing | Brain Call knows everything that was said |
| Who may do what | for local commands only Home Assistant's exposure applies | only *Callers* in Brain Call |

Recommended: **on** for the Voice assistant (speed, timers on satellites), **off** for the Text assistant: there
the context matters more than a second, and a memory with gaps confuses.

### Who may do what

Brain Call decides, from its *Callers*, and from nothing else: *Expose* in Home Assistant's voice assistant
settings does not apply to Brain Call. An entry for Assist has `ha_user` and/or `ha_device` instead of or next to
the phone number:

```yaml
- number: "0151xxxxxxx"   # the same person on the phone
  ha_user: Jan            # name or id of the Home Assistant user
  name: Jan
  access: admin
- ha_user: dashboard      # the user a shared dashboard runs as
  name: Hallway
  access: admin
- ha_device: Küche        # name or id of a device, or of its area: voice devices have no user
  access: read
```

- Users and devices that are not listed get a fixed sentence ("not set up") and cause no costs. The Brain Call
  log names the user, device and area that asked: copy the name from there.
- `*` as `ha_user` or `ha_device` stands for everyone else.
- **A shared dashboard** may well be `admin`: whoever stands at it is in the house anyway, as with Home
  Assistant's own assistant. A restricted entry is an option, e.g. `ha_device: Flur Tablet` with
  `access: shopping`.
- With an entry for the user and one for the device the stricter access wins: a hallway tablet with `shopping`
  stays at the shopping list even while an administrator is logged in on it.

Brain Assist tells Brain Call who asks (user id and name) and from where (device, area). The area is what "here"
means: "turn on the light" on the kitchen device means the kitchen. A request has an area only if its device has
one in Home Assistant. Phones and browsers usually have none; voice satellites are not tested yet.

## What to know

- **Questions back:** when the answer ends with a question, a voice device keeps listening.
- **Memory:** with *Memory per person* on in Brain Call (1.12.0 or newer, off by default), phone and Assist
  share what is remembered of a person: an entry in *Callers* with number and `ha_user` has one memory. A
  conversation is summarized when it is over (15 minutes after the last request, or when the same user starts
  a new one on the same device), which costs one more short request to Claude. A voice device without a user
  has its own memory.
- **No notifications:** Assist conversations are never announced (Brain Call 1.13.0 or newer); notifications
  are for calls.
- **Tools** Brain Call used show up in the debug view of the assistant (Settings → Voice assistants → ⋮ → Debug).
- Brain Call trusts what Brain Assist says about user and device. Whoever has the API token can act as any user:
  treat it like a Home Assistant access token. It is stored in the config entry and left out of diagnostics.

## Planned calls

Brain Call can make a call on its own with a task, hold the conversation and work through the result ("Ruf im
Restaurant an und reservier einen Tisch für vier"). With *Planned calls* on in Brain Call 1.14.0 or newer:

- **In Assist:** an administrator asks for it by voice or in the chat: "Ruf Mark morgen um 18 Uhr an und frag, ob
  er Freitag Zeit hat". Brain Call reads the plan back and stores it only after a yes; a voice device keeps
  listening for it.
- **Action `brainassist.call`**, for automations, scripts and by hand: `number`, or `name` alone for a person
  from *Callers* (their number is taken from there), and `task`. Optional: `not_before`, `not_after`, `retries`,
  `language`, `call_back`, `leave_message`. The answer is the planned task with its `id`.

  ```yaml
  action: brainassist.call
  data:
    name: Mark
    task: Frag Mark, ob er Freitag um 18 Uhr Zeit hat, und trag es in den Kalender ein.
    not_before: "2026-11-16 17:00:00"
  response_variable: planned
  ```

- **Who asks:** the Home Assistant user who runs the action or speaks in Assist, as their entry in *Callers*. An
  automation has no user: then it is the owner. The result is called back only if the requester's entry has a
  phone number; otherwise it comes as notification and event.
- **To-do list *Planned calls*** (`todo.brain_assist_planned_calls`): open calls first, with the time of the next
  attempt, then the finished ones of the last 30 days with their result. Deleting an open item cancels the call; a
  running or finished one cannot be taken back. It follows Brain Call's event `braincall_task_updated` and also
  asks once a minute.
- **The result** comes as event `braincall_task_finished` from Brain Call (`id`, `status`, `summary`, `answers`,
  `steps`, `open_points`, …): use it as trigger in automations. Brain Assist adds no event of its own.

## Troubleshooting

| What you see | Why |
|---|---|
| "This user or device is not set up for …" | No entry in *Callers* matches. The Brain Call log shows the names it got |
| Long answers read out, "I only output text" | The assistant with speech uses *Brain Assist Text*: switch it to Voice |
| Answers in the wrong language | The language of the assistant is not the household's language |
| Setup: "cannot be reached" | Brain Call is not running, or the address is wrong. Without an own *API token* Brain Call only listens on `127.0.0.1` |
| Setup: "refused the token" | The token is not the one in `api-token.txt` or in *API token* |
| Setup: "too old" | Brain Call is older than 1.11.0 |
| Setup: the app did not start or did not answer | The log of the app (Settings → Apps → Brain Call → Log) says why; most often the Claude API key |
| "As an app on this system" ends in the address form | The app is older than 1.13.0 and does not announce itself: update it, or enter address and token |
| Repair "Brain Call is too old / Brain Assist is too old" | The two were updated apart and no longer speak the same API version. Update the one named; Brain Assist reconnects by itself |
| `brainassist.call`: "Planned calls are off" | *Planned calls* is off in Brain Call, Brain Call has no phone, or it is older than 1.14.0 |
| `brainassist.call`: "did not plan the call: …" | Brain Call's reason, e.g. a number that may not be dialed or a name that is no person with a number in *Callers* |
| "Brain Call could not answer: …" | Brain Call reported a failure, e.g. from the Claude API. The rest of the sentence and the Brain Call log say which |

## Not yet

Limits per person. See
[brainassist#2](https://github.com/janlenck/brainassist/issues/2) and
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
