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
            model="MPC Optimizer v1.0",
            sw_version="1.0.0",
        )


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
            }
        return {}


class PredictedSavingsSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing predicted cost savings."""

    _attr_icon = "mdi:piggy-bank-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "SEK"  # Swedish Krona / adapt to your currency

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "predicted_savings", "Predicted Savings")

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data:
            val = self.coordinator.data.get("predicted_savings")
            return round(val, 2) if val is not None else None
        return None


class SavingsPercentageSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing savings as a percentage."""

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
    """Sensor showing predicted optimized cost."""

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
    """Sensor showing baseline (non-optimized) cost."""

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
    """Sensor showing current electricity price."""

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
    """Sensor showing the current optimal setpoint."""

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


class CurrentPowerSensor(HeatPumpOptimizerSensorBase):
    """Sensor showing the current recommended power."""

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
    """Sensor showing the current estimated COP."""

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
    """Sensor showing indoor temperature used by optimizer."""

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
    """Sensor showing outdoor temperature used by optimizer."""

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
    """Sensor showing estimated slab floor temperature."""

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
    """Sensor showing when the next optimization will run."""

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
    """Sensor showing when the last optimization ran."""

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
    """Sensor showing the current heat pump action/recommendation."""

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
            return {
                "power_kw": action.get("power"),
                "setpoint": action.get("setpoint"),
                "price": action.get("price"),
                "power_normalized": action.get("power_normalized"),
            }
        return {}


class ScheduleSensor(HeatPumpOptimizerSensorBase):
    """Sensor containing the optimization schedule as attributes."""

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
