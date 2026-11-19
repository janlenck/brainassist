"""Client for the HTTP API of Brain Call (the server behind Brain Assist)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=15)
# Planning a call: without a plan Brain Call lets Claude make one before it answers. Giving up early would report a
# failure for a task that is stored anyway, and trying again would plan it twice
PLAN_TIMEOUT = aiohttp.ClientTimeout(total=120, sock_connect=10)
# An answer may take long (tools, web lookups): no limit on the whole request, only on the gaps in the stream
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=180)


class BrainCallError(Exception):
    """Brain Call answered with an error, or not in the way this integration understands."""


class BrainCallConnectionError(BrainCallError):
    """Brain Call cannot be reached."""


class BrainCallAuthError(BrainCallError):
    """Brain Call refused the token."""


class BrainCallRefusedError(BrainCallError):
    """Brain Call understood the request and refused it, e.g. a number that may not be dialed."""

    def __init__(self, status: int, reason: str | None) -> None:
        """Keep Brain Call's own reason, to be shown to the user."""
        super().__init__(f"Brain Call answered {status}" + (f": {reason}" if reason else ""))
        self.status = status
        self.reason = reason or f"HTTP {status}"


class BrainCallTooOldError(BrainCallError):
    """This Brain Call has no API for Brain Assist yet (older than 1.11)."""


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """What GET /api/version says about a Brain Call server."""

    api: int
    version: str
    assistant: str
    phone: bool
    home_assistant: bool
    tasks: bool = False  # planned calls are on (Brain Call 1.14.0 or newer says so)
    line: bool = False  # GET /api/status and the event braincall_state exist (Brain Call 1.19.0 or newer)
    messages: bool = False  # GET /api/messages and braincall_messages_updated exist (Brain Call 1.20.0 or newer)


def normalize_url(url: str) -> str:
    """The base URL as it is stored and compared: no trailing slash, http:// if no scheme was typed."""
    url = url.strip().rstrip("/")
    if url and "://" not in url:
        url = f"http://{url}"
    return url


class BrainCallClient:
    """Talks to one Brain Call server."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        """Remember where the server is and the token every request needs."""
        self._session = session
        self.url = normalize_url(url)
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _raise_for_status(self, resp: aiohttp.ClientResponse, ok: tuple[int, ...] = (200,)) -> None:
        if resp.status in ok:
            return
        if resp.status == 401:
            raise BrainCallAuthError("Brain Call refused the token")
        try:
            reason = (await resp.json(content_type=None)).get("error")
        except aiohttp.ClientError, ValueError, AttributeError:
            reason = None
        if 400 <= resp.status < 500:  # Brain Call understood the request and said no, with its reason
            raise BrainCallRefusedError(resp.status, str(reason) if reason else None)
        raise BrainCallError(f"Brain Call answered {resp.status}" + (f": {reason}" if reason else ""))

    async def async_version(self) -> ServerInfo:
        """Ask the server which API and version it has; also checks address and token."""
        try:
            async with self._session.get(f"{self.url}/api/version", headers=self._headers, timeout=TIMEOUT) as resp:
                if resp.status == 404:
                    raise BrainCallTooOldError("This Brain Call has no API for Brain Assist (needs 1.11.0 or newer)")
                await self._raise_for_status(resp)
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrainCallConnectionError(f"Brain Call cannot be reached at {self.url}: {err}") from err
        except ValueError as err:
            raise BrainCallError(f"{self.url} did not answer like Brain Call") from err
        if not isinstance(data, dict) or not isinstance(data.get("api"), int):
            raise BrainCallError(f"{self.url} did not answer like Brain Call")
        return ServerInfo(
            api=data["api"],
            version=str(data.get("version") or ""),
            assistant=str(data.get("assistant") or ""),
            phone=bool(data.get("phone")),
            home_assistant=bool(data.get("home_assistant")),
            tasks=data.get("tasks") is True,
            line=data.get("line") is True,
            messages=data.get("messages") is True,
        )

    async def async_health(self) -> dict[str, Any]:
        """GET /health: status (ok / unhealthy), problems, sip, busy, phone and, since 1.19.0, line.
        Brain Call answers 503 while unhealthy, with the same fields."""
        try:
            async with self._session.get(f"{self.url}/health", timeout=TIMEOUT) as resp:
                if resp.status not in (200, 503):
                    raise BrainCallError(f"Brain Call answered /health with {resp.status}")
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrainCallConnectionError(f"Brain Call cannot be reached at {self.url}: {err}") from err
        except ValueError as err:
            raise BrainCallError(f"{self.url} did not answer like Brain Call") from err
        if not isinstance(data, dict) or "status" not in data:
            raise BrainCallError(f"{self.url} did not answer like Brain Call")
        return data

    async def async_status(self) -> dict[str, Any]:
        """GET /api/status: line, sip, phone and the current call (number, caller_name, direction, since)."""
        data = await self._json("GET", "/api/status")
        if not isinstance(data, dict) or not isinstance(data.get("line"), str):
            raise BrainCallError("Brain Call sent no line state")
        return data

    async def _json(
        self, method: str, path: str, body: dict[str, Any] | None = None, limit: aiohttp.ClientTimeout = TIMEOUT
    ) -> Any:
        """One request to the API; the answer as JSON. Errors name what Brain Call said ("error")."""
        try:
            async with self._session.request(
                method, f"{self.url}{path}", headers=self._headers, json=body, timeout=limit
            ) as resp:
                await self._raise_for_status(resp, ok=(200, 201))
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrainCallConnectionError(f"Brain Call cannot be reached at {self.url}: {err}") from err
        except ValueError as err:
            raise BrainCallError(f"{self.url} did not answer like Brain Call") from err

    async def async_tasks(self) -> list[dict[str, Any]]:
        """All planned calls with their status and result (GET /api/tasks)."""
        data = await self._json("GET", "/api/tasks")
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, list):
            raise BrainCallError("Brain Call sent no list of tasks")
        return [t for t in tasks if isinstance(t, dict) and t.get("id")]

    async def async_plan_task(self, body: dict[str, Any]) -> dict[str, Any]:
        """Plan a call (POST /api/tasks); the stored task. A refusal is a BrainCallError with Brain Call's reason."""
        task = await self._json("POST", "/api/tasks", body, limit=PLAN_TIMEOUT)
        if not isinstance(task, dict) or not task.get("id"):
            raise BrainCallError("Brain Call did not answer with a task")
        return task

    async def async_cancel_task(self, task_id: str) -> None:
        """Cancel a planned call (DELETE /api/tasks/<id>); a running or finished one is refused (BrainCallError)."""
        await self._json("DELETE", f"/api/tasks/{quote(task_id, safe='')}")

    async def async_messages(self) -> dict[str, Any]:
        """The owner's messages (GET /api/messages): the calls Brain Call took for them, newest first, with the
        counts open, new and callbacks."""
        data = await self._json("GET", "/api/messages")
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise BrainCallError("Brain Call sent no list of messages")
        return {
            "messages": [m for m in data["messages"] if isinstance(m, dict) and m.get("id")],
            **{k: data.get(k) if isinstance(data.get(k), int) else 0 for k in ("open", "new", "callbacks")},
        }

    async def async_update_message(self, message_id: str, **change: bool) -> dict[str, Any]:
        """Set or clear heard and done of a message (PATCH /api/messages/<id>); the entry as it is now."""
        data = await self._json("PATCH", f"/api/messages/{quote(message_id, safe='')}", change)
        if not isinstance(data, dict) or not data.get("id"):
            raise BrainCallError("Brain Call did not answer with the message")
        return data

    async def async_delete_message(self, message_id: str) -> None:
        """Remove a message (DELETE /api/messages/<id>)."""
        await self._json("DELETE", f"/api/messages/{quote(message_id, safe='')}")

    async def async_converse(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Send one Assist request; yields the events of the answer as they arrive (JSON lines)."""
        try:
            async with self._session.post(
                f"{self.url}/api/converse", headers=self._headers, json=payload, timeout=STREAM_TIMEOUT
            ) as resp:
                if resp.status == 404:
                    raise BrainCallTooOldError("This Brain Call has no API for Brain Assist (needs 1.11.0 or newer)")
                await self._raise_for_status(resp)
                async for line in resp.content:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError as err:
                        raise BrainCallError("Brain Call sent an answer that is not valid JSON") from err
                    if isinstance(event, dict):
                        yield event
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BrainCallConnectionError(f"The connection to Brain Call failed: {err}") from err
