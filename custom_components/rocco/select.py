"""Cooling mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import MODES
from .coordinator import RoccoConfigEntry, RoccoCoordinator
from .entity import RoccoEntity

_TO_ENUM = {v: k for k, v in MODES.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: RoccoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(RoccoModeSelect(coordinator, fid) for fid in coordinator.data)


class RoccoModeSelect(RoccoEntity, SelectEntity):
    """Smart / Party / Holiday compressor mode."""

    _attr_options = list(MODES.values())

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str) -> None:
        super().__init__(coordinator, fridge_id, "cooling_mode")

    @property
    def current_option(self) -> str | None:
        return MODES.get(self.fridge.setting("CompressorControlMode"))

    async def async_select_option(self, option: str) -> None:
        await self._command(lambda iot: self.coordinator.api.set_mode(iot, _TO_ENUM[option]))
