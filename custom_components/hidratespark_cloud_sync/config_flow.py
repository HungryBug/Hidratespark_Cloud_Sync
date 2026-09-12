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

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input:
            data = dict(user_input)
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
                data.update(session_token=client.token, user_id=client.user_id)
                return self.async_create_entry(title=data.pop("name"), data=data, options=options)
        schema = vol.Schema({vol.Required("name", default="HidrateSpark Home"): str,
            vol.Required("time_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ha_hidratespark")),
            vol.Required("volume_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ha_hidratespark")),
            vol.Required("serial_entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", integration="ha_hidratespark")),
            vol.Required("username"): str,
            vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Required("time_zone", default=self.hass.config.time_zone): str})
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
