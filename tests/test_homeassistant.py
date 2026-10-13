"""Runs against real HA 2026.10 when installed; portable jobs omit HA."""
import asyncio
import copy
import inspect
from types import SimpleNamespace
import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntries
from homeassistant.exceptions import Unauthorized
from custom_components.forever_ha import Runtime, ingest, ingest_chunk, heartbeat, get_dataset
from custom_components.forever_ha import sensor, binary_sensor
from custom_components.forever_ha.config_flow import ForeverHAConfigFlow
from custom_components.forever_ha.const import DOMAIN


class Connection:
    def __init__(self, admin=True):
        self.user = SimpleNamespace(is_admin=admin)
        self.results, self.errors = [], []

    def send_result(self, ident, result):
        self.results.append((ident, result))

    def send_error(self, *args):
        self.errors.append(args)


class Entry:
    def async_on_unload(self, callback):
        pass


def test_real_ha_storage_handlers_sensors_and_restore(tmp_path, env):
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        runtime = Runtime(hass, None)
        hass.data[DOMAIN] = runtime
        connection = Connection()
        await inspect.unwrap(ingest)(hass, connection, {"id": 1, "envelopes": [env()]})
        assert connection.results[0][1]["acknowledged"] == [1]
        restored = await runtime.store.async_load()
        assert restored["watermarks"]
        await inspect.unwrap(heartbeat)(hass, connection, {"id": 2, "installations": ["pc"],
                                                         "diagnostics": {"queued_records": 0, "dropped_records": 0}})
        assert runtime.connected("pc")
        await inspect.unwrap(get_dataset)(hass, connection, {"id": 3, "installation": "pc",
                     "character_guid": "Player-1-ABC", "account": "", "dataset": "character", "offset": 0, "limit": 200})
        assert connection.results[-1][1]["payload"]["payload"]["level"] == 60
        sensors, binaries = [], []
        await sensor.async_setup_entry(hass, Entry(), sensors.extend)
        await binary_sensor.async_setup_entry(hass, Entry(), binaries.extend)
        gold = next(e for e in sensors if e.name == "Gold")
        assert gold.native_value == 10
        assert gold.available
        assert gold.extra_state_attributes["snapshot_only"]
        assert any(e.name == "Companion connected" and e.is_on for e in binaries)
        assert gold.device_info["identifiers"]
        cold = Runtime(hass, restored)
        assert not cold.connected("pc")
        with pytest.raises(Unauthorized):
            ingest(hass, Connection(admin=False), {"id": 4, "envelopes": [env()]})
        await hass.async_block_till_done()
        await hass.async_stop()
    asyncio.run(scenario())


def test_no_ack_or_state_commit_when_storage_fails(tmp_path, env):
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        runtime = Runtime(hass, None)
        hass.data[DOMAIN] = runtime
        before = copy.deepcopy(runtime.model.data)
        class BrokenStore:
            async def async_save(self, data):
                raise OSError("disk failure")
        runtime.store = BrokenStore()
        connection = Connection()
        with pytest.raises(OSError):
            await inspect.unwrap(ingest)(hass, connection, {"id": 1, "envelopes": [env()]})
        assert not connection.results
        assert runtime.model.data == before
        await hass.async_stop()
    asyncio.run(scenario())


def test_live_sensor_disconnect_and_missing_secret_value(tmp_path, env):
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        runtime = Runtime(hass, None)
        runtime.model.apply(env(dataset="stats", payload={"status": "restricted", "payload": {"health_max": 100}}))
        key = next(iter(runtime.model.data["devices"]))
        health = sensor.ForeverSensor(runtime, key, ("stats", "health", "Health", None, False))
        assert health.native_value is None and not health.available
        runtime.model.apply(env(seq=2, dataset="combat_log", payload={"source_guid": "Player-1-ABC", "source_flags": 0x511,
                             "metric": "damage", "effective": 10}))
        damage = sensor.ForeverSensor(runtime, key, ("combat_statistics", "damage_done", "Damage", None, True))
        assert damage.native_value == 10 and not damage.available
        runtime.last_seen["pc"] = __import__("time").time()
        assert damage.available
        await hass.async_stop()
    asyncio.run(scenario())


def test_config_flow_form(tmp_path):
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        flow = ForeverHAConfigFlow()
        hass.config_entries = ConfigEntries(hass, {})
        flow.hass = hass
        flow.context = {"source": "user"}
        form = await flow.async_step_user()
        assert form["type"] == "form" and form["step_id"] == "user"
        result = await flow.async_step_user({})
        assert result["type"] == "create_entry"
        await hass.async_stop()
    asyncio.run(scenario())


def test_large_snapshot_fragmented_and_retried(tmp_path, env):
    from forever_ha_bridge.transport import Transport
    from forever_ha_bridge.store import Store
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        runtime = Runtime(hass, None)
        hass.data[DOMAIN] = runtime
        connection = Connection()
        calls = []
        class LocalTransport(Transport):
            fail = True
            async def command(self, kind, **data):
                calls.append(kind)
                await inspect.unwrap(ingest_chunk if kind.endswith("chunk") else ingest)(hass, connection, dict(data, id=len(calls)))
                if self.fail and data.get("index") == 2:
                    self.fail = False
                    raise OSError("connection interrupted")
                return connection.results[-1][1]
        bridge = LocalTransport("http://localhost", "test-token")
        store = Store(tmp_path / "queue.db")
        payload = {"status": "available", "payload": {"data": "x" * (3 * 1024 * 1024 + 100)}}
        store.ingest("snapshot", {"hash": "x"}, [env(dataset="large_snapshot", payload=payload)])
        with pytest.raises(OSError):
            await bridge.send(store)
        assert len(store.batch()) == 1
        await bridge.send(store)
        assert not store.batch()
        assert not connection.errors
        assert all(c == "forever_ha/ingest_chunk" for c in calls)
        assert runtime.model.get_dataset("pc", "Player-1-ABC", "large_snapshot")["payload"] == payload
        store.close()
        await hass.async_stop()
    asyncio.run(scenario())


def test_persistent_addon_event_dedup_after_restart(tmp_path, env):
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        runtime = Runtime(hass, None)
        hass.data[DOMAIN] = runtime
        emitted = []
        hass.bus.async_listen("forever_ha_event", lambda event: emitted.append(event.data))
        event = {"event": "PLAYER_DEAD", "session": "game-login", "sequence": 12, "observed_at": 100}
        first = env(dataset="addon_events", payload=event)
        first["source"] = "pixel"
        await inspect.unwrap(ingest)(hass, Connection(), {"id": 1, "envelopes": [first]})
        await hass.async_block_till_done()
        assert len(emitted) == 1
        restored = Runtime(hass, await runtime.store.async_load())
        hass.data[DOMAIN] = restored
        later = env(seq=2, dataset="addon_events", payload=event)
        await inspect.unwrap(ingest)(hass, Connection(), {"id": 2, "envelopes": [later]})
        await hass.async_block_till_done()
        assert len(emitted) == 1
        assert restored.model.data["addon_event_ranges"]
        await hass.async_stop()
    asyncio.run(scenario())


def test_live_state_entities_and_feed_freshness(tmp_path, env):
    import time
    async def scenario():
        hass = HomeAssistant(str(tmp_path))
        runtime = Runtime(hass, None)
        hass.data[DOMAIN] = runtime
        record = {"status": "restricted", "payload": {"health": None, "dead": False, "money_copper": 100000}}
        runtime.model.apply(env(dataset="live_state", payload=record))
        connection = Connection()
        diagnostics = {"pixel_feed": {"window": "foreground", "last_seen": time.time(), "character_guid": "Player-1-ABC"}}
        await inspect.unwrap(heartbeat)(hass, connection, {"id": 1, "installations": ["pc"], "diagnostics": diagnostics})
        sensors, binaries = [], []
        await sensor.async_setup_entry(hass, Entry(), sensors.extend)
        await binary_sensor.async_setup_entry(hass, Entry(), binaries.extend)
        gold = next(e for e in sensors if e.name == "Live gold")
        health = next(e for e in sensors if e.name == "Live health")
        dead = next(e for e in binaries if e.name == "Live dead")
        assert gold.available and gold.native_value == 10
        assert not health.available and health.native_value is None
        assert dead.available and dead.is_on is False
        feed = runtime.model.data["bridges"]["pc"]["pixel_feed"]
        feed["character_guid"] = "Player-1-OTHER"
        assert not gold.available and not dead.available
        feed["character_guid"] = "Player-1-ABC"
        feed["last_seen"] = time.time() - 20
        assert not gold.available
        await hass.async_stop()
    asyncio.run(scenario())
