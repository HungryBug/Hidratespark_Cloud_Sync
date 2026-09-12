"""Read-only adapter for bditter/HA-Hidratespark's paired sip ledger."""
from homeassistant.helpers import entity_registry as er
from .const import SOURCE_DOMAIN


def source_entry_id(hass, options):
    registry = er.async_get(hass)
    entries = [registry.async_get(options[key]) for key in ("time_entity", "volume_entity")]
    for item, key in zip(entries, ("last_sip_time", "last_sip_volume")):
        if item is None or item.domain != "sensor" or item.platform != SOURCE_DOMAIN or not item.unique_id.endswith(key):
            raise ValueError("Select the supported HidrateSpark sip sensors")
    if entries[0].config_entry_id != entries[1].config_entry_id:
        raise ValueError("Sensors must belong to the same bottle")
    return entries[0].config_entry_id


def get_source(hass, options):
    source_id = source_entry_id(hass, options)
    source = hass.data.get(SOURCE_DOMAIN, {}).get(source_id)
    if source is None or not hasattr(source, "signal") or not hasattr(source.state, "sips"):
        raise ValueError("Supported BLE integration is not ready")
    return source
