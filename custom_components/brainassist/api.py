"""Client for the HTTP API of Brain Call (the server behind Brain Assist)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=15)
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
        )

    async def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """One request to the API; the answer as JSON. Errors name what Brain Call said ("error")."""
        try:
            async with self._session.request(
                method, f"{self.url}{path}", headers=self._headers, json=body, timeout=TIMEOUT
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
        task = await self._json("POST", "/api/tasks", body)
        if not isinstance(task, dict) or not task.get("id"):
            raise BrainCallError("Brain Call did not answer with a task")
        return task

    async def async_cancel_task(self, task_id: str) -> None:
        """Cancel a planned call (DELETE /api/tasks/<id>); a running or finished one is refused (BrainCallError)."""
        await self._json("DELETE", f"/api/tasks/{quote(task_id, safe='')}")

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
