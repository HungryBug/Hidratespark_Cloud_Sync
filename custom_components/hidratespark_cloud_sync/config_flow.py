"""UI setup and reauthentication."""
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .api import HidrateSparkCloudClient, AuthenticationError, CloudError
from .const import DOMAIN
from .source import get_source, source_serial_number
from .health_bridge import async_get_bridge

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        bridge = await async_get_bridge(self.hass)
        errors = {}
        if user_input:
            data = dict(user_input)
            bridge_token = data.pop("apple_health_token")
            bridge_enabled = data.pop("apple_health_enabled")
            debounce = data.pop("apple_health_debounce")
            options = {key: data.pop(key) for key in ("time_entity", "volume_entity", "serial_entity")}
            data["installation_id"] = str(uuid.uuid4())
            try:
                ZoneInfo(data["time_zone"])
                source = get_source(self.hass, options)
                data["bottle_serial"] = source_serial_number(self.hass, options)
                if not source.serial_number or source.serial_number.upper() != data["bottle_serial"]:
                    raise ValueError("Serial does not match source")
                await self.async_set_unique_id(data["bottle_serial"])
                self._abort_if_unique_id_configured()
                client = HidrateSparkCloudClient(async_get_clientsession(self.hass), data)
                await client.async_authenticate()
            except (ValueError, ZoneInfoNotFoundError):
                errors["base"] = "invalid_source"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                await bridge.async_configure(bridge_token, bridge_enabled, debounce)
                data.update(session_token=client.token, user_id=client.user_id)
                return self.async_create_entry(title=data.pop("name"), data=data, options=options)
        schema = vol.Schema({vol.Required("name", default="HidrateSpark Home"): str,
            vol.Required("time_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ha_hidratespark")),
            vol.Required("volume_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ha_hidratespark")),
            vol.Required("serial_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ha_hidratespark")),
            vol.Required("username"): str,
            vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Required("time_zone", default=self.hass.config.time_zone): str,
            vol.Required("apple_health_token", default=bridge.token): str,
            vol.Required("apple_health_enabled", default=bridge.enabled): bool,
            vol.Required("apple_health_debounce", default=bridge.debounce_seconds): vol.All(int, vol.Range(min=1, max=300))})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input:
            data = {**entry.data, **user_input}
            client = HidrateSparkCloudClient(async_get_clientsession(self.hass), data)
            try:
                await client.async_authenticate()
                if client.user_id != entry.data["user_id"]:
                    raise AuthenticationError("Different account")
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                data.update(session_token=client.token, user_id=client.user_id)
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(step_id="reauth_confirm", errors=errors, data_schema=vol.Schema({
            vol.Required("username", default=entry.data["username"]): str,
            vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))}))

    async def async_step_reconfigure(self, user_input=None):
        return self.async_show_menu(step_id="reconfigure", menu_options=["bridge_settings", "bridge_regenerate"])

    async def async_step_bridge_settings(self, user_input=None):
        bridge = await async_get_bridge(self.hass)
        errors = {}
        if user_input:
            try:
                await bridge.async_configure(user_input["token"], user_input["enabled"], user_input["debounce_seconds"])
            except ValueError:
                errors["base"] = "invalid_token"
            else:
                return self.async_abort(reason="bridge_updated")
        return self.async_show_form(step_id="bridge_settings", errors=errors, data_schema=vol.Schema({
            vol.Required("token", default=bridge.token): str,
            vol.Required("enabled", default=bridge.enabled): bool,
            vol.Required("debounce_seconds", default=bridge.debounce_seconds): vol.All(int, vol.Range(min=1, max=300)),
        }))
    async def async_step_bridge_regenerate(self, user_input=None):
        bridge = await async_get_bridge(self.hass)
        if not hasattr(self, "_new_bridge_token"):
            import secrets
            self._new_bridge_token = secrets.token_urlsafe(32)
        if user_input:
            if not user_input.get("confirm"):
                return self.async_show_form(step_id="bridge_regenerate", errors={"base": "confirmation_required"},
                    data_schema=vol.Schema({vol.Required("new_token", default=self._new_bridge_token): str,
                                            vol.Required("confirm", default=False): bool}))
            await bridge.async_configure(self._new_bridge_token, bridge.enabled, bridge.debounce_seconds)
            return self.async_abort(reason="bridge_regenerated")
        return self.async_show_form(step_id="bridge_regenerate", data_schema=vol.Schema({
            vol.Required("new_token", default=self._new_bridge_token): str,
            vol.Required("confirm", default=False): bool,
        }))
