"""Climate entity for Heat Pump Cost Optimizer.

Provides a virtual climate entity that represents the optimizer's control
over the heat pump. Users can use this to:
- Set target temperature
- Switch between optimization modes (auto, comfort, economy, off, boost)
- View current state and optimizer recommendations
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MODE_AUTO,
    MODE_COMFORT,
    MODE_ECONOMY,
    MODE_OFF,
    MODE_BOOST,
    CONF_TARGET_TEMP,
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    DEFAULT_TARGET_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
)
from .coordinator import HeatPumpOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)

# Map our modes to HVAC modes
MODE_TO_HVAC = {
    MODE_AUTO: HVACMode.AUTO,
    MODE_COMFORT: HVACMode.HEAT,
    MODE_ECONOMY: HVACMode.HEAT,
    MODE_OFF: HVACMode.OFF,
    MODE_BOOST: HVACMode.HEAT,
}

# Map our modes to HVAC presets
PRESET_AUTO = "auto"
PRESET_COMFORT = "comfort"
PRESET_ECONOMY = "economy"
PRESET_BOOST = "boost"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Heat Pump Optimizer climate entity."""
    coordinator: HeatPumpOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([HeatPumpOptimizerClimate(coordinator, entry)])


class HeatPumpOptimizerClimate(CoordinatorEntity, ClimateEntity):
    """Climate entity for the Heat Pump Optimizer."""

    _attr_has_entity_name = True
    _attr_name = "Heat Pump Optimizer"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )
    _attr_preset_modes = [PRESET_AUTO, PRESET_COMFORT, PRESET_ECONOMY, PRESET_BOOST]
    _attr_target_temperature_step = 0.5
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: HeatPumpOptimizerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._entry = entry
        self._config = {**entry.data, **entry.options}
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_min_temp = self._config.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP) - 1
        self._attr_max_temp = self._config.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP) + 1
        self._target_temperature = self._config.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP)

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
    def current_temperature(self) -> float | None:
        """Return the current indoor temperature."""
        if self.coordinator.data:
            return self.coordinator.data.get("indoor_temperature")
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            setpoint = action.get("setpoint")
            if setpoint is not None:
                return setpoint
        return self._target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        if self.coordinator.data:
            mode = self.coordinator.data.get("mode", MODE_AUTO)
            return MODE_TO_HVAC.get(mode, HVACMode.AUTO)
        return HVACMode.AUTO

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            power_norm = action.get("power_normalized", 0)
            mode = self.coordinator.data.get("mode", MODE_AUTO)

            if mode == MODE_OFF:
                return HVACAction.OFF
            if power_norm > 0.1:
                return HVACAction.HEATING
            return HVACAction.IDLE
        return None

    @property
    def preset_mode(self) -> str | None:
        """Return the current preset mode."""
        if self.coordinator.data:
            mode = self.coordinator.data.get("mode", MODE_AUTO)
            return mode
        return PRESET_AUTO

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs = {}
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            attrs["optimizer_mode"] = action.get("mode", "unknown")
            attrs["recommended_power_kw"] = action.get("power")
            attrs["current_price"] = self.coordinator.data.get("current_price")
            attrs["predicted_savings"] = self.coordinator.data.get("predicted_savings")
            attrs["savings_percentage"] = self.coordinator.data.get("savings_percentage")
            attrs["optimization_status"] = self.coordinator.data.get("optimization_status")
            attrs["slab_temperature"] = self.coordinator.data.get("slab_temperature")
            attrs["outdoor_temperature"] = self.coordinator.data.get("outdoor_temperature")
        return attrs

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_mode(MODE_OFF)
        elif hvac_mode == HVACMode.AUTO:
            await self.coordinator.async_set_mode(MODE_AUTO)
        elif hvac_mode == HVACMode.HEAT:
            await self.coordinator.async_set_mode(MODE_COMFORT)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            self._target_temperature = temp
            self.coordinator._opt_config.target_temp = temp
            _LOGGER.info("Target temperature set to %.1f°C", temp)
            await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        mode_map = {
            PRESET_AUTO: MODE_AUTO,
            PRESET_COMFORT: MODE_COMFORT,
            PRESET_ECONOMY: MODE_ECONOMY,
            PRESET_BOOST: MODE_BOOST,
        }
        mode = mode_map.get(preset_mode, MODE_AUTO)
        await self.coordinator.async_set_mode(mode)

    async def async_turn_on(self) -> None:
        """Turn on."""
        await self.coordinator.async_set_mode(MODE_AUTO)

    async def async_turn_off(self) -> None:
        """Turn off."""
        await self.coordinator.async_set_mode(MODE_OFF)
