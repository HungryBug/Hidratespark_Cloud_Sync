"""Per-bottle diagnostic entities."""
from datetime import datetime
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(SyncSensor(entry, key) for key in ("status", "pending", "last_success"))

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
