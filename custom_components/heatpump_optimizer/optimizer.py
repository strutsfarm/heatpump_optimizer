"""Model Predictive Control optimizer for heat pump cost minimization.

This module implements an MPC-based optimizer that determines the optimal
heat pump power schedule over a 24-hour horizon to minimize electricity costs
while maintaining indoor temperature within comfort bounds.

The optimization problem is:

    minimize   Σ price[k] * P_el[k] * dt + comfort_weight * Σ penalty[k]
    subject to T_min[k] ≤ T_room[k] ≤ T_max[k]  (soft constraints with penalty)
               P_min ≤ P_el[k] ≤ P_max
               Thermal dynamics (state-space model)

The solver uses scipy.optimize.minimize with the L-BFGS-B method, which
handles box constraints efficiently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from scipy.optimize import minimize, LinearConstraint

from .thermal_model import ThermalModel, ThermalParameters, ThermalState

_LOGGER = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of the MPC optimization."""

    power_schedule: list[float]  # kW electrical per time step
    room_temp_trajectory: list[float]  # °C predicted room temp
    slab_temp_trajectory: list[float]  # °C predicted slab temp
    timestamps: list[datetime]  # timestamp for each step
    prices: list[float]  # electricity price per step
    predicted_cost: float  # total cost in currency units
    baseline_cost: float  # cost with constant-temp strategy
    predicted_savings: float  # savings vs baseline
    savings_percentage: float  # savings as percentage
    optimal_setpoints: list[float]  # recommended setpoints per step
    status: str  # optimization status
    solve_time_ms: float = 0.0  # solver time in milliseconds


@dataclass
class OptimizationConfig:
    """Configuration for the optimizer."""

    # Temperature constraints
    target_temp: float = 21.0
    min_temp: float = 19.0
    max_temp: float = 23.0
    comfort_temp_day: float = 21.0
    comfort_temp_night: float = 19.5
    day_start_hour: int = 7
    day_end_hour: int = 22

    # Optimization parameters
    horizon_hours: float = 24.0
    time_step_minutes: float = 15.0
    price_weight: float = 1.0
    comfort_weight: float = 5.0

    @property
    def n_steps(self) -> int:
        """Number of optimization steps."""
        return int(self.horizon_hours * 60 / self.time_step_minutes)

    @property
    def dt_hours(self) -> float:
        """Time step in hours."""
        return self.time_step_minutes / 60.0

    def get_comfort_temp(self, hour: float) -> float:
        """Get comfort temperature for a given hour of day."""
        if self.day_start_hour <= hour < self.day_end_hour:
            return self.comfort_temp_day
        return self.comfort_temp_night

    def get_temp_bounds(self, hour: float) -> tuple[float, float]:
        """Get temperature bounds for a given hour."""
        comfort = self.get_comfort_temp(hour)
        # Allow wider range during night
        if self.day_start_hour <= hour < self.day_end_hour:
            return (self.min_temp, self.max_temp)
        return (self.min_temp - 0.5, self.max_temp)


class HeatPumpOptimizer:
    """MPC-based heat pump cost optimizer."""

    def __init__(
        self,
        thermal_model: ThermalModel,
        config: OptimizationConfig,
    ) -> None:
        """Initialize the optimizer."""
        self.model = thermal_model
        self.config = config

    def optimize(
        self,
        initial_state: ThermalState,
        prices: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray | None = None,
        precipitation: np.ndarray | None = None,
        start_time: datetime | None = None,
    ) -> OptimizationResult:
        """Run the MPC optimization.

        Args:
            initial_state: Current thermal state of the house
            prices: Electricity prices (currency/kWh) for each time step
            outdoor_temps: Outdoor temperature forecast (°C) for each time step
            wind_speeds: Wind speed forecast (m/s), optional
            precipitation: Precipitation forecast (mm/h), optional
            start_time: Start time of the optimization horizon

        Returns:
            OptimizationResult with optimal power schedule and predictions
        """
        import time

        t_start = time.monotonic()

        n_steps = min(len(prices), len(outdoor_temps), self.config.n_steps)
        dt = self.config.dt_hours

        if wind_speeds is None:
            wind_speeds = np.zeros(n_steps)
        if precipitation is None:
            precipitation = np.zeros(n_steps)

        if start_time is None:
            start_time = datetime.now()

        # Truncate arrays to n_steps
        prices = prices[:n_steps]
        outdoor_temps = outdoor_temps[:n_steps]
        wind_speeds = wind_speeds[:n_steps]
        precipitation = precipitation[:n_steps]

        _LOGGER.debug(
            "Starting optimization: %d steps, dt=%.2fh, horizon=%.1fh",
            n_steps, dt, n_steps * dt,
        )

        # Compute comfort targets for each time step
        comfort_targets = np.array([
            self.config.get_comfort_temp(
                (start_time + timedelta(hours=i * dt)).hour
                + (start_time + timedelta(hours=i * dt)).minute / 60.0
            )
            for i in range(n_steps)
        ])

        temp_min_bounds = np.array([
            self.config.get_temp_bounds(
                (start_time + timedelta(hours=i * dt)).hour
                + (start_time + timedelta(hours=i * dt)).minute / 60.0
            )[0]
            for i in range(n_steps)
        ])

        temp_max_bounds = np.array([
            self.config.get_temp_bounds(
                (start_time + timedelta(hours=i * dt)).hour
                + (start_time + timedelta(hours=i * dt)).minute / 60.0
            )[1]
            for i in range(n_steps)
        ])

        p_min = self.model.params.min_electrical_power
        p_max = self.model.params.max_electrical_power

        def objective(power_schedule: np.ndarray) -> float:
            """Compute the total cost objective function.

            Cost = Σ price[k] * P_el[k] * dt  (electricity cost)
                 + comfort_weight * Σ max(0, T_min[k] - T_room[k])²  (undershoot penalty)
                 + comfort_weight * Σ max(0, T_room[k] - T_max[k])²  (overshoot penalty)
                 + 0.1 * comfort_weight * Σ (T_room[k] - comfort_target[k])²  (comfort tracking)
            """
            room_temps, _ = self.model.simulate_trajectory(
                initial_state=initial_state,
                power_schedule=power_schedule,
                outdoor_temps=outdoor_temps,
                wind_speeds=wind_speeds,
                precipitation=precipitation,
                dt_hours=dt,
            )

            # Electricity cost
            energy_cost = np.sum(prices * power_schedule * dt) * self.config.price_weight

            # Temperature constraint violations (soft constraints)
            room_t = room_temps[1:]  # skip initial state
            undershoot = np.maximum(0, temp_min_bounds - room_t)
            overshoot = np.maximum(0, room_t - temp_max_bounds)

            penalty = self.config.comfort_weight * (
                np.sum(undershoot ** 2) * 10.0  # heavy penalty for too cold
                + np.sum(overshoot ** 2) * 5.0  # moderate penalty for too warm
            )

            # Comfort tracking (gentle pull toward comfort target)
            comfort_deviation = room_t - comfort_targets
            comfort_cost = 0.05 * self.config.comfort_weight * np.sum(comfort_deviation ** 2)

            # Smoothness penalty to avoid rapid on/off cycling
            if len(power_schedule) > 1:
                smoothness = 0.01 * np.sum(np.diff(power_schedule) ** 2)
            else:
                smoothness = 0.0

            total = energy_cost + penalty + comfort_cost + smoothness
            return total

        def objective_gradient(power_schedule: np.ndarray) -> np.ndarray:
            """Compute gradient using finite differences."""
            grad = np.zeros_like(power_schedule)
            eps = 0.01  # kW perturbation
            f0 = objective(power_schedule)
            for i in range(len(power_schedule)):
                power_schedule[i] += eps
                f1 = objective(power_schedule)
                grad[i] = (f1 - f0) / eps
                power_schedule[i] -= eps
            return grad

        # Initial guess: proportional to price inverse (heat more when cheap)
        price_normalized = prices / (np.mean(prices) + 1e-6)
        initial_power = p_max * np.clip(1.5 - price_normalized, 0.2, 1.0)
        initial_power = np.clip(initial_power, p_min, p_max)

        # Bounds for each power value
        bounds = [(p_min, p_max)] * n_steps

        # Optimize using L-BFGS-B
        try:
            result = minimize(
                objective,
                initial_power,
                method="L-BFGS-B",
                bounds=bounds,
                options={
                    "maxiter": 200,
                    "ftol": 1e-6,
                    "disp": False,
                },
            )
            optimal_power = result.x
            status = "optimal" if result.success else f"suboptimal ({result.message})"
        except Exception as e:
            _LOGGER.error("Optimization failed: %s", e)
            optimal_power = initial_power
            status = f"failed ({e})"

        # Simulate with optimal schedule
        room_temps, slab_temps = self.model.simulate_trajectory(
            initial_state=initial_state,
            power_schedule=optimal_power,
            outdoor_temps=outdoor_temps,
            wind_speeds=wind_speeds,
            precipitation=precipitation,
            dt_hours=dt,
        )

        # Compute baseline cost (constant power to maintain target temp)
        baseline_power = self._compute_baseline_power(
            initial_state, outdoor_temps, wind_speeds, precipitation, dt
        )
        baseline_cost = float(np.sum(prices * baseline_power * dt))
        predicted_cost = float(np.sum(prices * optimal_power * dt))
        savings = baseline_cost - predicted_cost

        # Generate timestamps
        timestamps = [
            start_time + timedelta(hours=i * dt)
            for i in range(n_steps)
        ]

        # Convert optimal power to setpoint recommendations
        optimal_setpoints = self._power_to_setpoints(
            optimal_power, room_temps[:-1], outdoor_temps
        )

        t_elapsed = (time.monotonic() - t_start) * 1000

        _LOGGER.info(
            "Optimization completed in %.0fms: cost=%.2f, baseline=%.2f, savings=%.1f%%",
            t_elapsed,
            predicted_cost,
            baseline_cost,
            (savings / baseline_cost * 100) if baseline_cost > 0 else 0,
        )

        return OptimizationResult(
            power_schedule=optimal_power.tolist(),
            room_temp_trajectory=room_temps.tolist(),
            slab_temp_trajectory=slab_temps.tolist(),
            timestamps=timestamps,
            prices=prices.tolist(),
            predicted_cost=predicted_cost,
            baseline_cost=baseline_cost,
            predicted_savings=savings,
            savings_percentage=(savings / baseline_cost * 100) if baseline_cost > 0 else 0,
            optimal_setpoints=optimal_setpoints,
            status=status,
            solve_time_ms=t_elapsed,
        )

    def _compute_baseline_power(
        self,
        initial_state: ThermalState,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray,
        precipitation: np.ndarray,
        dt: float,
    ) -> np.ndarray:
        """Compute baseline power schedule (constant temperature strategy).

        This represents the "naive" strategy of maintaining a constant
        temperature without any price optimization.
        """
        n_steps = len(outdoor_temps)
        target = self.config.target_temp
        p = self.model.params

        # Simple proportional control baseline
        baseline_power = np.zeros(n_steps)
        state = initial_state

        for i in range(n_steps):
            # Heat loss at current conditions
            u_eff = self.model.effective_heat_loss_coefficient(
                wind_speeds[i], precipitation[i]
            )
            heat_loss = u_eff * (state.room_temperature - outdoor_temps[i])
            cop = self.model.compute_cop(outdoor_temps[i])

            # Power needed to compensate heat loss
            thermal_need = max(0, heat_loss - p.internal_gains)
            # Add proportional correction for temperature error
            temp_error = target - state.room_temperature
            correction = p.slab_heat_transfer * temp_error * 0.5

            electrical_power = max(0, (thermal_need + correction) / cop)
            electrical_power = np.clip(electrical_power, p.min_electrical_power, p.max_electrical_power)
            baseline_power[i] = electrical_power

            state = self.model.simulate_step(
                state, electrical_power, outdoor_temps[i],
                wind_speeds[i], precipitation[i], dt,
            )

        return baseline_power

    def _power_to_setpoints(
        self,
        power_schedule: np.ndarray,
        room_temps: np.ndarray,
        outdoor_temps: np.ndarray,
    ) -> list[float]:
        """Convert power schedule to equivalent temperature setpoints.

        Maps the optimal power level to a displacement/setpoint value that
        the heat pump controller can use.
        """
        setpoints = []
        p_range = self.model.params.max_electrical_power - self.model.params.min_electrical_power

        for i, (power, room_t) in enumerate(zip(power_schedule, room_temps)):
            # Normalize power to 0-1 range
            p_norm = (power - self.model.params.min_electrical_power) / max(p_range, 0.1)
            p_norm = np.clip(p_norm, 0, 1)

            # Map to setpoint: higher power → higher setpoint displacement
            displacement = p_norm * (self.config.max_temp - self.config.min_temp)
            setpoint = self.config.min_temp + displacement

            setpoints.append(round(float(setpoint), 1))

        return setpoints

    def get_current_action(
        self, result: OptimizationResult, current_time: datetime
    ) -> dict[str, Any]:
        """Get the current recommended action from the optimization result.

        Returns the action (power, setpoint, mode) for the current time step.
        """
        if not result.timestamps:
            return {
                "power": self.model.params.min_electrical_power,
                "setpoint": self.config.target_temp,
                "mode": "idle",
                "price": 0.0,
            }

        # Find the current time step
        for i, ts in enumerate(result.timestamps):
            if i + 1 < len(result.timestamps):
                if ts <= current_time < result.timestamps[i + 1]:
                    break
            else:
                # Last step
                i = len(result.timestamps) - 1
                break

        power = result.power_schedule[i]
        setpoint = result.optimal_setpoints[i]
        price = result.prices[i]

        # Determine mode based on power level
        p_range = self.model.params.max_electrical_power - self.model.params.min_electrical_power
        p_norm = (power - self.model.params.min_electrical_power) / max(p_range, 0.1)

        if p_norm < 0.1:
            mode = "off"
        elif p_norm < 0.4:
            mode = "eco"
        elif p_norm < 0.7:
            mode = "normal"
        elif p_norm < 0.9:
            mode = "pre_heat"
        else:
            mode = "boost"

        return {
            "power": round(power, 2),
            "setpoint": setpoint,
            "mode": mode,
            "price": round(price, 4),
            "power_normalized": round(p_norm, 2),
        }
