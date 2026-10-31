"""Door, lock and firmware binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_IDENTITY_ID
from .coordinator import FridgeData, RoccoConfigEntry, RoccoCoordinator
from .entity import RoccoEntity


def _door(f: FridgeData) -> bool | None:
    status = f.reported.get("DoorStatus")
    return None if not status else status != "DoorStatusClosed"


def _unlocked(f: FridgeData) -> bool | None:
    engaged = f.reported.get("DoorLockEngaged")
    return None if engaged is None else not engaged


@dataclass(frozen=True, kw_only=True)
class RoccoBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[FridgeData], bool | None]


DESCRIPTIONS = (
    RoccoBinaryDescription(key="door", device_class=BinarySensorDeviceClass.DOOR, value=_door),
    # HA's lock class is on = unlocked.
    RoccoBinaryDescription(key="door_lock", device_class=BinarySensorDeviceClass.LOCK, value=_unlocked),
    RoccoBinaryDescription(
        key="firmware_update",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda f: f.requires_update,
    ),
)


# Only available from the live AWS IoT connection.
LIVE_DESCRIPTIONS = (
    RoccoBinaryDescription(
        key="compressor",
        device_class=BinarySensorDeviceClass.RUNNING,
        value=lambda f: f.reported.get("CompressorRunning"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: RoccoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    descriptions = DESCRIPTIONS + (LIVE_DESCRIPTIONS if entry.options.get(CONF_IDENTITY_ID) else ())
    async_add_entities(RoccoBinarySensor(coordinator, fid, d) for fid in coordinator.data for d in descriptions)


class RoccoBinarySensor(RoccoEntity, BinarySensorEntity):
    entity_description: RoccoBinaryDescription

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str, description: RoccoBinaryDescription) -> None:
        super().__init__(coordinator, fridge_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.fridge)
