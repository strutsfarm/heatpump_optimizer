"""Thermal model for house with slab floor heating.

This module models the thermal dynamics of a house with a concrete slab floor
heated by an air-to-water heat pump. The model uses a two-node thermal network:

    Node 1: Room air temperature (T_room)
    Node 2: Slab floor temperature (T_slab)

The thermal dynamics are governed by:

    C_room * dT_room/dt = Q_slab_to_room - Q_loss + Q_internal
    C_slab * dT_slab/dt = Q_hp - Q_slab_to_room

Where:
    C_room = thermal mass of room air + furnishings (kWh/°C)
    C_slab = thermal mass of concrete slab (kWh/°C)
    Q_slab_to_room = k_slab * (T_slab - T_room) (kW)
    Q_loss = U * (T_room - T_outdoor_eff) (kW)
    Q_hp = COP(T_outdoor) * P_electrical (kW thermal)
    Q_internal = internal heat gains (solar, occupancy, appliances)
    T_outdoor_eff = effective outdoor temp accounting for wind and rain
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
    WIND_CHILL_FACTOR,
    RAIN_COOLING_FACTOR,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ThermalParameters:
    """Parameters for the thermal model."""

    # Thermal masses (kWh/°C)
    room_thermal_mass: float = DEFAULT_HOUSE_THERMAL_MASS
    slab_thermal_mass: float = DEFAULT_SLAB_THERMAL_MASS

    # Heat transfer coefficients (kW/°C)
    heat_loss_coefficient: float = DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT
    slab_heat_transfer: float = DEFAULT_SLAB_HEAT_TRANSFER

    # Heat pump parameters
    cop_nominal: float = DEFAULT_HEAT_PUMP_COP_NOMINAL
    cop_reference_temp: float = 7.0  # °C - outdoor temp at which COP is nominal
    max_electrical_power: float = DEFAULT_HEAT_PUMP_MAX_POWER  # kW
    min_electrical_power: float = DEFAULT_HEAT_PUMP_MIN_POWER  # kW

    # Internal gains (kW) - baseline heat from occupancy, appliances, etc.
    internal_gains: float = 0.3

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
        )
        return cls(
            room_thermal_mass=config.get(CONF_HOUSE_THERMAL_MASS, DEFAULT_HOUSE_THERMAL_MASS),
            slab_thermal_mass=config.get(CONF_SLAB_THERMAL_MASS, DEFAULT_SLAB_THERMAL_MASS),
            heat_loss_coefficient=config.get(
                CONF_HOUSE_HEAT_LOSS_COEFFICIENT, DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT
            ),
            slab_heat_transfer=config.get(CONF_SLAB_HEAT_TRANSFER, DEFAULT_SLAB_HEAT_TRANSFER),
            cop_nominal=config.get(CONF_HEAT_PUMP_COP_NOMINAL, DEFAULT_HEAT_PUMP_COP_NOMINAL),
            max_electrical_power=config.get(CONF_HEAT_PUMP_MAX_POWER, DEFAULT_HEAT_PUMP_MAX_POWER),
            min_electrical_power=config.get(CONF_HEAT_PUMP_MIN_POWER, DEFAULT_HEAT_PUMP_MIN_POWER),
        )


@dataclass
class WeatherForecastPoint:
    """A single point in the weather forecast."""

    timestamp: float  # Unix timestamp
    temperature: float  # °C
    wind_speed: float = 0.0  # m/s
    precipitation: float = 0.0  # mm/h


@dataclass
class ThermalState:
    """Current thermal state of the system."""

    room_temperature: float = 21.0  # °C
    slab_temperature: float = 22.0  # °C
    outdoor_temperature: float = 5.0  # °C


class ThermalModel:
    """Two-node thermal model of house with slab floor heating."""

    def __init__(self, params: ThermalParameters) -> None:
        """Initialize the thermal model."""
        self.params = params

    def compute_cop(self, outdoor_temp: float) -> float:
        """Compute heat pump COP as function of outdoor temperature.

        The COP of an air-source heat pump decreases as the outdoor temperature
        drops. We use a linear approximation based on the Carnot efficiency:

            COP ≈ COP_nominal * (1 + 0.025 * (T_outdoor - T_ref))

        This gives roughly:
        - COP drops ~2.5% per °C below reference temperature
        - At -15°C with ref 7°C: COP ≈ nominal * 0.45
        - At 15°C with ref 7°C: COP ≈ nominal * 1.2
        """
        delta = outdoor_temp - self.params.cop_reference_temp
        factor = max(0.3, 1.0 + 0.025 * delta)
        return self.params.cop_nominal * min(factor, 1.5)

    def effective_outdoor_temp(
        self, temp: float, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective outdoor temperature accounting for wind chill and rain.

        Wind increases convective heat loss from the building envelope.
        Rain increases evaporative cooling effects.
        """
        # Wind chill effect on building: increased heat loss coefficient
        # Rather than modifying the coefficient, we reduce effective outdoor temp
        wind_effect = wind_speed * 0.3  # rough wind chill approximation
        rain_effect = precipitation * 0.5  # cooling from rain

        return temp - wind_effect - rain_effect

    def effective_heat_loss_coefficient(
        self, wind_speed: float = 0.0, precipitation: float = 0.0
    ) -> float:
        """Compute effective heat loss coefficient accounting for weather."""
        base = self.params.heat_loss_coefficient
        wind_increase = WIND_CHILL_FACTOR * wind_speed
        rain_increase = RAIN_COOLING_FACTOR * precipitation
        return base + wind_increase + rain_increase

    def simulate_step(
        self,
        state: ThermalState,
        electrical_power: float,
        outdoor_temp: float,
        wind_speed: float = 0.0,
        precipitation: float = 0.0,
        dt_hours: float = 0.25,
    ) -> ThermalState:
        """Simulate one time step of the thermal model.

        Args:
            state: Current thermal state
            electrical_power: Electrical power input to heat pump (kW)
            outdoor_temp: Outdoor temperature (°C)
            wind_speed: Wind speed (m/s)
            precipitation: Precipitation rate (mm/h)
            dt_hours: Time step in hours

        Returns:
            New thermal state after the time step
        """
        p = self.params

        # Compute COP and thermal power
        cop = self.compute_cop(outdoor_temp)
        thermal_power = cop * electrical_power  # kW thermal

        # Effective heat loss coefficient
        u_eff = self.effective_heat_loss_coefficient(wind_speed, precipitation)

        # Heat flows (kW)
        q_slab_to_room = p.slab_heat_transfer * (state.slab_temperature - state.room_temperature)
        q_loss = u_eff * (state.room_temperature - outdoor_temp)
        q_internal = p.internal_gains

        # Temperature derivatives (°C/h)
        dT_room = (q_slab_to_room - q_loss + q_internal) / p.room_thermal_mass
        dT_slab = (thermal_power - q_slab_to_room) / p.slab_thermal_mass

        # Euler integration
        new_room_temp = state.room_temperature + dT_room * dt_hours
        new_slab_temp = state.slab_temperature + dT_slab * dt_hours

        return ThermalState(
            room_temperature=new_room_temp,
            slab_temperature=new_slab_temp,
            outdoor_temperature=outdoor_temp,
        )

    def simulate_trajectory(
        self,
        initial_state: ThermalState,
        power_schedule: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray | None = None,
        precipitation: np.ndarray | None = None,
        dt_hours: float = 0.25,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Simulate the full trajectory given a power schedule.

        Args:
            initial_state: Starting thermal state
            power_schedule: Array of electrical power values (kW) for each time step
            outdoor_temps: Array of outdoor temperatures (°C) for each time step
            wind_speeds: Array of wind speeds (m/s), optional
            precipitation: Array of precipitation rates (mm/h), optional
            dt_hours: Time step in hours

        Returns:
            Tuple of (room_temperatures, slab_temperatures) arrays
        """
        n_steps = len(power_schedule)

        if wind_speeds is None:
            wind_speeds = np.zeros(n_steps)
        if precipitation is None:
            precipitation = np.zeros(n_steps)

        room_temps = np.zeros(n_steps + 1)
        slab_temps = np.zeros(n_steps + 1)

        room_temps[0] = initial_state.room_temperature
        slab_temps[0] = initial_state.slab_temperature

        state = initial_state

        for i in range(n_steps):
            state = self.simulate_step(
                state=state,
                electrical_power=power_schedule[i],
                outdoor_temp=outdoor_temps[i],
                wind_speed=wind_speeds[i],
                precipitation=precipitation[i],
                dt_hours=dt_hours,
            )
            room_temps[i + 1] = state.room_temperature
            slab_temps[i + 1] = state.slab_temperature

        return room_temps, slab_temps

    def get_state_matrices(
        self,
        outdoor_temp: float,
        wind_speed: float = 0.0,
        precipitation: float = 0.0,
        dt_hours: float = 0.25,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Get discrete-time state-space matrices for the thermal model.

        State: x = [T_room, T_slab]
        Input: u = P_electrical
        Disturbance: d = [T_outdoor, Q_internal]

        Returns: (A, B, E) matrices for x[k+1] = A*x[k] + B*u[k] + E*d[k]
        """
        p = self.params
        cop = self.compute_cop(outdoor_temp)
        u_eff = self.effective_heat_loss_coefficient(wind_speed, precipitation)

        # Continuous-time A matrix
        a11 = -(p.slab_heat_transfer + u_eff) / p.room_thermal_mass
        a12 = p.slab_heat_transfer / p.room_thermal_mass
        a21 = -p.slab_heat_transfer / p.slab_thermal_mass
        a22 = -p.slab_heat_transfer / p.slab_thermal_mass  # self-cooling via room

        # Correct a22: slab only loses heat to room
        a22 = -p.slab_heat_transfer / p.slab_thermal_mass

        A_cont = np.array([[a11, a12], [a21, a22]])

        # B matrix (input = electrical power, thermal output = COP * P_el)
        B_cont = np.array([
            [0.0],
            [cop / p.slab_thermal_mass],
        ])

        # E matrix (disturbance = [T_outdoor, Q_internal])
        E_cont = np.array([
            [u_eff / p.room_thermal_mass, 1.0 / p.room_thermal_mass],
            [0.0, 0.0],
        ])

        # Discretize using Euler method (sufficient for 15-min steps)
        A = np.eye(2) + A_cont * dt_hours
        B = B_cont * dt_hours
        E = E_cont * dt_hours

        return A, B, E
