"""Mirror one Switch room's messages into Switchboard using long polling."""

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("switchboard.switch_adapter")

SWITCH_ROOM_ID = os.getenv("SWITCH_ROOM_ID", "")
SWITCH_CREDENTIALS_FILE = Path(os.getenv("SWITCH_CREDENTIALS_FILE", "/run/switch-credentials/credentials.json"))
# A container's localhost is the container itself. Docker Desktop exposes host
# services at host.docker.internal; this override takes precedence over the
# endpoint recorded by Switch Console (which commonly uses localhost).
SWITCH_BASE_URL_OVERRIDE = os.getenv("SWITCH_BASE_URL", "").rstrip("/")
SWITCHBOARD_API_URL = os.getenv("SWITCHBOARD_API_URL", "http://api:8000/api/v1").rstrip("/")
ADAPTER_RETRY_SECONDS = float(os.getenv("SWITCH_ADAPTER_RETRY_SECONDS", "5"))
POLL_SECONDS = float(os.getenv("SWITCH_ADAPTER_POLL_SECONDS", "5"))


@dataclass(frozen=True)
class SwitchCredentials:
    base_url: str
    agent_id: str
    token: str


def load_credentials() -> SwitchCredentials | None:
    try:
        raw = json.loads(SWITCH_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    # Switch Console stores agent transport credentials in the `env` object
    # alongside provider metadata; accept a flat shape as well for standalone
    # protocol clients.
    values = raw.get("env", raw)
    if isinstance(values, dict) and isinstance(values.get("credentials"), dict):
        values = values["credentials"]
    if not isinstance(values, dict):
        return None
    base_url = SWITCH_BASE_URL_OVERRIDE or str(values.get("SWITCH_API_ENDPOINT") or values.get("api_endpoint") or "")
    agent_id = str(values.get("SWITCH_AGENT_ID") or values.get("agent_id") or "")
    token = str(values.get("SWITCH_API_TOKEN") or values.get("api_token") or "")
    if not all((base_url, agent_id, token, SWITCH_ROOM_ID)):
        return None
    # Switch Console writes a loopback URL for its local relay into the agent
    # credentials. This adapter runs in Docker, so route that same relay port
    # through Docker Desktop's host alias instead of overriding it with Core's
    # unrelated API port.
    parsed = urlsplit(base_url)
    if not SWITCH_BASE_URL_OVERRIDE and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        host = "host.docker.internal"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        base_url = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    return SwitchCredentials(base_url.rstrip("/"), agent_id, token)


async def mirror_event(client: httpx.AsyncClient, config: SwitchCredentials, kind: str, raw_data: str) -> bool:
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        log.warning("Ignoring non-JSON Switch event")
        return True
    if not isinstance(data, dict):
        return True
    data.setdefault("type", kind)
    try:
        response = await client.post(
            f"{SWITCHBOARD_API_URL}/switch/events",
            json={"event": data, "adapter_agent_id": config.agent_id},
        )
        response.raise_for_status()
        outcome = response.json()
        if outcome.get("created") or outcome.get("kind"):
            log.info("Mirrored Switch message: %s", outcome)
        return True
    except httpx.HTTPError as exc:
        log.exception("Could not mirror Switch event: %s", exc)
        return False


def history_message_id(room_id: str, message: dict) -> str:
    """Build a stable deduplication key; history omits Switch message IDs."""
    identity = json.dumps(
        [room_id, message.get("sender"), message.get("sender_name"), message.get("timestamp"), message.get("body")],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "history:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def poll_history(client: httpx.AsyncClient, config: SwitchCredentials) -> int:
    endpoint = f"{config.base_url}/agents/{config.agent_id}/rooms/{SWITCH_ROOM_ID}/history"
    response = await client.get(endpoint, timeout=15)
    response.raise_for_status()
    body = response.json()
    messages = body.get("events", []) if isinstance(body, dict) else []
    mirrored = 0
    for message in messages:
        if not isinstance(message, dict) or not str(message.get("body") or "").strip():
            continue
        event = {
            "type": "message",
            "room_id": SWITCH_ROOM_ID,
            "payload": {**message, "message_id": history_message_id(SWITCH_ROOM_ID, message)},
        }
        if not await mirror_event(client, config, "message", json.dumps(event)):
            log.warning("Pausing history polling to retry after an API error")
            break
        mirrored += 1
    return mirrored


async def main():
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=15, write=10, pool=10),
    ) as client:
        while True:
            config = load_credentials()
            if config is None:
                log.info("Switch adapter idle; create the credential-file bind mount and set SWITCH_ROOM_ID")
                await asyncio.sleep(10)
                continue
            client.headers["Authorization"] = f"Bearer {config.token}"
            try:
                mirrored = await poll_history(client, config)
                if mirrored:
                    log.info("Checked %s Switch history messages", mirrored)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    log.error(
                        "Switch rejected the adapter credential for room history (HTTP %s). Stopping.",
                        status,
                    )
                    return
                if status == 429:
                    retry_after = exc.response.headers.get("Retry-After", "")
                    try:
                        delay = max(30.0, float(retry_after))
                    except ValueError:
                        delay = max(30.0, ADAPTER_RETRY_SECONDS)
                    log.warning("Switch rate limited the adapter; retrying in %.0f seconds", delay)
                    await asyncio.sleep(delay)
                    continue
                log.warning("Switch connection failed with HTTP %s", status)
            except httpx.HTTPError as exc:
                log.warning("Switch connection failed: %s", exc)
            await asyncio.sleep(POLL_SECONDS if POLL_SECONDS > 0 else ADAPTER_RETRY_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
