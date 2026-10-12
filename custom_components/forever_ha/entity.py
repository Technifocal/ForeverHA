import time
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity
from .const import DOMAIN, SIGNAL
from .model import entity_prefix


def values(record):
    if not record:
        return {}
    payload = record.get("payload", {})
    if isinstance(payload, dict) and "status" in payload:
        return payload.get("payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


class ForeverEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime, key, suffix, name):
        self.runtime, self.key = runtime, key
        self._attr_unique_id = entity_prefix(key) + "_" + suffix
        self._attr_name = name

    @property
    def device(self):
        return self.runtime.model.data["devices"][self.key]

    @property
    def device_info(self):
        device = self.device
        name = device.get("name", "WoW Forever")
        if device.get("realm"):
            name += " – " + device["realm"]
        return DeviceInfo(identifiers={(DOMAIN, self.key)}, name=name,
                          manufacturer="Blizzard Entertainment", model="WoW Forever character" if device["guid"] else "ForeverHA companion",
                          sw_version="0.1.0")

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, self.async_write_ha_state))


def metadata(record):
    if not record:
        return {"collection_status": "unobserved"}
    payload = record.get("payload", {})
    status = payload.get("status", record.get("status", "available")) if isinstance(payload, dict) else "available"
    observed = record.get("observed_at")
    return {"collection_status": status, "observed_at": observed, "received_at": record.get("received_at"),
            "age_seconds": max(0, int(time.time() - observed)) if observed else None,
            "source": record.get("source"), "snapshot_only": record.get("source") == "saved_variables"}
