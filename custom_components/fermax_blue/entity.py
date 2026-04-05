"""Base entity for Fermax Blue integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import FermaxBlueCoordinator


class FermaxBlueEntity(CoordinatorEntity[FermaxBlueCoordinator]):
    """Base entity for Fermax Blue devices."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FermaxBlueCoordinator) -> None:
        super().__init__(coordinator)
        self._device_id = coordinator.pairing.device_id

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("connection_state") == "Connected"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        info = self.coordinator.device_info
        model = f"{info.device_type} {info.subtype}" if info else "Unknown"

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=f"Fermax {self.coordinator.pairing.tag}",
            manufacturer=MANUFACTURER,
            model=model,
            sw_version=None,
        )
