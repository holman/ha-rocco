"""Zone temperature setpoints."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import UNIT_C, UNIT_F, ZONES
from .coordinator import RoccoConfigEntry, RoccoCoordinator
from .entity import RoccoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RoccoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        RoccoZoneSetpoint(coordinator, fid, zone) for fid in coordinator.data for zone in ZONES
    )


class RoccoZoneSetpoint(RoccoEntity, NumberEntity):
    """Target temperature for the upper or lower zone."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str, zone: str) -> None:
        super().__init__(coordinator, fridge_id, f"{zone}_zone_setpoint")
        self.zone = zone

    @property
    def _celsius(self) -> bool:
        return self.fridge.reported.get("UnitTemperature") == UNIT_C

    def _field(self, zone: str) -> str:
        return f"{zone.capitalize()}ZoneSetTemp{'C' if self._celsius else 'F'}"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS if self._celsius else UnitOfTemperature.FAHRENHEIT

    @property
    def native_min_value(self) -> float:
        return ZONES[self.zone]["c" if self._celsius else "f"][0]

    @property
    def native_max_value(self) -> float:
        return ZONES[self.zone]["c" if self._celsius else "f"][1]

    @property
    def native_value(self) -> float | None:
        return self.fridge.setting(self._field(self.zone))

    async def async_set_native_value(self, value: float) -> None:
        other = self.fridge.setting(self._field("lower" if self.zone == "upper" else "upper"))
        if other is not None:
            upper, lower = (value, other) if self.zone == "upper" else (other, value)
            if lower < upper:
                raise ServiceValidationError(
                    f"The lower zone must be at least as warm as the upper zone (upper {upper}, lower {lower})."
                )
        unit = UNIT_C if self._celsius else UNIT_F
        await self._command(lambda iot: self.coordinator.api.set_temp(iot, self.zone, value, unit))
