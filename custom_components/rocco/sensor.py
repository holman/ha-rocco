"""Inventory and alert sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_IDENTITY_ID, UNIT_C
from .coordinator import RoccoConfigEntry, RoccoCoordinator
from .entity import RoccoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: RoccoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for fid, fridge in coordinator.data.items():
        entities.append(RoccoItemCount(coordinator, fid, None))
        entities.append(RoccoAlert(coordinator, fid))
        for rack in _racks(fridge.contents):
            entities.append(RoccoItemCount(coordinator, fid, rack["num"]))
        # These come only from the live AWS IoT connection.
        if entry.options.get(CONF_IDENTITY_ID):
            entities += [
                RoccoTemperature(coordinator, fid, "upper_zone_temperature", "UpperZoneCurrentTemp"),
                RoccoTemperature(coordinator, fid, "lower_zone_temperature", "LowerZoneCurrentTemp"),
                RoccoTemperature(coordinator, fid, "ambient_temperature", "AmbientTemperature"),
                RoccoTemperature(coordinator, fid, "evaporator_temperature", "EvapZoneTemperature",
                                 diagnostic=True),
                RoccoWifiSignal(coordinator, fid),
            ]
    async_add_entities(entities)


def _racks(contents: dict[str, Any] | None) -> list[dict[str, Any]]:
    return ((contents or {}).get("Inventory") or {}).get("racks") or []


def _label(item: dict[str, Any]) -> str:
    product = item.get("product") or {}
    return " ".join(p for p in (product.get("title"), product.get("subTitle")) if p) or "Unknown"


class RoccoItemCount(RoccoEntity, SensorEntity):
    """Drinks in the whole fridge, or on one rack."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "items"

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str, rack: int | None) -> None:
        super().__init__(coordinator, fridge_id, "items" if rack is None else f"rack_{rack}_items")
        self.rack = rack
        if rack is not None:
            self._attr_translation_key = "rack_items"
            self._attr_translation_placeholders = {"rack": str(rack + 1)}

    def _items(self) -> list[dict[str, Any]]:
        racks = _racks(self.fridge.contents)
        if self.rack is not None:
            racks = [r for r in racks if r["num"] == self.rack]
        return [i for r in racks for i in r.get("items") or []]

    @property
    def native_value(self) -> int:
        return len(self._items())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self._items():
            counts[_label(item)] = counts.get(_label(item), 0) + 1
        return {"contents": counts}


class RoccoAlert(RoccoEntity, SensorEntity):
    """The fridge's current alert message, if any."""

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str) -> None:
        super().__init__(coordinator, fridge_id, "alert")

    @property
    def native_value(self) -> str:
        alert = (self.fridge.contents or {}).get("Alert")
        return (alert.get("Title") or "Alert") if alert else "None"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        alert = (self.fridge.contents or {}).get("Alert") or {}
        return {"message": alert.get("Message")}


class RoccoTemperature(RoccoEntity, SensorEntity):
    """A measured temperature reported by the fridge."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0  # the fridge reports whole degrees

    def __init__(
        self, coordinator: RoccoCoordinator, fridge_id: str, key: str, field: str, diagnostic: bool = False
    ) -> None:
        super().__init__(coordinator, fridge_id, key)
        self._field = field
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False

    @property
    def _celsius(self) -> bool:
        return self.fridge.reported.get("UnitTemperature") == UNIT_C

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS if self._celsius else UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self) -> float | None:
        return self.fridge.reported.get(f"{self._field}{'C' if self._celsius else 'F'}")


class RoccoWifiSignal(RoccoEntity, SensorEntity):
    """The fridge's Wi-Fi signal strength."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str) -> None:
        super().__init__(coordinator, fridge_id, "wifi_signal")

    @property
    def native_value(self) -> int | None:
        return self.fridge.reported.get("WifiSignalStrength")
