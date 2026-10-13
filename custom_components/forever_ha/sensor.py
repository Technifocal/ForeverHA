import time
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN, SIGNAL
from .entity import ForeverEntity, metadata, values

# Dataset, field, display name, unit, live-only.
METRICS = [
    ("live_state", "health", "Live health", None, True),
    ("live_state", "health_max", "Live maximum health", None, True),
    ("live_state", "power", "Live power", None, True),
    ("live_state", "power_max", "Live maximum power", None, True),
    ("live_state", "level", "Live level", None, True),
    ("live_state", "money_copper", "Live gold", "gold", True),
    ("live_state", "xp", "Live experience", "XP", True),
    ("live_state", "xp_max", "Live experience needed", "XP", True),
    ("live_state", "rested_xp", "Live rested experience", "XP", True),
    ("live_state", "zone", "Live zone", None, True),
    ("live_state", "subzone", "Live subzone", None, True),
    ("live_state", "dropped_pixel_packets", "Dropped screen packets", None, True),
    ("character", "level", "Level", None, False),
    ("character", "xp", "Experience", "XP", False),
    ("character", "xp_max", "Experience needed", "XP", False),
    ("character", "rested_xp", "Rested experience", "XP", False),
    ("character", "money_copper", "Gold", "gold", False),
    ("location", "zone", "Zone", None, False),
    ("location", "subzone", "Subzone", None, False),
    ("stats", "health", "Health snapshot", None, False),
    ("stats", "health_max", "Maximum health snapshot", None, False),
    ("stats", "power", "Power snapshot", None, False),
    ("stats", "power_max", "Maximum power snapshot", None, False),
    ("stats", "crit", "Critical strike", "%", False),
    ("stats", "haste", "Haste", "%", False),
    ("combat_statistics", "damage_done", "Logged damage done", None, True),
    ("combat_statistics", "damage_taken", "Logged damage taken", None, True),
    ("combat_statistics", "healing_done", "Logged effective healing done", None, True),
    ("combat_statistics", "healing_taken", "Logged effective healing received", None, True),
    ("combat_statistics", "deaths", "Logged deaths", None, True),
    ("combat_statistics", "casts", "Logged successful casts", None, True),
    ("combat_statistics", "interrupts", "Logged interrupts", None, True),
    ("combat_statistics", "unparsed_statistic_records", "Unparsed combat statistics", None, True),
    ("export_status", "dropped_addon_events", "Dropped addon events", None, False),
    ("last_encounter", "dps", "Last encounter logged DPS", None, False),
    ("last_encounter", "hps", "Last encounter logged HPS", None, False),
    ("last_encounter", "duration", "Last encounter duration", "s", False),
    ("encounter_statistics", "encounters_started", "Logged encounters started", None, True),
    ("encounter_statistics", "encounters_completed", "Logged encounters completed", None, True),
    ("encounter_statistics", "encounters_won", "Logged encounters won", None, True),
    ("encounter_statistics", "encounters_failed", "Logged encounters failed", None, True),
    ("encounter_statistics", "active_encounter", "Logged active encounter", None, True),
    ("encounter_statistics", "last_encounter", "Last logged encounter", None, False),
    ("encounter_statistics", "last_duration", "Last logged encounter duration", "s", False),
]


async def async_setup_entry(hass, entry, async_add_entities):
    runtime, added = hass.data[DOMAIN], set()

    @callback
    def discover():
        entities = []
        for key, device in runtime.model.data["devices"].items():
            for metric in METRICS:
                suffix = metric[0] + "_" + metric[1]
                if (key, suffix) not in added and metric[0] in device["datasets"]:
                    added.add((key, suffix))
                    entities.append(ForeverSensor(runtime, key, metric))
            if device["guid"]:
                if (key, "snapshot_age") not in added:
                    added.add((key, "snapshot_age"))
                    entities.append(SnapshotAge(runtime, key))
            elif (key, "queue") not in added:
                added.add((key, "queue"))
                entities.extend([BridgeSensor(runtime, key, field, name) for field, name in
                                 [("queued_records", "Queued records"), ("dropped_records", "Dropped bridge records")]])
        if entities:
            async_add_entities(entities)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL, discover))
    discover()


class ForeverSensor(ForeverEntity, SensorEntity):
    def __init__(self, runtime, key, metric):
        self.dataset, self.field, name, unit, self.live = metric
        super().__init__(runtime, key, self.dataset + "_" + self.field, name)
        self._attr_native_unit_of_measurement = unit

    @property
    def record(self):
        return self.device["datasets"].get(self.dataset)

    @property
    def native_value(self):
        value = values(self.record).get(self.field)
        if self.field == "money_copper" and type(value) in (int, float):
            return value / 10000
        return value if type(value) in (str, int, float) else None

    @property
    def available(self):
        status = metadata(self.record)["collection_status"]
        return (status in ("available", "partial", "restricted") and self.native_value is not None and
                (pixel_available(self.runtime, self.device) if self.dataset == "live_state" else
                 (not self.live or self.runtime.connected(self.device["installation"]))))

    @property
    def extra_state_attributes(self):
        return metadata(self.record)


class SnapshotAge(ForeverEntity, SensorEntity):
    _attr_native_unit_of_measurement = "s"

    def __init__(self, runtime, key):
        super().__init__(runtime, key, "snapshot_age", "Character snapshot age")

    @property
    def native_value(self):
        record = self.device["datasets"].get("character")
        stamp = record.get("observed_at") if record else None
        return max(0, int(time.time() - stamp)) if stamp else None


class BridgeSensor(ForeverEntity, SensorEntity):
    def __init__(self, runtime, key, field, name):
        self.field = field
        super().__init__(runtime, key, field, name)

    @property
    def native_value(self):
        return self.runtime.model.data["bridges"].get(self.device["installation"], {}).get(self.field)

    @property
    def available(self):
        return self.runtime.connected(self.device["installation"])

    @property
    def extra_state_attributes(self):
        bridge = self.runtime.model.data["bridges"].get(self.device["installation"], {})
        return {"file_errors": bridge.get("file_errors", [])[:20], "last_heartbeat": bridge.get("last_heartbeat"),
                "pixel_feed": bridge.get("pixel_feed", {})}


def pixel_available(runtime, device):
    feed = runtime.model.data["bridges"].get(device["installation"], {}).get("pixel_feed", {})
    seen = feed.get("last_seen")
    return bool(runtime.connected(device["installation"]) and feed.get("window") == "foreground" and
                feed.get("character_guid") == device["guid"] and seen is not None and time.time() - seen < 15)
