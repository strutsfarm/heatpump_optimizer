"""Climate entity for Heat Pump Cost Optimizer.

Provides a virtual climate entity that represents the optimizer's control
over the heat pump. Users can use this to:
- Set target temperature
- Switch between optimization modes (auto, comfort, economy, off, boost)
- View current state and optimizer recommendations
- See both zone temperatures in attributes (two-zone mode)
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
        self._target_temperature = self._config.get(
            CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Heat Pump Optimizer",
            manufacturer="Custom",
            model="MPC Optimizer v2.0",
            sw_version="2.0.0",
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the current indoor temperature (weighted avg for two-zone)."""
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
        if self.coordinator.data:
            mode = self.coordinator.data.get("mode", MODE_AUTO)
            return MODE_TO_HVAC.get(mode, HVACMode.AUTO)
        return HVACMode.AUTO

    @property
    def hvac_action(self) -> HVACAction | None:
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
        if self.coordinator.data:
            return self.coordinator.data.get("mode", MODE_AUTO)
        return PRESET_AUTO

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes including two-zone info."""
        attrs = {}
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            attrs["optimizer_mode"] = action.get("mode", "unknown")
            attrs["recommended_power_kw"] = action.get("power")
            attrs["current_price"] = self.coordinator.data.get("current_price")
            attrs["predicted_savings"] = self.coordinator.data.get("predicted_savings")
            attrs["savings_percentage"] = self.coordinator.data.get(
                "savings_percentage"
            )
            attrs["optimization_status"] = self.coordinator.data.get(
                "optimization_status"
            )
            attrs["slab_temperature"] = self.coordinator.data.get("slab_temperature")
            attrs["heat_pump_on"] = action.get("heat_pump_on")
            attrs["ecl110_displace"] = action.get("displace_value")
            attrs["ecl110_effective_displace"] = self.coordinator.data.get(
                "ecl110_effective_displace"
            )
            attrs["ecl110_command_topic"] = self.coordinator.data.get(
                "ecl110_command_topic"
            )
            attrs["outdoor_temperature"] = self.coordinator.data.get(
                "outdoor_temperature"
            )

            # Two-zone attributes
            attrs["two_zone_enabled"] = self.coordinator.data.get(
                "two_zone_enabled", False
            )
            attrs["upper_floor_temperature"] = self.coordinator.data.get(
                "upper_floor_temperature"
            )
            attrs["lower_floor_temperature"] = self.coordinator.data.get(
                "lower_floor_temperature"
            )
            attrs["floor_return_temperature"] = self.coordinator.data.get(
                "floor_return_temperature"
            )
            attrs["solar_heat_gain_kw"] = self.coordinator.data.get(
                "solar_heat_gain"
            )
            attrs["solar_radiation_wm2"] = self.coordinator.data.get(
                "solar_radiation"
            )

            # Zone setpoints from current action
            if "upper_setpoint" in action:
                attrs["upper_floor_setpoint"] = action["upper_setpoint"]
            if "lower_setpoint" in action:
                attrs["lower_floor_setpoint"] = action["lower_setpoint"]

            # DHW status
            attrs["dhw_enabled"] = self.coordinator.data.get("dhw_enabled", False)
            attrs["dhw_temperature"] = self.coordinator.data.get("dhw_temperature")
            attrs["dhw_setpoint"] = self.coordinator.data.get("dhw_setpoint")
            attrs["dhw_heating_active"] = self.coordinator.data.get(
                "dhw_heating_active", False
            )
            attrs["dhw_heating_cost"] = self.coordinator.data.get(
                "dhw_heating_cost", 0.0
            )

            # Predictive optimization insights
            predictive = self.coordinator.data.get("predictive_info", {})
            if predictive:
                attrs["solar_reduction_factor"] = predictive.get(
                    "solar_reduction_factor"
                )
                attrs["wind_anticipation_factor"] = predictive.get(
                    "wind_anticipation_factor"
                )
                attrs["pre_heat_urgency"] = predictive.get("pre_heat_urgency")

        return attrs

    async def _async_publish_displace_from_current_action(self, reason: str) -> None:
        """Publish current displace command over MQTT through the coordinator."""
        try:
            await self.coordinator.async_publish_current_action(reason=reason)
        except Exception as err:
            _LOGGER.warning("Failed to publish ECL110 displace command: %s", err)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_mode(MODE_OFF)
            await self._async_publish_displace_from_current_action("manual_hvac_mode")
        elif hvac_mode == HVACMode.AUTO:
            await self.coordinator.async_set_mode(MODE_AUTO)
            await self._async_publish_displace_from_current_action("manual_hvac_mode")
        elif hvac_mode == HVACMode.HEAT:
            await self.coordinator.async_set_mode(MODE_COMFORT)
            await self._async_publish_displace_from_current_action("manual_hvac_mode")

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            self._target_temperature = temp
            self.coordinator._opt_config.target_temp = temp
            _LOGGER.info("Target temperature set to %.1f°C", temp)
            await self.coordinator.async_request_refresh()
            await self._async_publish_displace_from_current_action("manual_target_temp")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        mode_map = {
            PRESET_AUTO: MODE_AUTO,
            PRESET_COMFORT: MODE_COMFORT,
            PRESET_ECONOMY: MODE_ECONOMY,
            PRESET_BOOST: MODE_BOOST,
        }
        mode = mode_map.get(preset_mode, MODE_AUTO)
        await self.coordinator.async_set_mode(mode)
        await self._async_publish_displace_from_current_action("manual_preset")

    async def async_turn_on(self) -> None:
        await self.coordinator.async_set_mode(MODE_AUTO)
        await self._async_publish_displace_from_current_action("manual_turn_on")

    async def async_turn_off(self) -> None:
        await self.coordinator.async_set_mode(MODE_OFF)
        await self._async_publish_displace_from_current_action("manual_turn_off")