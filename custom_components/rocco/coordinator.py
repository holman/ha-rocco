"""Data coordinator for Rocco."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RoccoApi, RoccoAuthError, RoccoError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class FridgeData:
    """Everything known about one fridge."""

    info: dict[str, Any]
    contents: dict[str, Any] | None = None
    requires_update: bool | None = None
    # Device shadow ({'Desired': {...}, 'Reported': {...}}), from the live AWS IoT
    # connection when configured, and from GraphQL command responses.
    shadow: dict[str, Any] | None = None

    @property
    def reported(self) -> dict[str, Any]:
        return (self.shadow or {}).get("Reported") or {}

    @property
    def desired(self) -> dict[str, Any]:
        return (self.shadow or {}).get("Desired") or {}

    def setting(self, key: str) -> Any:
        """A user-set value: the pending desired value if any, else reported."""
        value = self.desired.get(key)
        if value in (None, ""):
            value = self.reported.get(key)
        return value


type RoccoConfigEntry = ConfigEntry[RoccoCoordinator]


class RoccoCoordinator(DataUpdateCoordinator[dict[str, FridgeData]]):
    """Polls account, inventory and update status."""

    config_entry: RoccoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: RoccoConfigEntry, api: RoccoApi) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.api = api

    async def _async_update_data(self) -> dict[str, FridgeData]:
        previous = self.data or {}
        try:
            account = await self.api.get_account()
            result: dict[str, FridgeData] = {}
            for info in account.get("Fridges") or []:
                fid = info["ID"]
                fridge = FridgeData(info=info, shadow=previous[fid].shadow if fid in previous else None)
                fridge.contents = await self.api.get_contents(fid)
                fridge.requires_update = await self.api.requires_update(fid)
                result[fid] = fridge
        except RoccoAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RoccoError as err:
            raise UpdateFailed(str(err)) from err
        return result

    def apply_shadow(self, fridge_id: str, shadow: dict[str, Any]) -> None:
        """Merge the shadow returned by a GraphQL command."""
        self._merge(fridge_id, shadow.get("State") or shadow)

    def apply_iot_state(self, fridge_id: str, state: dict[str, Any]) -> None:
        """Merge a live shadow state from AWS IoT (camelCase keys)."""
        self._merge(
            fridge_id,
            {"Desired": _pascal(state.get("desired")), "Reported": _pascal(state.get("reported"))},
        )

    def _merge(self, fridge_id: str, shadow: dict[str, Any]) -> None:
        if (fridge := (self.data or {}).get(fridge_id)) is None:
            return
        old = fridge.shadow or {}
        fridge.shadow = {
            # GraphQL responses carry fewer fields (no current temps), so merge.
            part: {**(old.get(part) or {}), **{k: v for k, v in (shadow.get(part) or {}).items() if k != "__typename"}}
            for part in ("Desired", "Reported")
        }
        self.async_update_listeners()


def _pascal(d: dict[str, Any] | None) -> dict[str, Any]:
    """IoT uses lamp2On; GraphQL uses Lamp2On. Normalise to the GraphQL form."""
    return {k[:1].upper() + k[1:]: v for k, v in (d or {}).items()}
