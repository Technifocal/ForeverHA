"""Authenticated local push integration. Persist data before acknowledging it."""
import asyncio
import copy
import json
import time
import base64
import binascii

from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from datetime import timedelta
import voluptuous as vol

from .const import DOMAIN, PLATFORMS, SIGNAL
from .model import Model, validate


class Runtime:
    def __init__(self, hass, data):
        self.model = Model(data)
        self.store = Store(hass, 1, DOMAIN)
        self.lock = asyncio.Lock()
        self.last_seen = {}  # Heartbeats restored from disk are not proof of connectivity.
        self.uploads = {}

    def connected(self, installation):
        return time.time() - self.last_seen.get(installation, 0) < 35


async def async_setup(hass, config):
    websocket_api.async_register_command(hass, ingest)
    websocket_api.async_register_command(hass, heartbeat)
    websocket_api.async_register_command(hass, get_dataset)
    websocket_api.async_register_command(hass, ingest_chunk)
    return True


async def async_setup_entry(hass, entry):
    store = Store(hass, 1, DOMAIN)
    runtime = Runtime(hass, await store.async_load())
    hass.data[DOMAIN] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def tick(now):
        async_dispatcher_send(hass, SIGNAL)
    entry.async_on_unload(async_track_time_interval(hass, tick, timedelta(seconds=10)))
    return True


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        runtime = hass.data.pop(DOMAIN)
        await runtime.store.async_save(runtime.model.data)
        return True
    return False


def runtime_or_error(hass, connection, msg):
    runtime = hass.data.get(DOMAIN)
    if not runtime:
        connection.send_error(msg["id"], "not_configured", "Add the WoW Forever integration first")
    return runtime


@websocket_api.websocket_command({vol.Required("type"): "forever_ha/ingest", vol.Required("envelopes"): list})
@websocket_api.require_admin
@websocket_api.async_response
async def ingest(hass, connection, msg):
    await _ingest(hass, connection, msg)


async def _ingest(hass, connection, msg):
    runtime = runtime_or_error(hass, connection, msg)
    if not runtime:
        return
    batch = msg["envelopes"]
    if not 1 <= len(batch) <= 50:
        connection.send_error(msg["id"], "invalid_payload", "Batch must contain 1–50 envelopes")
        return
    try:
        for envelope in batch:
            validate(envelope)
    except (ValueError, TypeError, KeyError):
        connection.send_error(msg["id"], "invalid_payload", "Invalid envelope")
        return
    async with runtime.lock:
        candidate = Model(copy.deepcopy(runtime.model.data))
        events, results = [], []
        for envelope in batch:
            result, emitted = candidate.apply(envelope)
            results.append(result)
            events.extend(emitted)
        if len(json.dumps(candidate.data).encode()) > 128 * 1024 * 1024:
            connection.send_error(msg["id"], "storage_limit", "Integration storage limit reached; export and remove old data")
            return
        await runtime.store.async_save(candidate.data)
        runtime.model = candidate
    for event in events:
        # Event records are bounded by the file parser, not embedded in entity attributes.
        hass.bus.async_fire("forever_ha_event", event)
    async_dispatcher_send(hass, SIGNAL)
    connection.send_result(msg["id"], {"acknowledged": [e["sequence"] for e in batch], "results": results})


@websocket_api.websocket_command({vol.Required("type"): "forever_ha/ingest_chunk",
                                 vol.Required("upload_id"): str, vol.Required("index"): int,
                                 vol.Required("count"): int, vol.Required("data"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ingest_chunk(hass, connection, msg):
    runtime = runtime_or_error(hass, connection, msg)
    if not runtime:
        return
    now = time.monotonic()
    runtime.uploads = {k: v for k, v in runtime.uploads.items() if now - v["touched"] < 60}
    upload_id, index, count = msg["upload_id"], msg["index"], msg["count"]
    try:
        if len(upload_id) != 64 or not 1 <= count <= 17 or not 0 <= index < count or len(msg["data"]) > 350000:
            raise ValueError("Invalid fragment metadata")
        piece = base64.b64decode(msg["data"], validate=True)
        if len(piece) > 256 * 1024:
            raise ValueError("Fragment too large")
        key = (getattr(connection.user, "id", "admin"), upload_id)
        if index == 0:
            if key not in runtime.uploads and len(runtime.uploads) >= 4:
                raise ValueError("Too many incomplete uploads")
            runtime.uploads[key] = {"parts": [], "count": count, "touched": now, "bytes": 0}
        upload = runtime.uploads.get(key)
        if not upload or upload["count"] != count or len(upload["parts"]) != index:
            raise ValueError("Fragment out of order")
        upload["parts"].append(piece)
        upload["bytes"] += len(piece)
        upload["touched"] = now
        if upload["bytes"] > 4 * 1024 * 1024:
            runtime.uploads.pop(key, None)
            raise ValueError("Upload too large")
        if index + 1 < count:
            connection.send_result(msg["id"], {"stored": index})
            return
        runtime.uploads.pop(key, None)
        envelope = json.loads(b"".join(upload["parts"]))
    except (ValueError, TypeError, binascii.Error):
        connection.send_error(msg["id"], "invalid_payload", "Invalid or expired upload fragment")
        return
    await _ingest(hass, connection, {"id": msg["id"], "envelopes": [envelope]})


@websocket_api.websocket_command({vol.Required("type"): "forever_ha/heartbeat",
                                 vol.Required("installations"): [str], vol.Required("diagnostics"): dict})
@websocket_api.require_admin
@websocket_api.async_response
async def heartbeat(hass, connection, msg):
    runtime = runtime_or_error(hass, connection, msg)
    if not runtime:
        return
    if not 1 <= len(msg["installations"]) <= 100 or any(not i or len(i) > 100 for i in msg["installations"]):
        connection.send_error(msg["id"], "invalid_payload", "Invalid installation identifiers")
        return
    async with runtime.lock:
        runtime.model.heartbeat(msg["installations"], msg["diagnostics"])
        for installation in msg["installations"]:
            runtime.last_seen[installation] = time.time()
    async_dispatcher_send(hass, SIGNAL)
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command({vol.Required("type"): "forever_ha/get_dataset",
                                 vol.Required("installation"): str,
                                 vol.Optional("character_guid", default=None): vol.Any(str, None),
                                 vol.Optional("account", default=""): str,
                                 vol.Required("dataset"): str,
                                 vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0)),
                                 vol.Optional("limit", default=200): vol.All(int, vol.Range(min=1, max=2000))})
@websocket_api.require_admin
@websocket_api.async_response
async def get_dataset(hass, connection, msg):
    runtime = runtime_or_error(hass, connection, msg)
    if runtime:
        connection.send_result(msg["id"], runtime.model.get_dataset(msg["installation"], msg["character_guid"],
                                                                 msg["dataset"], msg["account"], msg["offset"], msg["limit"]))
