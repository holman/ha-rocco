"""Base entity for Rocco."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import RoccoError
from .const import DOMAIN
from .coordinator import FridgeData, RoccoCoordinator


class RoccoEntity(CoordinatorEntity[RoccoCoordinator]):
    """One entity on one fridge."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RoccoCoordinator, fridge_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.fridge_id = fridge_id
        self._attr_unique_id = f"{fridge_id}_{key}"
        self._attr_translation_key = key
        info = self.fridge.info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, fridge_id)},
            name=info.get("Name") or "Rocco",
            manufacturer="Rocco",
            model="Super Smart Fridge",
            serial_number=info.get("IOTID"),
        )

    @property
    def fridge(self) -> FridgeData:
        return self.coordinator.data[self.fridge_id]

    @property
    def available(self) -> bool:
        return super().available and self.fridge_id in (self.coordinator.data or {})

    async def _command(self, call: Callable[[str], Awaitable[dict[str, Any]]]) -> None:
        """Run a mutation against this fridge and apply the returned shadow."""
        try:
            shadow = await call(self.fridge.info["IOTID"])
        except RoccoError as err:
            raise HomeAssistantError(f"Rocco command failed: {err}") from err
        self.coordinator.apply_shadow(self.fridge_id, shadow)
