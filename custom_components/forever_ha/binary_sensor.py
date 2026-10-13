from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN, SIGNAL
from .entity import ForeverEntity, metadata, values
from .sensor import pixel_available


async def async_setup_entry(hass, entry, async_add_entities):
    runtime, added = hass.data[DOMAIN], set()

    @callback
    def discover():
        entities = []
        for key, device in runtime.model.data["devices"].items():
            if not device["guid"] and (key, "connected") not in added:
                added.add((key, "connected"))
                entities.append(BridgeConnected(runtime, key))
            elif device["guid"] and "stats" in device["datasets"]:
                for field, name in [("dead", "Dead snapshot"), ("mounted", "Mounted snapshot"), ("combat", "In combat snapshot")]:
                    if (key, field) not in added:
                        added.add((key, field))
                        entities.append(SnapshotFlag(runtime, key, field, name))
            if device["guid"] and "live_state" in device["datasets"]:
                for field, name in [("dead", "Live dead"), ("mounted", "Live mounted"), ("combat", "Live in combat")]:
                    if (key, "live_" + field) not in added:
                        added.add((key, "live_" + field))
                        entities.append(LiveFlag(runtime, key, field, name))
        if entities:
            async_add_entities(entities)
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL, discover))
    discover()


class BridgeConnected(ForeverEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, runtime, key):
        super().__init__(runtime, key, "connected", "Companion connected")

    @property
    def is_on(self):
        return self.runtime.connected(self.device["installation"])


class SnapshotFlag(ForeverEntity, BinarySensorEntity):
    def __init__(self, runtime, key, field, name):
        self.field = field
        super().__init__(runtime, key, field + "_snapshot", name)

    @property
    def is_on(self):
        value = values(self.device["datasets"].get("stats")).get(self.field)
        return value if type(value) is bool else None

    @property
    def available(self):
        return self.is_on is not None and self.extra_state_attributes["collection_status"] in ("available", "partial", "restricted")

    @property
    def extra_state_attributes(self):
        return metadata(self.device["datasets"].get("stats"))


class LiveFlag(ForeverEntity, BinarySensorEntity):
    def __init__(self, runtime, key, field, name):
        self.field = field
        super().__init__(runtime, key, "live_" + field, name)

    @property
    def is_on(self):
        value = values(self.device["datasets"].get("live_state")).get(self.field)
        return value if type(value) is bool else None

    @property
    def available(self):
        return self.is_on is not None and pixel_available(self.runtime, self.device)

    @property
    def extra_state_attributes(self):
        return metadata(self.device["datasets"].get("live_state"))
