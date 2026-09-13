"""HidrateSpark Cloud Sync."""
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import HidrateSparkCloudClient
from .const import DOMAIN, PLATFORMS
from homeassistant.exceptions import ServiceValidationError
import voluptuous as vol
from .manager import SyncManager
from .source import get_source
from .health_bridge import async_get_bridge
from .http import register_views
from homeassistant.util import dt as dt_util
from homeassistant.core import SupportsResponse

async def async_setup(hass, config):
    bridge = await async_get_bridge(hass)
    register_views(hass, bridge)
    async def retry_failed(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
            raise ServiceValidationError("Cloud Sync entry is not loaded")
        manager = entry.runtime_data
        manager.pending.extend(manager.failed)
        manager.failed = []
        await manager.save()
        manager.wake.set()
        manager.changed()
    hass.services.async_register(DOMAIN, "retry_failed", retry_failed,
                                 schema=vol.Schema({vol.Required("entry_id"): str}))
    async def requeue_apple_health(call):
        def value(key):
            raw = call.data.get(key)
            return dt_util.parse_datetime(raw) if raw else None
        count = await bridge.async_requeue(value("start"), value("end"), call.data.get("bottle"))
        return {"requeued": count, "warning": "Requeueing may create duplicates; deduplicate by event id before writing HealthKit."}
    hass.services.async_register(DOMAIN, "requeue_apple_health", requeue_apple_health,
        schema=vol.Schema({vol.Optional("start"): str, vol.Optional("end"): str,
                           vol.Optional("bottle"): str}), supports_response=SupportsResponse.OPTIONAL)
    return True

async def async_setup_entry(hass, entry):
    try:
        get_source(hass, entry.options)
    except ValueError as err:
        raise ConfigEntryNotReady(str(err)) from err
    bridge = await async_get_bridge(hass)
    manager = SyncManager(hass, entry, HidrateSparkCloudClient(async_get_clientsession(hass), entry.data), bridge)
    entry.runtime_data = manager
    await manager.start()
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await manager.stop()
        raise
    return True

async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False
