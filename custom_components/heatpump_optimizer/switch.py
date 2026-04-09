"""Switch entity for Heat Pump Cost Optimizer.

Provides an on/off switch to enable/disable the optimizer.
When off, the heat pump is left in its default state.
When on, the optimizer actively controls the heat pump.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_AUTO, MODE_OFF
from .coordinator import HeatPumpOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heat Pump Optimizer switch from a config entry."""
    coordinator: HeatPumpOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OptimizerEnableSwitch(coordinator, entry)])


class OptimizerEnableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable the optimizer."""

    _attr_has_entity_name = True
    _attr_name = "Optimizer Active"
    _attr_icon = "mdi:robot"

    def __init__(
        self,
        coordinator: HeatPumpOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_optimizer_switch"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Heat Pump Optimizer",
            manufacturer="Custom",
            model="MPC Optimizer v1.0",
            sw_version="1.0.0",
        )

    @property
    def is_on(self) -> bool:
        """Return true if the optimizer is active."""
        if self.coordinator.data:
            return self.coordinator.data.get("mode", MODE_OFF) != MODE_OFF
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if self.coordinator.data:
            return {
                "mode": self.coordinator.data.get("mode"),
                "optimization_status": self.coordinator.data.get("optimization_status"),
            }
        return {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the optimizer."""
        await self.coordinator.async_set_mode(MODE_AUTO)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the optimizer."""
        await self.coordinator.async_set_mode(MODE_OFF)
