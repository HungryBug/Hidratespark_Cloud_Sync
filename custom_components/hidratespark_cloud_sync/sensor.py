"""Per-bottle diagnostic entities."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN
from .const import BRIDGE_SIGNAL
from .health_bridge import async_get_bridge

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(SyncSensor(entry, key) for key in ("status", "pending", "last_success"))
    bridge = await async_get_bridge(hass)
    if bridge.sensor_owner is None:
        bridge.sensor_owner = entry.entry_id
    if bridge.sensor_owner == entry.entry_id:
        async_add_entities(BridgeSensor(bridge, key) for key in (
            "bridge_status", "bridge_pending", "bridge_last_success",
            "bridge_synced_today", "bridge_oldest_pending"))

class SyncSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry, key):
        self.manager, self.key = entry.runtime_data, key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title, "manufacturer": "HidrateSpark"}
        if key == "last_success":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, self.manager.signal, self.async_write_ha_state))

    @property
    def native_value(self):
        if self.key == "status":
            return self.manager.status
        if self.key == "pending":
            return len(self.manager.pending)
        return datetime.fromisoformat(self.manager.last_success) if self.manager.last_success else None

    @property
    def extra_state_attributes(self):
        if self.key == "status":
            return {"last_error": self.manager.last_error, "failed_uploads": len(self.manager.failed)}
        return None


class BridgeSensor(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, bridge, key):
        self.bridge, self.key = bridge, key
        self._attr_unique_id = f"{DOMAIN}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {"identifiers": {(DOMAIN, "apple_health_bridge")},
            "name": "Apple Health Bridge", "manufacturer": "HidrateSpark"}
        if key in ("bridge_last_success", "bridge_oldest_pending"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, BRIDGE_SIGNAL, self.async_write_ha_state))

    @property
    def native_value(self):
        data = self.bridge.status_data()
        if self.key == "bridge_status": return data["status"]
        if self.key == "bridge_pending": return data["pending"]
        if self.key == "bridge_last_success":
            return datetime.fromisoformat(data["last_successful_sync"]) if data["last_successful_sync"] else None
        if self.key == "bridge_oldest_pending":
            return datetime.fromisoformat(data["oldest_pending"]) if data["oldest_pending"] else None
        today = datetime.now().astimezone().date()
        return sum(1 for record in self.bridge.records.values()
                   if record["apple_health_status"] == "synced"
                   and record["apple_health_synced_at"]
                   and datetime.fromisoformat(record["apple_health_synced_at"]).astimezone().date() == today)
