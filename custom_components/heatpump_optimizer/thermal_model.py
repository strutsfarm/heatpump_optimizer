"""Two-zone thermal model with DHW tank for house with radiator and slab floor heating.

This module models the thermal dynamics of a house with two heating zones
served by an air-to-water heat pump with a buffer tank, plus a DHW (Domestic
Hot Water) storage tank:

    Zone 1 (Upper Floor): Radiator heating — fast thermal response (low mass)
    Zone 2 (Lower Floor): Slab floor heating — slow thermal response (high mass)
    Buffer Tank: 35L buffer coupling the heat pump to both circuits
    DHW Tank: 200-300L hot water tank with its own thermal dynamics

The thermal dynamics are governed by:

    C_upper * dT_upper/dt = Q_rad - Q_loss_upper + Q_inter + Q_solar_upper + Q_internal_upper
    C_slab  * dT_slab/dt  = Q_floor_hp - Q_slab_to_lower
    C_lower * dT_lower/dt = Q_slab_to_lower - Q_loss_lower - Q_inter + Q_solar_lower + Q_internal_lower
    C_buf   * dT_buf/dt   = Q_hp - Q_rad_draw - Q_floor_draw - Q_buf_loss
    C_dhw   * dT_dhw/dt   = Q_hp_dhw - Q_dhw_draw - Q_dhw_loss

Where:
    Q_hp_dhw = heat pump power allocated to DHW heating
    Q_dhw_draw = heat lost from DHW draws (consumption)
    Q_dhw_loss = standby heat loss from DHW tank

Heat loss model includes:
    - Wind speed effect: h_conv = h_base * (1 + wind_sensitivity * wind_speed)
    - Rain effect: U_eff = U_base * rain_multiplier when raining
    - Both use FORECASTED values per time step for true predictive control

The model falls back to the original single-zone behaviour when two-zone
parameters are not provided.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .const import (
    DEFAULT_HOUSE_THERMAL_MASS,
    DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT,
    DEFAULT_SLAB_THERMAL_MASS,
    DEFAULT_SLAB_HEAT_TRANSFER,
    DEFAULT_HEAT_PUMP_COP_NOMINAL,
    DEFAULT_HEAT_PUMP_MAX_POWER,
    DEFAULT_HEAT_PUMP_MIN_POWER,
    DEFAULT_UPPER_FLOOR_THERMAL_MASS,
    DEFAULT_LOWER_FLOOR_THERMAL_MASS,
    DEFAULT_UPPER_FLOOR_HEAT_LOSS,
    DEFAULT_LOWER_FLOOR_HEAT_LOSS,
    DEFAULT_INTER_ZONE_TRANSFER,
    DEFAULT_RADIATOR_POWER_FRACTION,
    DEFAULT_BUFFER_TANK_VOLUME,
    DEFAULT_BUFFER_TANK_LOSS,
    DEFAULT_WINDOW_AREA,
    DEFAULT_SOLAR_ORIENTATION_FACTOR,
    DEFAULT_SOLAR_HEAT_GAIN_COEFF,
    DEFAULT_SOLAR_UPPER_FRACTION,
    DEFAULT_DHW_TANK_VOLUME,
    DEFAULT_DHW_SETPOINT,
    DEFAULT_DHW_MIN_TEMP,
    DEFAULT_DHW_DAILY_CONSUMPTION,
    DEFAULT_WIND_SENSITIVITY,
    DEFAULT_RAIN_HEAT_LOSS_MULTIPLIER,
    WIND_CHILL_FACTOR,
    RAIN_COOLING_FACTOR,
)

_LOGGER = logging.getLogger(__name__)

# Specific heat capacity of water: ~0.00116 kWh/(liter·°C)
WATER_SPECIFIC_HEAT: float = 0.00116


@dataclass
class ThermalParameters:
    """Parameters for the two-zone thermal model with DHW."""

    # --- Legacy single-zone parameters (kept for backward compat) ---
    room_thermal_mass: float = DEFAULT_HOUSE_THERMAL_MASS
    slab_thermal_mass: float = DEFAULT_SLAB_THERMAL_MASS
    heat_loss_coefficient: float = DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT
    slab_heat_transfer: float = DEFAULT_SLAB_HEAT_TRANSFER

    # --- Two-zone parameters ---
    upper_floor_thermal_mass: float = DEFAULT_UPPER_FLOOR_THERMAL_MASS  # kWh/°C
    lower_floor_thermal_mass: float = DEFAULT_LOWER_FLOOR_THERMAL_MASS  # kWh/°C
    upper_floor_heat_loss: float = DEFAULT_UPPER_FLOOR_HEAT_LOSS  # kW/°C
    lower_floor_heat_loss: float = DEFAULT_LOWER_FLOOR_HEAT_LOSS  # kW/°C
    inter_zone_transfer: float = DEFAULT_INTER_ZONE_TRANSFER  # kW/°C
    radiator_power_fraction: float = DEFAULT_RADIATOR_POWER_FRACTION  # 0-1

    # Buffer tank
    buffer_tank_volume: float = DEFAULT_BUFFER_TANK_VOLUME  # liters
    buffer_tank_heat_loss: float = DEFAULT_BUFFER_TANK_LOSS  # kW/°C

    # Solar gain parameters
    window_area: float = DEFAULT_WINDOW_AREA  # m²
    solar_orientation_factor: float = DEFAULT_SOLAR_ORIENTATION_FACTOR
    solar_heat_gain_coefficient: float = DEFAULT_SOLAR_HEAT_GAIN_COEFF
    solar_upper_fraction: float = DEFAULT_SOLAR_UPPER_FRACTION

    # DHW tank parameters
    dhw_tank_volume: float = DEFAULT_DHW_TANK_VOLUME  # liters
    dhw_setpoint: float = DEFAULT_DHW_SETPOINT  # °C
    dhw_min_temp: float = DEFAULT_DHW_MIN_TEMP  # °C
    dhw_daily_consumption: float = DEFAULT_DHW_DAILY_CONSUMPTION  # liters/day
    dhw_tank_heat_loss_coefficient: float = 0.005  # kW/°C standby loss

    # Weather sensitivity parameters (configurable)
    wind_sensitivity: float = DEFAULT_WIND_SENSITIVITY  # fraction per m/s
    rain_heat_loss_multiplier: float = DEFAULT_RAIN_HEAT_LOSS_MULTIPLIER  # multiplier

    # Heat pump parameters
    cop_nominal: float = DEFAULT_HEAT_PUMP_COP_NOMINAL
    cop_reference_temp: float = 7.0  # °C
    max_electrical_power: float = DEFAULT_HEAT_PUMP_MAX_POWER  # kW
    min_electrical_power: float = DEFAULT_HEAT_PUMP_MIN_POWER  # kW

    # Internal gains (kW) - baseline heat from occupancy, appliances, etc.
    internal_gains: float = 0.3

    # Whether to use the enhanced two-zone model
    two_zone_enabled: bool = False

    # Whether DHW optimization is enabled
    dhw_enabled: bool = False

    @property
    def buffer_tank_thermal_mass(self) -> float:
        """Thermal mass of buffer tank in kWh/°C."""
        return self.buffer_tank_volume * WATER_SPECIFIC_HEAT

    @property
    def dhw_tank_thermal_mass(self) -> float:
        """Thermal mass of DHW tank in kWh/°C."""
        return self.dhw_tank_volume * WATER_SPECIFIC_HEAT

    @property
    def dhw_draw_power(self) -> float:
        """Average DHW draw power in kW (heat lost from tank due to consumption).

        Based on daily consumption heated from ~10°C cold water to tank temp.
        """
        # Average draw rate in liters per hour
        liters_per_hour = self.dhw_daily_consumption / 24.0
        # Heat needed: mass * Cp * delta_T
        # Assume cold water at 10°C, mixing with tank water
        delta_t = self.dhw_setpoint - 10.0  # °C temperature rise
        # Power = volume_flow * Cp * delta_T
        return liters_per_hour * WATER_SPECIFIC_HEAT * delta_t

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ThermalParameters:
        """Create ThermalParameters from a config dictionary."""
        from .const import (
            CONF_HOUSE_THERMAL_MASS,
            CONF_HOUSE_HEAT_LOSS_COEFFICIENT,
            CONF_SLAB_THERMAL_MASS,
            CONF_SLAB_HEAT_TRANSFER,
            CONF_HEAT_PUMP_COP_NOMINAL,
            CONF_HEAT_PUMP_MAX_POWER,
            CONF_HEAT_PUMP_MIN_POWER,
            CONF_UPPER_FLOOR_THERMAL_MASS,
            CONF_LOWER_FLOOR_THERMAL_MASS,
            CONF_UPPER_FLOOR_HEAT_LOSS,
            CONF_LOWER_FLOOR_HEAT_LOSS,
            CONF_INTER_ZONE_TRANSFER,
            CONF_RADIATOR_POWER_FRACTION,
            CONF_BUFFER_TANK_VOLUME,
            CONF_BUFFER_TANK_LOSS,
            CONF_WINDOW_AREA,
            CONF_SOLAR_ORIENTATION_FACTOR,
            CONF_SOLAR_HEAT_GAIN_COEFF,
            CONF_SOLAR_UPPER_FRACTION,
            CONF_DHW_TANK_VOLUME,
            CONF_DHW_SETPOINT,
            CONF_DHW_MIN_TEMP,
            CONF_DHW_DAILY_CONSUMPTION,
            CONF_WIND_SENSITIVITY,
            CONF_RAIN_HEAT_LOSS_MULTIPLIER,
            CONF_DHW_TEMP_ENTITY,
        )

        # Detect if two-zone config is provided
        two_zone = any(
            k in config
            for k in (
                CONF_UPPER_FLOOR_THERMAL_MASS,
                CONF_LOWER_FLOOR_THERMAL_MASS,
                CONF_INTER_ZONE_TRANSFER,
                CONF_RADIATOR_POWER_FRACTION,
            )
        )

        # Detect if DHW config is provided
        dhw_enabled = any(
            k in config
            for k in (CONF_DHW_TANK_VOLUME, CONF_DHW_TEMP_ENTITY)
        )

        return cls(
            # Legacy
            room_thermal_mass=config.get(
                CONF_HOUSE_THERMAL_MASS, DEFAULT_HOUSE_THERMAL_MASS
            ),
            slab_thermal_mass=config.get(
                CONF_SLAB_THERMAL_MASS, DEFAULT_SLAB_THERMAL_MASS
            ),
            heat_loss_coefficient=config.get(
                CONF_HOUSE_HEAT_LOSS_COEFFICIENT, DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT
            ),
            slab_heat_transfer=config.get(
                CONF_SLAB_HEAT_TRANSFER, DEFAULT_SLAB_HEAT_TRANSFER
            ),
            # Two-zone
            upper_floor_thermal_mass=config.get(
                CONF_UPPER_FLOOR_THERMAL_MASS, DEFAULT_UPPER_FLOOR_THERMAL_MASS
            ),
            lower_floor_thermal_mass=config.get(
                CONF_LOWER_FLOOR_THERMAL_MASS, DEFAULT_LOWER_FLOOR_THERMAL_MASS
            ),
            upper_floor_heat_loss=config.get(
                CONF_UPPER_FLOOR_HEAT_LOSS, DEFAULT_UPPER_FLOOR_HEAT_LOSS
            ),
            lower_floor_heat_loss=config.get(
                CONF_LOWER_FLOOR_HEAT_LOSS, DEFAULT_LOWER_FLOOR_HEAT_LOSS
            ),
            inter_zone_transfer=config.get(
                CONF_INTER_ZONE_TRANSFER, DEFAULT_INTER_ZONE_TRANSFER
            ),
            radiator_power_fraction=config.get(
                CONF_RADIATOR_POWER_FRACTION, DEFAULT_RADIATOR_POWER_FRACTION
            ),
            # Buffer tank
            buffer_tank_volume=config.get(
                CONF_BUFFER_TANK_VOLUME, DEFAULT_BUFFER_TANK_VOLUME
            ),
            buffer_tank_heat_loss=config.get(
                CONF_BUFFER_TANK_LOSS, DEFAULT_BUFFER_TANK_LOSS
            ),
            # Solar
            window_area=config.get(CONF_WINDOW_AREA, DEFAULT_WINDOW_AREA),
            solar_orientation_factor=config.get(
                CONF_SOLAR_ORIENTATION_FACTOR, DEFAULT_SOLAR_ORIENTATION_FACTOR
            ),
            solar_heat_gain_coefficient=config.get(
                CONF_SOLAR_HEAT_GAIN_COEFF, DEFAULT_SOLAR_HEAT_GAIN_COEFF
            ),
            solar_upper_fraction=config.get(
                CONF_SOLAR_UPPER_FRACTION, DEFAULT_SOLAR_UPPER_FRACTION
            ),
            # DHW
            dhw_tank_volume=config.get(
                CONF_DHW_TANK_VOLUME, DEFAULT_DHW_TANK_VOLUME
            ),
            dhw_setpoint=config.get(
                CONF_DHW_SETPOINT, DEFAULT_DHW_SETPOINT
            ),
            dhw_min_temp=config.get(
                CONF_DHW_MIN_TEMP, DEFAULT_DHW_MIN_TEMP
            ),
            dhw_daily_consumption=config.get(
                CONF_DHW_DAILY_CONSUMPTION, DEFAULT_DHW_DAILY_CONSUMPTION
            ),
            # Weather sensitivity
            wind_sensitivity=config.get(
                CONF_WIND_SENSITIVITY, DEFAULT_WIND_SENSITIVITY
            ),
            rain_heat_loss_multiplier=config.get(
                CONF_RAIN_HEAT_LOSS_MULTIPLIER, DEFAULT_RAIN_HEAT_LOSS_MULTIPLIER
            ),
            # Heat pump
            cop_nominal=config.get(
                CONF_HEAT_PUMP_COP_NOMINAL, DEFAULT_HEAT_PUMP_COP_NOMINAL
            ),
            max_electrical_power=config.get(
                CONF_HEAT_PUMP_MAX_POWER, DEFAULT_HEAT_PUMP_MAX_POWER
            ),
            min_electrical_power=config.get(
                CONF_HEAT_PUMP_MIN_POWER, DEFAULT_HEAT_PUMP_MIN_POWER
            ),
            two_zone_enabled=two_zone,
            dhw_enabled=dhw_enabled,
        )


@dataclass
class WeatherForecastPoint:
    """A single point in the weather forecast."""

    timestamp: float  # Unix timestamp
    temperature: float  # °C
    wind_speed: float = 0.0  # m/s
    precipitation: float = 0.0  # mm/h
    solar_radiation: float = 0.0  # W/m² (global horizontal irradiance)


@dataclass
class ThermalState:
    """Current thermal state of the two-zone system with DHW.

    Falls back to single-zone semantics when two_zone_enabled is False:
    - room_temperature is the single room temp
    - slab_temperature is the single slab temp
    """

    room_temperature: float = 21.0  # °C (or upper floor temp in two-zone)
    slab_temperature: float = 22.0  # °C
    outdoor_temperature: float = 5.0  # °C

    # Two-zone additions
    upper_floor_temperature: float = 21.0  # °C
    lower_floor_temperature: float = 21.0  # °C
    buffer_tank_temperature: float = 40.0  # °C
    floor_return_temperature: float | None = None  # °C (from real sensor)
    solar_radiation: float = 0.0  # W/m² current solar irradiance

    # DHW state
    dhw_temperature: float = 55.0  # °C current DHW tank temperature


# DHW draw pattern: normalized hourly multipliers (24 values, sum=24)
# Morning peak (6-9), evening peak (17-21), low overnight
DHW_HOURLY_DRAW_PATTERN: list[float] = [
    0.2, 0.1, 0.1, 0.1, 0.2, 0.5,   # 00-05: very low
    1.5, 2.5, 2.0, 1.0, 0.8, 0.7,   # 06-11: morning peak
    0.8, 0.7, 0.6, 0.6, 0.8, 1.5,   # 12-17: afternoon
    2.0, 2.2, 1.8, 1.2, 0.8, 0.4,   # 18-23: evening peak
]
# Normalize so average = 1.0
_DHW_SUM = sum(DHW_HOURLY_DRAW_PATTERN)
DHW_HOURLY_DRAW_PATTERN = [x * 24.0 / _DHW_SUM for x in DHW_HOURLY_DRAW_PATTERN]


class ThermalModel:
    """Thermal model supporting single-zone, two-zone, and DHW operation."""

    def __init__(self, params: ThermalParameters) -> None:
        """Initialize the thermal model."""
        self.params = params

    # ------------------------------------------------------------------
    # Common helpers
    # ------------------------------------------------------------------

    def compute_cop(self, outdoor_temp: float) -> float:
        """Compute heat pump COP as function of outdoor temperature.

        COP ≈ COP_nominal * (1 + 0.025 * (T_outdoor - T_ref))
        """
        delta = outdoor_temp - self.params.cop_reference_temp
        factor = max(0.3, 1.0 + 0.025 * delta)
        return self.params.cop_nominal * min(factor, 1.5)

    def compute_cop_dhw(self, outdoor_temp: float, dhw_temp: float) -> float:
        """Compute heat pump COP for DHW mode.

        DHW requires higher supply temperature (55-65°C vs 35-45°C for space
        heating), so COP is lower.  Rough model:
        COP_dhw ≈ COP_space * 0.7 (penalty for higher supply temp)
        """
        base_cop = self.compute_cop(outdoor_temp)
        # Higher DHW temp → lower COP (Carnot-like penalty)
        dhw_penalty = max(0.5, 1.0 - 0.008 * (dhw_temp - 35.0))
        return base_cop * dhw_penalty

    def effective_outdoor_temp(
        self, temp: float, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective outdoor temperature accounting for wind chill and rain."""
        wind_effect = wind_speed * 0.3
        rain_effect = precipitation * 0.5
        return temp - wind_effect - rain_effect

    def effective_heat_loss_coefficient(
        self, base_u: float, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective heat loss coefficient using configurable weather sensitivity.

        Wind effect: convective heat transfer increases with wind speed.
        h_eff = h_base * (1 + wind_sensitivity * wind_speed)

        Rain effect: wet building envelope has higher U-value.
        U_eff = U_wind_adjusted * rain_multiplier (when precipitation > 0)
        """
        p = self.params
        # Wind-enhanced convective loss
        wind_factor = 1.0 + p.wind_sensitivity * wind_speed
        u_wind = base_u * wind_factor

        # Rain effect: apply multiplier when raining
        if precipitation > 0.1:  # threshold for "raining"
            # Scale rain multiplier based on precipitation intensity
            # Light rain (0.1-1 mm/h): partial multiplier
            # Heavy rain (>2 mm/h): full multiplier
            rain_intensity = min(precipitation / 2.0, 1.0)
            rain_factor = 1.0 + (p.rain_heat_loss_multiplier - 1.0) * rain_intensity
            u_wind *= rain_factor

        return u_wind

    def effective_heat_loss_coefficient_legacy(
        self, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective heat loss coefficient (backward compat wrapper)."""
        return self.effective_heat_loss_coefficient(
            self.params.heat_loss_coefficient, wind_speed, precipitation
        )

    def compute_solar_gain(self, solar_radiation: float) -> float:
        """Compute total solar heat gain in kW from solar radiation (W/m²).

        Q_solar = solar_radiation * window_area * orientation_factor * SHGC / 1000
        """
        p = self.params
        if solar_radiation <= 0:
            return 0.0
        return (
            solar_radiation
            * p.window_area
            * p.solar_orientation_factor
            * p.solar_heat_gain_coefficient
            / 1000.0  # W → kW
        )

    def solar_gain_per_zone(
        self, solar_radiation: float
    ) -> tuple[float, float]:
        """Split solar gain between upper and lower floor.

        Returns: (Q_solar_upper, Q_solar_lower) in kW
        """
        total = self.compute_solar_gain(solar_radiation)
        upper = total * self.params.solar_upper_fraction
        lower = total * (1.0 - self.params.solar_upper_fraction)
        return upper, lower

    # ------------------------------------------------------------------
    # DHW tank model
    # ------------------------------------------------------------------

    def dhw_draw_rate(self, hour_of_day: float) -> float:
        """Get DHW draw rate in kW for a given hour of day.

        Uses a time-of-day pattern multiplied by average draw power.
        """
        hour_idx = int(hour_of_day) % 24
        pattern_multiplier = DHW_HOURLY_DRAW_PATTERN[hour_idx]
        return self.params.dhw_draw_power * pattern_multiplier

    def simulate_dhw_step(
        self,
        dhw_temp: float,
        dhw_power_thermal: float,
        hour_of_day: float,
        ambient_temp: float = 20.0,
        dt_hours: float = 0.25,
    ) -> float:
        """Simulate one time step of DHW tank dynamics.

        Args:
            dhw_temp: Current DHW tank temperature (°C)
            dhw_power_thermal: Thermal power from HP to DHW (kW)
            hour_of_day: Current hour (0-24) for draw pattern
            ambient_temp: Ambient temperature near the tank (°C)
            dt_hours: Time step (hours)

        Returns:
            New DHW tank temperature (°C)
        """
        p = self.params
        C_dhw = p.dhw_tank_thermal_mass
        if C_dhw < 0.01:
            return dhw_temp

        # Heat input from heat pump
        q_in = dhw_power_thermal

        # Heat drawn by consumption
        q_draw = self.dhw_draw_rate(hour_of_day)

        # Standby heat loss to ambient
        q_loss = p.dhw_tank_heat_loss_coefficient * (dhw_temp - ambient_temp)

        # Temperature change
        dT = (q_in - q_draw - q_loss) / C_dhw
        new_temp = dhw_temp + dT * dt_hours

        # Physical bounds (can't go below cold water inlet ~10°C)
        new_temp = max(10.0, new_temp)

        return new_temp

    # ------------------------------------------------------------------
    # Single-zone simulation (backward compatible)
    # ------------------------------------------------------------------

    def _simulate_step_single(
        self,
        state: ThermalState,
        electrical_power: float,
        outdoor_temp: float,
        wind_speed: float = 0.0,
        precipitation: float = 0.0,
        solar_radiation: float = 0.0,
        dt_hours: float = 0.25,
    ) -> ThermalState:
        """Simulate one step with the original single-zone model."""
        p = self.params
        cop = self.compute_cop(outdoor_temp)
        thermal_power = cop * electrical_power

        u_eff = self.effective_heat_loss_coefficient(
            p.heat_loss_coefficient, wind_speed, precipitation
        )
        q_slab_to_room = p.slab_heat_transfer * (
            state.slab_temperature - state.room_temperature
        )
        q_loss = u_eff * (state.room_temperature - outdoor_temp)
        q_internal = p.internal_gains
        q_solar = self.compute_solar_gain(solar_radiation)

        dT_room = (q_slab_to_room - q_loss + q_internal + q_solar) / p.room_thermal_mass
        dT_slab = (thermal_power - q_slab_to_room) / p.slab_thermal_mass

        new_room = state.room_temperature + dT_room * dt_hours
        new_slab = state.slab_temperature + dT_slab * dt_hours

        return ThermalState(
            room_temperature=new_room,
            slab_temperature=new_slab,
            outdoor_temperature=outdoor_temp,
            upper_floor_temperature=new_room,
            lower_floor_temperature=new_room,
            buffer_tank_temperature=state.buffer_tank_temperature,
            floor_return_temperature=state.floor_return_temperature,
            solar_radiation=solar_radiation,
            dhw_temperature=state.dhw_temperature,
        )

    # ------------------------------------------------------------------
    # Two-zone simulation
    # ------------------------------------------------------------------

    def _simulate_step_two_zone(
        self,
        state: ThermalState,
        electrical_power: float,
        outdoor_temp: float,
        wind_speed: float = 0.0,
        precipitation: float = 0.0,
        solar_radiation: float = 0.0,
        dt_hours: float = 0.25,
    ) -> ThermalState:
        """Simulate one step with the two-zone model including buffer tank.

        State vector: [T_upper, T_lower, T_slab, T_buffer]
        """
        p = self.params
        cop = self.compute_cop(outdoor_temp)
        thermal_power = cop * electrical_power  # total heat from HP to buffer

        # Weather-adjusted heat loss using configurable sensitivity
        u_upper = self.effective_heat_loss_coefficient(
            p.upper_floor_heat_loss, wind_speed, precipitation
        )
        # Lower floor less exposed to wind (partially underground/sheltered)
        u_lower = self.effective_heat_loss_coefficient(
            p.lower_floor_heat_loss, wind_speed * 0.5, precipitation * 0.5
        )

        # Solar gains per zone
        q_solar_upper, q_solar_lower = self.solar_gain_per_zone(solar_radiation)

        # Internal gains split proportional to area ratio
        area_ratio = getattr(p, "upper_floor_area_ratio", 0.5) if hasattr(p, "upper_floor_area_ratio") else 0.5
        q_internal_upper = p.internal_gains * area_ratio
        q_internal_lower = p.internal_gains * (1.0 - area_ratio)

        T_upper = state.upper_floor_temperature
        T_lower = state.lower_floor_temperature
        T_slab = state.slab_temperature
        T_buf = state.buffer_tank_temperature

        # --- Buffer tank dynamics ---
        C_buf = p.buffer_tank_thermal_mass
        if C_buf < 1e-6:
            C_buf = 0.04  # fallback for 35L

        # Heat drawn from buffer to radiators (upper floor)
        rad_fraction = p.radiator_power_fraction
        q_rad_from_buf = rad_fraction * thermal_power
        q_floor_from_buf = (1.0 - rad_fraction) * thermal_power

        # Buffer tank loss to ambient (assume ~20°C ambient indoors)
        q_buf_loss = p.buffer_tank_heat_loss * (T_buf - 20.0)

        dT_buf = (thermal_power - q_rad_from_buf - q_floor_from_buf - q_buf_loss) / max(C_buf, 0.01)

        # --- Slab dynamics ---
        q_slab_to_lower = p.slab_heat_transfer * (T_slab - T_lower)
        dT_slab = (q_floor_from_buf - q_slab_to_lower) / p.slab_thermal_mass

        # --- Inter-zone heat transfer ---
        q_inter = p.inter_zone_transfer * (T_lower - T_upper)

        # --- Upper floor (radiators) ---
        q_loss_upper = u_upper * (T_upper - outdoor_temp)
        dT_upper = (
            q_rad_from_buf - q_loss_upper + q_inter + q_solar_upper + q_internal_upper
        ) / p.upper_floor_thermal_mass

        # --- Lower floor (slab heated) ---
        q_loss_lower = u_lower * (T_lower - outdoor_temp)
        dT_lower = (
            q_slab_to_lower - q_loss_lower - q_inter + q_solar_lower + q_internal_lower
        ) / p.lower_floor_thermal_mass

        # Euler integration
        new_upper = T_upper + dT_upper * dt_hours
        new_lower = T_lower + dT_lower * dt_hours
        new_slab = T_slab + dT_slab * dt_hours
        new_buf = T_buf + dT_buf * dt_hours

        # Weighted average for legacy room_temperature field
        avg_room = new_upper * area_ratio + new_lower * (1.0 - area_ratio)

        return ThermalState(
            room_temperature=avg_room,
            slab_temperature=new_slab,
            outdoor_temperature=outdoor_temp,
            upper_floor_temperature=new_upper,
            lower_floor_temperature=new_lower,
            buffer_tank_temperature=new_buf,
            floor_return_temperature=state.floor_return_temperature,
            solar_radiation=solar_radiation,
            dhw_temperature=state.dhw_temperature,
        )

    # ------------------------------------------------------------------
    # Public simulation interface
    # ------------------------------------------------------------------

    def simulate_step(
        self,
        state: ThermalState,
        electrical_power: float,
        outdoor_temp: float,
        wind_speed: float = 0.0,
        precipitation: float = 0.0,
        solar_radiation: float = 0.0,
        dt_hours: float = 0.25,
    ) -> ThermalState:
        """Simulate one time step (dispatches to single or two-zone)."""
        if self.params.two_zone_enabled:
            return self._simulate_step_two_zone(
                state, electrical_power, outdoor_temp,
                wind_speed, precipitation, solar_radiation, dt_hours,
            )
        return self._simulate_step_single(
            state, electrical_power, outdoor_temp,
            wind_speed, precipitation, solar_radiation, dt_hours,
        )

    def simulate_trajectory(
        self,
        initial_state: ThermalState,
        power_schedule: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray | None = None,
        precipitation: np.ndarray | None = None,
        solar_radiation: np.ndarray | None = None,
        dt_hours: float = 0.25,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Simulate the full trajectory given a power schedule.

        Returns:
            Tuple of (room_temperatures, slab_temperatures,
                       upper_floor_temperatures, lower_floor_temperatures)
        """
        n_steps = len(power_schedule)

        if wind_speeds is None:
            wind_speeds = np.zeros(n_steps)
        if precipitation is None:
            precipitation = np.zeros(n_steps)
        if solar_radiation is None:
            solar_radiation = np.zeros(n_steps)

        room_temps = np.zeros(n_steps + 1)
        slab_temps = np.zeros(n_steps + 1)
        upper_temps = np.zeros(n_steps + 1)
        lower_temps = np.zeros(n_steps + 1)

        room_temps[0] = initial_state.room_temperature
        slab_temps[0] = initial_state.slab_temperature
        upper_temps[0] = initial_state.upper_floor_temperature
        lower_temps[0] = initial_state.lower_floor_temperature

        state = initial_state
        for i in range(n_steps):
            state = self.simulate_step(
                state=state,
                electrical_power=power_schedule[i],
                outdoor_temp=outdoor_temps[i],
                wind_speed=wind_speeds[i],
                precipitation=precipitation[i],
                solar_radiation=solar_radiation[i],
                dt_hours=dt_hours,
            )
            room_temps[i + 1] = state.room_temperature
            slab_temps[i + 1] = state.slab_temperature
            upper_temps[i + 1] = state.upper_floor_temperature
            lower_temps[i + 1] = state.lower_floor_temperature

        return room_temps, slab_temps, upper_temps, lower_temps

    def simulate_trajectory_with_dhw(
        self,
        initial_state: ThermalState,
        space_power_schedule: np.ndarray,
        dhw_power_schedule: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray | None = None,
        precipitation: np.ndarray | None = None,
        solar_radiation: np.ndarray | None = None,
        start_hour: float = 0.0,
        dt_hours: float = 0.25,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Simulate full trajectory with coordinated space + DHW heating.

        Returns:
            Tuple of (room_temps, slab_temps, upper_temps, lower_temps, dhw_temps)
        """
        n_steps = len(space_power_schedule)

        if wind_speeds is None:
            wind_speeds = np.zeros(n_steps)
        if precipitation is None:
            precipitation = np.zeros(n_steps)
        if solar_radiation is None:
            solar_radiation = np.zeros(n_steps)

        room_temps = np.zeros(n_steps + 1)
        slab_temps = np.zeros(n_steps + 1)
        upper_temps = np.zeros(n_steps + 1)
        lower_temps = np.zeros(n_steps + 1)
        dhw_temps = np.zeros(n_steps + 1)

        room_temps[0] = initial_state.room_temperature
        slab_temps[0] = initial_state.slab_temperature
        upper_temps[0] = initial_state.upper_floor_temperature
        lower_temps[0] = initial_state.lower_floor_temperature
        dhw_temps[0] = initial_state.dhw_temperature

        state = initial_state
        current_hour = start_hour

        for i in range(n_steps):
            # Space heating simulation
            state = self.simulate_step(
                state=state,
                electrical_power=space_power_schedule[i],
                outdoor_temp=outdoor_temps[i],
                wind_speed=wind_speeds[i],
                precipitation=precipitation[i],
                solar_radiation=solar_radiation[i],
                dt_hours=dt_hours,
            )

            # DHW simulation (runs in parallel with space heating)
            cop_dhw = self.compute_cop_dhw(outdoor_temps[i], state.dhw_temperature)
            dhw_thermal_power = cop_dhw * dhw_power_schedule[i]

            new_dhw = self.simulate_dhw_step(
                dhw_temp=state.dhw_temperature,
                dhw_power_thermal=dhw_thermal_power,
                hour_of_day=current_hour % 24.0,
                ambient_temp=20.0,  # indoor ambient near tank
                dt_hours=dt_hours,
            )
            state.dhw_temperature = new_dhw

            room_temps[i + 1] = state.room_temperature
            slab_temps[i + 1] = state.slab_temperature
            upper_temps[i + 1] = state.upper_floor_temperature
            lower_temps[i + 1] = state.lower_floor_temperature
            dhw_temps[i + 1] = new_dhw

            current_hour += dt_hours

        return room_temps, slab_temps, upper_temps, lower_temps, dhw_temps

    def get_state_matrices(
        self,
        outdoor_temp: float,
        wind_speed: float = 0.0,
        precipitation: float = 0.0,
        dt_hours: float = 0.25,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get discrete-time state-space matrices for the thermal model.

        For single-zone: State x = [T_room, T_slab], Input u = P_el
        For two-zone: State x = [T_upper, T_lower, T_slab, T_buf], Input u = P_el

        Returns: (A, B, E) for x[k+1] = A*x[k] + B*u[k] + E*d[k]
        """
        p = self.params

        if not p.two_zone_enabled:
            return self._get_state_matrices_single(
                outdoor_temp, wind_speed, precipitation, dt_hours
            )
        return self._get_state_matrices_two_zone(
            outdoor_temp, wind_speed, precipitation, dt_hours
        )

    def _get_state_matrices_single(
        self,
        outdoor_temp: float,
        wind_speed: float,
        precipitation: float,
        dt_hours: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """State-space matrices for the single-zone model."""
        p = self.params
        cop = self.compute_cop(outdoor_temp)
        u_eff = self.effective_heat_loss_coefficient(
            p.heat_loss_coefficient, wind_speed, precipitation
        )

        a11 = -(p.slab_heat_transfer + u_eff) / p.room_thermal_mass
        a12 = p.slab_heat_transfer / p.room_thermal_mass
        a21 = -p.slab_heat_transfer / p.slab_thermal_mass
        a22 = -p.slab_heat_transfer / p.slab_thermal_mass

        A_cont = np.array([[a11, a12], [a21, a22]])
        B_cont = np.array([[0.0], [cop / p.slab_thermal_mass]])
        E_cont = np.array([
            [u_eff / p.room_thermal_mass, 1.0 / p.room_thermal_mass],
            [0.0, 0.0],
        ])

        A = np.eye(2) + A_cont * dt_hours
        B = B_cont * dt_hours
        E = E_cont * dt_hours
        return A, B, E

    def _get_state_matrices_two_zone(
        self,
        outdoor_temp: float,
        wind_speed: float,
        precipitation: float,
        dt_hours: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """State-space matrices for the two-zone model.

        State: [T_upper, T_lower, T_slab, T_buf]
        """
        p = self.params
        cop = self.compute_cop(outdoor_temp)

        u_upper = self.effective_heat_loss_coefficient(
            p.upper_floor_heat_loss, wind_speed, precipitation
        )
        u_lower = self.effective_heat_loss_coefficient(
            p.lower_floor_heat_loss, wind_speed * 0.5, precipitation * 0.5
        )

        C_u = p.upper_floor_thermal_mass
        C_l = p.lower_floor_thermal_mass
        C_s = p.slab_thermal_mass
        C_b = max(p.buffer_tank_thermal_mass, 0.01)

        k_inter = p.inter_zone_transfer
        k_slab = p.slab_heat_transfer
        k_buf = p.buffer_tank_heat_loss
        f_rad = p.radiator_power_fraction

        A_cont = np.zeros((4, 4))
        # T_upper row
        A_cont[0, 0] = -(u_upper + k_inter) / C_u
        A_cont[0, 1] = k_inter / C_u
        # T_lower row
        A_cont[1, 0] = k_inter / C_l
        A_cont[1, 1] = -(u_lower + k_inter) / C_l
        A_cont[1, 2] = k_slab / C_l
        # T_slab row
        A_cont[2, 1] = -k_slab / C_s
        A_cont[2, 2] = -k_slab / C_s
        # T_buffer row
        A_cont[3, 3] = -k_buf / C_b

        # B (4x1) - input = P_electrical
        B_cont = np.array([
            [f_rad * cop / C_u],
            [0.0],
            [(1 - f_rad) * cop / C_s],
            [cop / C_b],
        ])

        # E (4x2) - disturbance = [T_outdoor, Q_internal_total]
        E_cont = np.array([
            [u_upper / C_u, 0.5 / C_u],
            [u_lower / C_l, 0.5 / C_l],
            [0.0, 0.0],
            [k_buf / C_b, 0.0],
        ])

        A = np.eye(4) + A_cont * dt_hours
        B = B_cont * dt_hours
        E = E_cont * dt_hours
        return A, B, E

    def update_slab_from_return_temp(
        self, state: ThermalState, return_temp: float
    ) -> ThermalState:
        """Update slab temperature estimate from actual floor return sensor.

        The return temperature of the floor heating circuit is a good proxy
        for the average slab temperature (typically return_temp ≈ T_slab - 2°C
        to T_slab + 0°C depending on flow rate and delta-T).

        We use a simple weighted merge: 70% sensor, 30% model to smooth noise.
        """
        if return_temp is None:
            return state

        # Return temp is typically close to slab temp
        estimated_slab = return_temp + 1.0

        # Weighted merge with model prediction
        merged_slab = 0.7 * estimated_slab + 0.3 * state.slab_temperature

        state.slab_temperature = merged_slab
        state.floor_return_temperature = return_temp
        return state
