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


def source_serial_number(hass, options):
    """Validate the selected serial sensor and return its current value."""
    registry = er.async_get(hass)
    serial = registry.async_get(options["serial_entity"])
    source_id = source_entry_id(hass, options)
    if (
        serial is None
        or serial.domain != "sensor"
        or serial.platform != SOURCE_DOMAIN
        or not serial.unique_id.endswith("serial_number")
        or serial.config_entry_id != source_id
    ):
        raise ValueError("Serial sensor must belong to the same bottle")
    state = hass.states.get(options["serial_entity"])
    if state is None or state.state in ("unknown", "unavailable", ""):
        raise ValueError("Bottle serial number is unavailable")
    return state.state.strip().upper()


def get_source(hass, options):
    source_id = source_entry_id(hass, options)
    source = hass.data.get(SOURCE_DOMAIN, {}).get(source_id)
    if source is None or not hasattr(source, "signal") or not hasattr(source.state, "sips"):
        raise ValueError("Supported BLE integration is not ready")
    return source
