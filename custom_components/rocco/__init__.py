"""The Rocco Fridge integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RoccoApi
from .const import CONF_CLIENT_TOKEN, CONF_DEVICE_ID, CONF_IDENTITY_ID
from .coordinator import RoccoConfigEntry, RoccoCoordinator
from .iot import RoccoIot

PLATFORMS = [Platform.BINARY_SENSOR, Platform.LIGHT, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: RoccoConfigEntry) -> bool:
    """Set up Rocco from a config entry."""

    @callback
    def _save_rotated_token(token: str) -> None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_CLIENT_TOKEN: token})

    api = RoccoApi(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_TOKEN],
        entry.data[CONF_DEVICE_ID],
        on_client_token=_save_rotated_token,
    )
    coordinator = RoccoCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if identity_id := entry.options.get(CONF_IDENTITY_ID):
        session = async_get_clientsession(hass)
        for fridge_id, fridge in coordinator.data.items():
            client = RoccoIot(
                session,
                identity_id,
                fridge.info["IOTID"],
                lambda state, fid=fridge_id: coordinator.apply_iot_state(fid, state),
            )
            # Config-entry background tasks are cancelled on unload.
            entry.async_create_background_task(hass, client.run_forever(), f"rocco_iot_{fridge_id}")

    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: RoccoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: RoccoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
