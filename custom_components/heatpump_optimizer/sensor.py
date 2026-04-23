"""Sensor entities for Heat Pump Cost Optimizer."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HeatPumpOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Heat Pump Optimizer sensors from a config entry."""
    coordinator: HeatPumpOptimizerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        OptimizationModeSensor(coordinator, entry),
        OptimizationStatusSensor(coordinator, entry),
        PredictedSavingsSensor(coordinator, entry),
        SavingsPercentageSensor(coordinator, entry),
        PredictedCostSensor(coordinator, entry),
        BaselineCostSensor(coordinator, entry),
        CurrentPriceSensor(coordinator, entry),
        CurrentSetpointSensor(coordinator, entry),
        CurrentPowerSensor(coordinator, entry),
        CurrentCOPSensor(coordinator, entry),
        IndoorTempSensor(coordinator, entry),
        OutdoorTempSensor(coordinator, entry),
        SlabTempSensor(coordinator, entry),
        NextOptimizationSensor(coordinator, entry),
        LastOptimizationSensor(coordinator, entry),
        HeatPumpActionSensor(coordinator, entry),
        ScheduleSensor(coordinator, entry),
        # Two-zone sensors
        UpperFloorTempSensor(coordinator, entry),
        LowerFloorTempSensor(coordinator, entry),
        FloorReturnTempSensor(coordinator, entry),
        SolarRadiationSensor(coordinator, entry),
        SolarHeatGainSensor(coordinator, entry),
        BufferTankTempSensor(coordinator, entry),
        # DHW sensors
        DHWTemperatureSensor(coordinator, entry),
        DHWScheduleSensor(coordinator, entry),
        DHWHeatingCostSensor(coordinator, entry),
        # Predictive insight sensors
        PredictiveInsightSensor(coordinator, entry),
        ECL110DisplaceSensor(coordinator, entry),
        ECL110EffectiveDisplaceSensor(coordinator, entry),
    ]

    async_add_entities(entities)


class HeatPumpOptimizerSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Heat Pump Optimizer sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HeatPumpOptimizerCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._entry = entry
        self._key = key

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


# ---------------------------------------------------------------------------
# Original sensors (maintained for backward compatibility)
# ---------------------------------------------------------------------------


class OptimizationModeSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the current optimization mode."""

    _attr_icon = "mdi:cog-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "mode", "Optimization Mode")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("mode", "unknown")
        return "unknown"


class OptimizationStatusSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the optimization solver status."""

    _attr_icon = "mdi:check-circle-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "optimization_status", "Optimization Status")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            return self.coordinator.data.get("optimization_status", "not_run")
        return "not_run"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            return {
                "solve_time_ms": self.coordinator.data.get("solve_time_ms", 0),
                "prices_available": self.coordinator.data.get("prices_available", 0),
                "weather_forecast_available": self.coordinator.data.get(
                    "weather_forecast_available", 0
                ),
                "two_zone_enabled": self.coordinator.data.get("two_zone_enabled", False),
            }
        return {}


class PredictedSavingsSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:piggy-bank-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "predicted_savings", "Predicted Savings")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("predicted_savings")
            return round(val, 2) if val is not None else None
        return None


class SavingsPercentageSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:percent"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "savings_percentage", "Savings Percentage")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("savings_percentage")
            return round(val, 1) if val is not None else None
        return None


class PredictedCostSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:currency-usd"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "predicted_cost", "Predicted Cost")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("predicted_cost")
            return round(val, 2) if val is not None else None
        return None


class BaselineCostSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:currency-usd-off"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "baseline_cost", "Baseline Cost")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("baseline_cost")
            return round(val, 2) if val is not None else None
        return None


class CurrentPriceSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:flash"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK/kWh"
    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "current_price", "Current Electricity Price")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("current_price")
            return round(val, 4) if val is not None else None
        return None


class CurrentSetpointSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:thermometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "current_setpoint", "Optimal Setpoint")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            return action.get("setpoint")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            attrs = {}
            if "upper_setpoint" in action:
                attrs["upper_floor_setpoint"] = action["upper_setpoint"]
            if "lower_setpoint" in action:
                attrs["lower_floor_setpoint"] = action["lower_setpoint"]
            return attrs
        return {}


class CurrentPowerSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:lightning-bolt"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "current_power", "Recommended Power")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            val = action.get("power")
            return round(val, 2) if val is not None else None
        return None


class CurrentCOPSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:gauge"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "current_cop", "Estimated COP")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            outdoor_temp = self.coordinator.data.get("outdoor_temperature", 5.0)
            cop = self.coordinator._thermal_model.compute_cop(outdoor_temp)
            return round(cop, 2)
        return None


class IndoorTempSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:home-thermometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "indoor_temp", "Indoor Temperature (Optimizer)")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("indoor_temperature")
            return round(val, 1) if val is not None else None
        return None


class OutdoorTempSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:thermometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "outdoor_temp", "Outdoor Temperature (Optimizer)")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("outdoor_temperature")
            return round(val, 1) if val is not None else None
        return None


class SlabTempSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:floor-plan"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "slab_temp", "Slab Temperature (Estimated)")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("slab_temperature")
            return round(val, 1) if val is not None else None
        return None


class NextOptimizationSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "next_optimization", "Next Optimization")

    @property
    def native_value(self) -> datetime | None:
        if self.coordinator.data:
            return self.coordinator.data.get("next_optimization")
        return None


class LastOptimizationSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "last_optimization", "Last Optimization")

    @property
    def native_value(self) -> datetime | None:
        if self.coordinator.data:
            return self.coordinator.data.get("last_optimization")
        return None


class HeatPumpActionSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "heat_pump_action", "Heat Pump Action")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            return action.get("mode", "unknown")
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            action = self.coordinator.data.get("current_action", {})
            attrs = {
                "power_kw": action.get("power"),
                "setpoint": action.get("setpoint"),
                "price": action.get("price"),
                "power_normalized": action.get("power_normalized"),
                "heat_pump_on": action.get("heat_pump_on"),
                "ecl110_displace": action.get("displace_value"),
            }
            if "upper_setpoint" in action:
                attrs["upper_floor_setpoint"] = action["upper_setpoint"]
            if "lower_setpoint" in action:
                attrs["lower_floor_setpoint"] = action["lower_setpoint"]
            if "solar_gain_kw" in action:
                attrs["solar_gain_kw"] = action["solar_gain_kw"]
            return attrs
        return {}


class ScheduleSensor(HeatPumpOptimizerSensorBase):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "schedule", "Optimization Schedule")

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            schedule = self.coordinator.data.get("schedule", [])
            return f"{len(schedule)} steps" if schedule else "no schedule"
        return "no schedule"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            return {
                "schedule": self.coordinator.data.get("schedule", []),
            }
        return {}


# ---------------------------------------------------------------------------
# New two-zone and solar sensors
# ---------------------------------------------------------------------------


class UpperFloorTempSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing upper floor (radiator zone) temperature."""

    _attr_icon = "mdi:home-floor-1"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "upper_floor_temp", "Upper Floor Temperature"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("upper_floor_temperature")
            return round(val, 1) if val is not None else None
        return None


class LowerFloorTempSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing lower floor (slab/floor heating zone) temperature."""

    _attr_icon = "mdi:home-floor-0"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "lower_floor_temp", "Lower Floor Temperature"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("lower_floor_temperature")
            return round(val, 1) if val is not None else None
        return None


class FloorReturnTempSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the floor heating return temperature (from real sensor)."""

    _attr_icon = "mdi:pipe-valve"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "floor_return_temp", "Floor Heating Return Temperature"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("floor_return_temperature")
            return round(val, 1) if val is not None else None
        return None


class SolarRadiationSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the current solar radiation used by optimizer."""

    _attr_icon = "mdi:white-balance-sunny"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "W/m²"
    _attr_device_class = SensorDeviceClass.IRRADIANCE

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "solar_radiation", "Solar Radiation (Optimizer)"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("solar_radiation")
            return round(val, 0) if val is not None else None
        return None


class SolarHeatGainSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the current solar heat gain contribution in kW."""

    _attr_icon = "mdi:solar-power"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "solar_heat_gain", "Solar Heat Gain"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("solar_heat_gain")
            return round(val, 3) if val is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            return {
                "solar_radiation_wm2": self.coordinator.data.get("solar_radiation", 0),
                "window_area_m2": self.coordinator._thermal_params.window_area,
                "shgc": self.coordinator._thermal_params.solar_heat_gain_coefficient,
                "orientation_factor": self.coordinator._thermal_params.solar_orientation_factor,
            }
        return {}


class BufferTankTempSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the modeled buffer tank temperature."""

    _attr_icon = "mdi:water-boiler"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "buffer_tank_temp", "Buffer Tank Temperature (Model)"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("buffer_tank_temperature")
            return round(val, 1) if val is not None else None
        return None


# ---------------------------------------------------------------------------
# DHW (Domestic Hot Water) sensors
# ---------------------------------------------------------------------------


class DHWTemperatureSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing current DHW tank temperature."""

    _attr_icon = "mdi:water-thermometer"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "dhw_temperature", "DHW Temperature"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("dhw_temperature")
            return round(val, 1) if val is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            return {
                "dhw_setpoint": self.coordinator.data.get("dhw_setpoint"),
                "dhw_min_temperature": self.coordinator.data.get("dhw_min_temperature"),
                "dhw_heating_active": self.coordinator.data.get("dhw_heating_active", False),
                "dhw_enabled": self.coordinator.data.get("dhw_enabled", False),
            }
        return {}


class DHWScheduleSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the planned DHW heating schedule for the next 24 hours."""

    _attr_icon = "mdi:water-boiler-auto"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "dhw_schedule", "DHW Heating Schedule"
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            schedule = self.coordinator.data.get("dhw_schedule", [])
            if schedule:
                active_steps = sum(1 for s in schedule if s.get("dhw_power", 0) > 0.1)
                return f"{active_steps} heating periods"
            return "no schedule"
        return "no schedule"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            return {
                "dhw_schedule": self.coordinator.data.get("dhw_schedule", []),
            }
        return {}


class DHWHeatingCostSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the estimated DHW heating cost."""

    _attr_icon = "mdi:cash-minus"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "dhw_heating_cost", "DHW Heating Cost"
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("dhw_heating_cost")
            return round(val, 2) if val is not None else None
        return None


# ---------------------------------------------------------------------------
# Predictive insight sensors
# ---------------------------------------------------------------------------


class PredictiveInsightSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing predictive optimization insights.

    Exposes the anticipatory control signals from the forecast analysis:
    - Solar reduction factor (how much heating is reduced due to upcoming sun)
    - Wind anticipation factor (how much extra heating due to upcoming wind)
    - Pre-heat urgency (overall urgency to pre-heat)
    """

    _attr_icon = "mdi:crystal-ball"

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator, entry, "predictive_insight", "Predictive Optimization Insight"
        )

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data:
            info = self.coordinator.data.get("predictive_info", {})
            if not info:
                return "no forecast"

            urgency = info.get("pre_heat_urgency", 0)
            solar_red = info.get("solar_reduction_factor", 1.0)

            if solar_red < 0.8:
                return "solar_anticipation"
            elif urgency > 0.5:
                return "pre_heating"
            else:
                return "normal"
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            info = self.coordinator.data.get("predictive_info", {})
            return {
                "solar_reduction_factor": info.get("solar_reduction_factor"),
                "wind_anticipation_factor": info.get("wind_anticipation_factor"),
                "rain_anticipation_factor": info.get("rain_anticipation_factor"),
                "pre_heat_urgency": info.get("pre_heat_urgency"),
                "future_solar_energy_kwh": info.get("future_solar_energy_kwh"),
                "future_solar_6_12h_kwh": info.get("future_solar_6_12h_kwh"),
                "avg_future_wind_ms": info.get("avg_future_wind_ms"),
                "avg_future_precip_mmh": info.get("avg_future_precip_mmh"),
                "dhw_preheat_lead_hours": info.get("dhw_preheat_lead_hours"),
                "dhw_peak_usage_hours": info.get("dhw_peak_usage_hours"),
                "dhw_min_temperature": info.get("dhw_min_temperature"),
                "dhw_target_temperature": info.get("dhw_target_temperature"),
                "dhw_usage_profile": self.coordinator.data.get("dhw_usage_profile", []),
            }
        return {}


class ECL110DisplaceSensor(HeatPumpOptimizerSensorBase):
    """Current ECL110 displace command value."""

    _attr_icon = "mdi:tune-vertical"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "ecl110_displace", "ECL110 Displace")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("ecl110_displace")
            return round(val, 1) if val is not None else None
        return None


class ECL110EffectiveDisplaceSensor(HeatPumpOptimizerSensorBase):
    """Modeled effective displace after ECL110 PI/PID dynamics."""

    _attr_icon = "mdi:chart-bell-curve"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator,
            entry,
            "ecl110_effective_displace",
            "ECL110 Effective Displace",
        )

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("ecl110_effective_displace")
            return round(val, 1) if val is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data:
            return {
                "command_topic": self.coordinator.data.get("ecl110_command_topic"),
                "state_topic": self.coordinator.data.get("ecl110_state_topic"),
            }
        return {}