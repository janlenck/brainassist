"""Client for the HTTP API of Brain Call (the server behind Brain Assist)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any

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

    async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status == 200:
            return
        if resp.status == 401:
            raise BrainCallAuthError("Brain Call refused the token")
        try:
            reason = (await resp.json(content_type=None)).get("error")
        except aiohttp.ClientError, ValueError, AttributeError:
            reason = None
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
        )

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
