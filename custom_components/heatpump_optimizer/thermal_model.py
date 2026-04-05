"""Two-zone thermal model for house with radiator and slab floor heating.

This module models the thermal dynamics of a house with two heating zones
served by an air-to-water heat pump with a buffer tank:

    Zone 1 (Upper Floor): Radiator heating — fast thermal response (low mass)
    Zone 2 (Lower Floor): Slab floor heating — slow thermal response (high mass)
    Buffer Tank: 35L buffer coupling the heat pump to both circuits

The thermal dynamics are governed by:

    C_upper * dT_upper/dt = Q_rad - Q_loss_upper + Q_inter + Q_solar_upper + Q_internal_upper
    C_slab  * dT_slab/dt  = Q_floor_hp - Q_slab_to_lower
    C_lower * dT_lower/dt = Q_slab_to_lower - Q_loss_lower - Q_inter + Q_solar_lower + Q_internal_lower
    C_buf   * dT_buf/dt   = Q_hp - Q_rad_draw - Q_floor_draw - Q_buf_loss

Where:
    Q_rad = heat delivered to upper floor via radiators
    Q_floor_hp = heat delivered to slab via floor heating circuit
    Q_inter = k_inter * (T_lower - T_upper)  (inter-zone, positive = heat rises)
    Q_solar = solar radiation heat gain through windows
    Q_hp = COP(T_outdoor) * P_electrical  (heat pump output to buffer)
    Q_buf_loss = k_buf * (T_buf - T_ambient)

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
    WIND_CHILL_FACTOR,
    RAIN_COOLING_FACTOR,
)

_LOGGER = logging.getLogger(__name__)

# Specific heat capacity of water: ~0.00116 kWh/(liter·°C)
WATER_SPECIFIC_HEAT: float = 0.00116


@dataclass
class ThermalParameters:
    """Parameters for the two-zone thermal model."""

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

    # Heat pump parameters
    cop_nominal: float = DEFAULT_HEAT_PUMP_COP_NOMINAL
    cop_reference_temp: float = 7.0  # °C
    max_electrical_power: float = DEFAULT_HEAT_PUMP_MAX_POWER  # kW
    min_electrical_power: float = DEFAULT_HEAT_PUMP_MIN_POWER  # kW

    # Internal gains (kW) - baseline heat from occupancy, appliances, etc.
    internal_gains: float = 0.3

    # Whether to use the enhanced two-zone model
    two_zone_enabled: bool = False

    @property
    def buffer_tank_thermal_mass(self) -> float:
        """Thermal mass of buffer tank in kWh/°C."""
        return self.buffer_tank_volume * WATER_SPECIFIC_HEAT

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
    """Current thermal state of the two-zone system.

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


class ThermalModel:
    """Thermal model supporting both single-zone and two-zone operation."""

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

    def effective_outdoor_temp(
        self, temp: float, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective outdoor temperature accounting for wind chill and rain."""
        wind_effect = wind_speed * 0.3
        rain_effect = precipitation * 0.5
        return temp - wind_effect - rain_effect

    def effective_heat_loss_coefficient(
        self, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective heat loss coefficient accounting for weather."""
        base = self.params.heat_loss_coefficient
        wind_increase = WIND_CHILL_FACTOR * wind_speed
        rain_increase = RAIN_COOLING_FACTOR * precipitation
        return base + wind_increase + rain_increase

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

        u_eff = self.effective_heat_loss_coefficient(wind_speed, precipitation)
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

        # Weather-adjusted heat loss multiplier
        wind_add = WIND_CHILL_FACTOR * wind_speed
        rain_add = RAIN_COOLING_FACTOR * precipitation

        u_upper = p.upper_floor_heat_loss + wind_add + rain_add
        u_lower = p.lower_floor_heat_loss + wind_add * 0.5 + rain_add * 0.5  # lower floor less exposed

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
        # HP heats buffer. Buffer supplies both circuits.
        C_buf = p.buffer_tank_thermal_mass
        if C_buf < 1e-6:
            C_buf = 0.04  # fallback for 35L

        # Heat drawn from buffer to radiators (upper floor)
        # Proportional control: radiators draw heat based on buffer-room delta
        rad_fraction = p.radiator_power_fraction
        q_rad_from_buf = rad_fraction * thermal_power  # simplified: direct fraction
        q_floor_from_buf = (1.0 - rad_fraction) * thermal_power

        # Buffer tank loss to ambient (assume ~20°C ambient indoors)
        q_buf_loss = p.buffer_tank_heat_loss * (T_buf - 20.0)

        dT_buf = (thermal_power - q_rad_from_buf - q_floor_from_buf - q_buf_loss) / max(C_buf, 0.01)

        # --- Slab dynamics ---
        # Floor heating heats the slab; slab radiates to lower floor room air
        q_slab_to_lower = p.slab_heat_transfer * (T_slab - T_lower)
        dT_slab = (q_floor_from_buf - q_slab_to_lower) / p.slab_thermal_mass

        # --- Inter-zone heat transfer ---
        # Heat rises from lower to upper (convection through open layout)
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
        u_eff = self.effective_heat_loss_coefficient(wind_speed, precipitation)

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
        wind_add = WIND_CHILL_FACTOR * wind_speed
        rain_add = RAIN_COOLING_FACTOR * precipitation
        u_upper = p.upper_floor_heat_loss + wind_add + rain_add
        u_lower = p.lower_floor_heat_loss + wind_add * 0.5 + rain_add * 0.5

        C_u = p.upper_floor_thermal_mass
        C_l = p.lower_floor_thermal_mass
        C_s = p.slab_thermal_mass
        C_b = max(p.buffer_tank_thermal_mass, 0.01)

        k_inter = p.inter_zone_transfer
        k_slab = p.slab_heat_transfer
        k_buf = p.buffer_tank_heat_loss
        f_rad = p.radiator_power_fraction

        # Continuous-time A (4x4)
        A_cont = np.array([
            [-(u_upper + k_inter) / C_u, k_inter / C_u,    0.0,           f_rad / C_u],
            [k_inter / C_l,    -(u_lower + k_inter + k_slab) / C_l, k_slab / C_l, 0.0],
            [0.0,              -k_slab / C_s,              -k_slab / C_s, (1 - f_rad) / C_s],
            [0.0,              0.0,                         0.0,          -(k_buf + 1.0) / C_b],
        ])
        # Note: the A matrix coupling from buffer is simplified. Full version below.
        # Recalculate properly:
        A_cont = np.zeros((4, 4))
        # T_upper row
        A_cont[0, 0] = -(u_upper + k_inter) / C_u
        A_cont[0, 1] = k_inter / C_u
        # T_lower row
        A_cont[1, 0] = k_inter / C_l  # heat from lower loses to upper
        A_cont[1, 1] = -(u_lower + k_inter) / C_l
        A_cont[1, 2] = k_slab / C_l  # heat from slab to lower
        # T_slab row
        A_cont[2, 1] = -k_slab / C_s  # slab loses to lower
        A_cont[2, 2] = -k_slab / C_s
        # T_buffer row
        A_cont[3, 3] = -k_buf / C_b

        # B (4x1) - input = P_electrical
        B_cont = np.array([
            [0.0],
            [0.0],
            [(1 - f_rad) * cop / C_s],
            [cop / C_b],  # HP heats buffer
        ])
        # Radiator heat goes to upper floor via buffer
        # Simplified: direct injection
        B_cont[0, 0] = f_rad * cop / C_u

        # E (4x2) - disturbance = [T_outdoor, Q_internal_total]
        E_cont = np.array([
            [u_upper / C_u, 0.5 / C_u],
            [u_lower / C_l, 0.5 / C_l],
            [0.0, 0.0],
            [k_buf / C_b, 0.0],  # buffer loses to ~ambient (proxy outdoor)
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
        # Apply a small offset: slab is usually 1-2°C warmer than return
        estimated_slab = return_temp + 1.0

        # Weighted merge with model prediction
        merged_slab = 0.7 * estimated_slab + 0.3 * state.slab_temperature

        state.slab_temperature = merged_slab
        state.floor_return_temperature = return_temp
        return state
