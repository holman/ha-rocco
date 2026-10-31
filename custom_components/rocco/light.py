"""Interior light."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RoccoConfigEntry, RoccoCoordinator
from .entity import RoccoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RoccoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(RoccoLight(coordinator, fid) for fid in coordinator.data)


class RoccoLight(RoccoEntity, LightEntity):
    """Lamp2 is the interior light. Brightness is read-only until its command is known."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str) -> None:
        super().__init__(coordinator, fridge_id, "light")

    @property
    def is_on(self) -> bool | None:
        return self.fridge.setting("Lamp2On")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        level = self.fridge.setting("Lamp2Brightness")
        return {"brightness_level": level.removeprefix("LampBrightness").lower() if level else None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(lambda iot: self.coordinator.api.set_lamp(iot, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(lambda iot: self.coordinator.api.set_lamp(iot, False))
