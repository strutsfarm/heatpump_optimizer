"""Model Predictive Control optimizer for heat pump cost minimization.

This module implements a TRUE predictive MPC optimizer that determines the
optimal heat pump power schedule over a 24-hour horizon to minimize electricity
costs while maintaining indoor temperature and DHW within comfort bounds.

KEY PREDICTIVE FEATURES (anticipatory, not just reactive):

1. **Solar Anticipation**: If high solar radiation is forecasted in next 12-24h,
   REDUCE current slab pre-heating because solar will provide free heat later.
   This saves money by not pre-heating what the sun will heat for free.

2. **Wind/Rain Anticipation**: If high wind/rain is forecasted, INCREASE current
   pre-heating during cheap electricity periods to buffer against upcoming
   higher heat loss. The slab thermal mass stores this heat.

3. **DHW Co-optimization**: Coordinate space heating and DHW heating to use
   the heat pump capacity optimally. DHW is heated during cheap electricity
   when possible, subject to minimum temperature constraints.

The optimization problem is:

    minimize   Σ price[k] * (P_space[k] + P_dhw[k]) * dt
             + comfort_weight * Σ penalty_space[k]
             + dhw_weight * Σ penalty_dhw[k]
    subject to T_min ≤ T_room[k] ≤ T_max  (soft constraint)
               T_dhw[k] ≥ T_dhw_min  (hard-ish constraint)
               P_space[k] + P_dhw[k] ≤ P_max  (capacity constraint)
               P_min ≤ P_space[k], P_dhw[k] ≤ P_max
               Thermal dynamics (two-zone + DHW state model)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from scipy.optimize import minimize

from .thermal_model import ThermalModel, ThermalParameters, ThermalState

_LOGGER = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of the MPC optimization."""

    power_schedule: list[float]  # kW electrical per time step (space heating)
    room_temp_trajectory: list[float]  # °C predicted avg room temp
    slab_temp_trajectory: list[float]  # °C predicted slab temp
    timestamps: list[datetime]  # timestamp for each step
    prices: list[float]  # electricity price per step
    predicted_cost: float  # total cost in currency units
    baseline_cost: float  # cost with constant-temp strategy
    predicted_savings: float  # savings vs baseline
    savings_percentage: float  # savings as percentage
    optimal_setpoints: list[float]  # recommended setpoints per step
    status: str  # optimization status
    solve_time_ms: float = 0.0

    # Two-zone trajectories
    upper_temp_trajectory: list[float] = field(default_factory=list)
    lower_temp_trajectory: list[float] = field(default_factory=list)
    solar_gain_trajectory: list[float] = field(default_factory=list)
    upper_setpoints: list[float] = field(default_factory=list)
    lower_setpoints: list[float] = field(default_factory=list)

    # DHW optimization results
    dhw_power_schedule: list[float] = field(default_factory=list)
    dhw_temp_trajectory: list[float] = field(default_factory=list)
    dhw_heating_cost: float = 0.0

    # Predictive insights
    predictive_info: dict[str, Any] = field(default_factory=dict)


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
        if self.day_start_hour <= hour < self.day_end_hour:
            return (self.min_temp, self.max_temp)
        return (self.min_temp - 0.5, self.max_temp)


class HeatPumpOptimizer:
    """MPC-based heat pump cost optimizer with predictive weather anticipation and DHW."""

    def __init__(
        self,
        thermal_model: ThermalModel,
        config: OptimizationConfig,
    ) -> None:
        """Initialize the optimizer."""
        self.model = thermal_model
        self.config = config

    # ------------------------------------------------------------------
    # Predictive weather analysis
    # ------------------------------------------------------------------

    def _analyze_forecast_trajectory(
        self,
        solar_radiation: np.ndarray,
        wind_speeds: np.ndarray,
        precipitation: np.ndarray,
        outdoor_temps: np.ndarray,
        dt_hours: float,
    ) -> dict[str, Any]:
        """Analyze the full 24-hour forecast for anticipatory control signals.

        This is the core of the PREDICTIVE optimization — looking ahead to
        determine how current actions should be modified.

        Returns a dict with anticipatory signals:
        - future_solar_energy: total forecasted solar gain (kWh) over horizon
        - solar_peak_hours: indices of high solar radiation periods
        - future_wind_loss_factor: weighted future wind heat loss increase
        - future_rain_loss_factor: weighted future rain heat loss increase
        - pre_heat_urgency: 0-1 signal indicating how much to pre-heat now
        - solar_savings_potential: how much the sun will heat for free
        """
        n = len(solar_radiation)
        if n == 0:
            return {
                "future_solar_energy_kwh": 0.0,
                "solar_peak_indices": [],
                "pre_heat_urgency": 0.5,
                "solar_reduction_factor": 1.0,
                "wind_anticipation_factor": 1.0,
                "rain_anticipation_factor": 1.0,
            }

        # --- Solar analysis ---
        # Compute total solar gain over the horizon
        solar_gains_kw = np.array([
            self.model.compute_solar_gain(sr) for sr in solar_radiation
        ])
        total_solar_energy = float(np.sum(solar_gains_kw) * dt_hours)  # kWh

        # Find peak solar periods (>200 W/m² is significant)
        solar_peak_mask = solar_radiation > 200.0
        solar_peak_indices = np.where(solar_peak_mask)[0].tolist()

        # Solar energy in the FUTURE (next 6-24 hours)
        # Weight more heavily the solar coming in the next 6-12 hours
        n_6h = min(int(6 / dt_hours), n)
        n_12h = min(int(12 / dt_hours), n)
        future_solar_6_12h = float(np.sum(solar_gains_kw[n_6h:n_12h]) * dt_hours)

        # If lots of solar is coming in 6-12h, reduce current heating
        # The slab has enough thermal mass to coast through to solar period
        typical_heat_loss = (
            self.model.params.heat_loss_coefficient
            * (self.config.target_temp - np.mean(outdoor_temps))
        )
        if typical_heat_loss > 0:
            solar_fraction = min(future_solar_6_12h / max(typical_heat_loss * 6, 0.1), 1.0)
        else:
            solar_fraction = 0.0

        # Solar reduction factor: 1.0 = no reduction, 0.5 = reduce heating by 50%
        # Only reduce slab pre-heating, not immediate comfort heating
        solar_reduction = 1.0 - 0.4 * solar_fraction  # max 40% reduction

        # --- Wind analysis ---
        # Look at future wind speeds and compute anticipated heat loss increase
        wind_weights = np.exp(-np.arange(n) * dt_hours / 12.0)  # decay over 12h
        wind_weights /= wind_weights.sum()
        avg_future_wind = float(np.sum(wind_speeds * wind_weights))
        wind_anticipation = 1.0 + self.model.params.wind_sensitivity * avg_future_wind

        # --- Rain analysis ---
        # Upcoming rain increases heat loss
        rain_weights = np.exp(-np.arange(n) * dt_hours / 12.0)
        rain_weights /= rain_weights.sum()
        avg_future_precip = float(np.sum(precipitation * rain_weights))
        rain_anticipation = 1.0
        if avg_future_precip > 0.1:
            rain_intensity = min(avg_future_precip / 2.0, 1.0)
            rain_anticipation = 1.0 + (
                self.model.params.rain_heat_loss_multiplier - 1.0
            ) * rain_intensity

        # --- Pre-heat urgency ---
        # High if bad weather (wind + rain) is coming AND cheap electricity now
        # Low if sunny weather is coming (solar will help)
        pre_heat_urgency = min(1.0, max(0.0,
            (wind_anticipation - 1.0) * 3.0 +
            (rain_anticipation - 1.0) * 5.0 -
            (1.0 - solar_reduction) * 2.0
        ))

        return {
            "future_solar_energy_kwh": total_solar_energy,
            "solar_peak_indices": solar_peak_indices,
            "pre_heat_urgency": pre_heat_urgency,
            "solar_reduction_factor": solar_reduction,
            "wind_anticipation_factor": wind_anticipation,
            "rain_anticipation_factor": rain_anticipation,
            "avg_future_wind_ms": avg_future_wind,
            "avg_future_precip_mmh": avg_future_precip,
            "future_solar_6_12h_kwh": future_solar_6_12h,
        }

    # ------------------------------------------------------------------
    # Main optimization
    # ------------------------------------------------------------------

    def optimize(
        self,
        initial_state: ThermalState,
        prices: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray | None = None,
        precipitation: np.ndarray | None = None,
        solar_radiation: np.ndarray | None = None,
        start_time: datetime | None = None,
    ) -> OptimizationResult:
        """Run the MPC optimization with predictive weather anticipation.

        This is the CORE of true MPC: the optimizer uses the FULL 24-hour
        forecast trajectory (solar, wind, rain, temperature) to make decisions
        about CURRENT actions. It doesn't just react to current conditions.

        Key anticipatory behaviors:
        - Reduces pre-heating before forecasted sunny periods
        - Increases pre-heating before forecasted windy/rainy periods
        - Coordinates DHW heating with space heating and electricity prices
        """
        import time

        t_start = time.monotonic()

        n_steps = min(len(prices), len(outdoor_temps), self.config.n_steps)
        dt = self.config.dt_hours

        if wind_speeds is None:
            wind_speeds = np.zeros(n_steps)
        if precipitation is None:
            precipitation = np.zeros(n_steps)
        if solar_radiation is None:
            solar_radiation = np.zeros(n_steps)

        if start_time is None:
            start_time = datetime.now()

        # Truncate arrays to n_steps
        prices = prices[:n_steps]
        outdoor_temps = outdoor_temps[:n_steps]
        wind_speeds = wind_speeds[:n_steps]
        precipitation = precipitation[:n_steps]
        solar_radiation = solar_radiation[:n_steps]

        # --- Analyze forecast trajectory for predictive signals ---
        forecast_analysis = self._analyze_forecast_trajectory(
            solar_radiation, wind_speeds, precipitation, outdoor_temps, dt
        )

        _LOGGER.debug(
            "Predictive analysis: solar_reduction=%.2f, wind_factor=%.2f, "
            "rain_factor=%.2f, pre_heat_urgency=%.2f, future_solar=%.1f kWh",
            forecast_analysis["solar_reduction_factor"],
            forecast_analysis["wind_anticipation_factor"],
            forecast_analysis["rain_anticipation_factor"],
            forecast_analysis["pre_heat_urgency"],
            forecast_analysis["future_solar_energy_kwh"],
        )

        p_min = self.model.params.min_electrical_power
        p_max = self.model.params.max_electrical_power
        two_zone = self.model.params.two_zone_enabled
        dhw_enabled = self.model.params.dhw_enabled

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

        # Hours for each time step (for DHW draw pattern)
        step_hours = np.array([
            ((start_time + timedelta(hours=i * dt)).hour
             + (start_time + timedelta(hours=i * dt)).minute / 60.0)
            for i in range(n_steps)
        ])

        # --- Precompute per-step solar gains for the cost function ---
        solar_gains_per_step = np.array([
            self.model.compute_solar_gain(sr) for sr in solar_radiation
        ])

        # --- Precompute per-step effective heat loss (using FORECAST data) ---
        # This is critical: use forecasted wind and rain at EACH future step
        forecast_heat_loss_factors = np.array([
            self.model.effective_heat_loss_coefficient(
                self.model.params.heat_loss_coefficient,
                wind_speeds[i],
                precipitation[i],
            ) / max(self.model.params.heat_loss_coefficient, 0.001)
            for i in range(n_steps)
        ])

        if dhw_enabled:
            result = self._optimize_with_dhw(
                initial_state, prices, outdoor_temps, wind_speeds,
                precipitation, solar_radiation, start_time, n_steps, dt,
                comfort_targets, temp_min_bounds, temp_max_bounds,
                step_hours, solar_gains_per_step, forecast_heat_loss_factors,
                forecast_analysis, t_start,
            )
        else:
            result = self._optimize_space_only(
                initial_state, prices, outdoor_temps, wind_speeds,
                precipitation, solar_radiation, start_time, n_steps, dt,
                comfort_targets, temp_min_bounds, temp_max_bounds,
                solar_gains_per_step, forecast_heat_loss_factors,
                forecast_analysis, t_start,
            )

        result.predictive_info = forecast_analysis
        return result

    def _optimize_space_only(
        self,
        initial_state: ThermalState,
        prices: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray,
        precipitation: np.ndarray,
        solar_radiation: np.ndarray,
        start_time: datetime,
        n_steps: int,
        dt: float,
        comfort_targets: np.ndarray,
        temp_min_bounds: np.ndarray,
        temp_max_bounds: np.ndarray,
        solar_gains_per_step: np.ndarray,
        forecast_heat_loss_factors: np.ndarray,
        forecast_analysis: dict,
        t_start: float,
    ) -> OptimizationResult:
        """Optimize space heating only (no DHW)."""
        import time

        p_min = self.model.params.min_electrical_power
        p_max = self.model.params.max_electrical_power
        two_zone = self.model.params.two_zone_enabled

        # --- Predictive cost function weights ---
        # Adjust per-step weights based on forecast analysis
        solar_reduction = forecast_analysis["solar_reduction_factor"]
        wind_factor = forecast_analysis["wind_anticipation_factor"]
        rain_factor = forecast_analysis["rain_anticipation_factor"]

        # Per-step weight modifiers based on forecasted conditions
        # Steps BEFORE high solar get reduced heating weight (solar will help)
        # Steps BEFORE high wind/rain get increased heating weight (need to pre-heat)
        anticipatory_weights = np.ones(n_steps)
        for i in range(n_steps):
            # Look ahead: is there significant solar in the next 4-8 hours?
            lookahead_start = i
            lookahead_end = min(i + int(8 / dt), n_steps)
            if lookahead_end > lookahead_start:
                future_solar = np.mean(solar_gains_per_step[lookahead_start:lookahead_end])
                # If significant solar coming, reduce current heating motivation
                if future_solar > 0.5:  # > 0.5 kW solar gain is significant
                    anticipatory_weights[i] *= max(0.6, 1.0 - future_solar * 0.3)

                # If bad weather coming, increase pre-heating motivation
                future_loss_factor = np.mean(forecast_heat_loss_factors[lookahead_start:lookahead_end])
                if future_loss_factor > 1.1:
                    anticipatory_weights[i] *= min(1.5, future_loss_factor)

        def objective(power_schedule: np.ndarray) -> float:
            """Compute the total cost with predictive weather anticipation."""
            room_temps, slab_temps, upper_temps, lower_temps = (
                self.model.simulate_trajectory(
                    initial_state=initial_state,
                    power_schedule=power_schedule,
                    outdoor_temps=outdoor_temps,
                    wind_speeds=wind_speeds,
                    precipitation=precipitation,
                    solar_radiation=solar_radiation,
                    dt_hours=dt,
                )
            )

            # Electricity cost
            energy_cost = (
                np.sum(prices * power_schedule * dt) * self.config.price_weight
            )

            if two_zone:
                upper_t = upper_temps[1:]
                lower_t = lower_temps[1:]

                undershoot_u = np.maximum(0, temp_min_bounds - upper_t)
                overshoot_u = np.maximum(0, upper_t - temp_max_bounds)
                undershoot_l = np.maximum(0, temp_min_bounds - lower_t)
                overshoot_l = np.maximum(0, lower_t - temp_max_bounds)

                # Weight comfort penalties by anticipatory weights
                # During periods before sunny weather, allow slightly lower temps
                penalty = self.config.comfort_weight * (
                    np.sum(undershoot_u ** 2 * anticipatory_weights) * 10.0
                    + np.sum(overshoot_u ** 2) * 5.0
                    + np.sum(undershoot_l ** 2 * anticipatory_weights) * 10.0
                    + np.sum(overshoot_l ** 2) * 5.0
                )

                comfort_dev_u = upper_t - comfort_targets
                comfort_dev_l = lower_t - comfort_targets
                comfort_cost = 0.05 * self.config.comfort_weight * (
                    np.sum(comfort_dev_u ** 2) + np.sum(comfort_dev_l ** 2)
                )
            else:
                room_t = room_temps[1:]
                undershoot = np.maximum(0, temp_min_bounds - room_t)
                overshoot = np.maximum(0, room_t - temp_max_bounds)

                penalty = self.config.comfort_weight * (
                    np.sum(undershoot ** 2 * anticipatory_weights) * 10.0
                    + np.sum(overshoot ** 2) * 5.0
                )

                comfort_deviation = room_t - comfort_targets
                comfort_cost = 0.05 * self.config.comfort_weight * np.sum(
                    comfort_deviation ** 2
                )

            # Smoothness penalty
            if len(power_schedule) > 1:
                smoothness = 0.01 * np.sum(np.diff(power_schedule) ** 2)
            else:
                smoothness = 0.0

            # --- Predictive solar anticipation penalty ---
            # Penalize heating during periods just BEFORE high solar
            # This encourages the optimizer to "wait for the sun"
            solar_anticipation_cost = 0.0
            for i in range(min(n_steps - 1, int(12 / dt))):
                # Check if significant solar is coming in the next 4-8 hours
                future_start = i + int(2 / dt)  # 2 hours from now
                future_end = min(i + int(8 / dt), n_steps)
                if future_end > future_start:
                    future_solar_avg = np.mean(
                        solar_gains_per_step[future_start:future_end]
                    )
                    if future_solar_avg > 0.3:  # significant solar coming
                        # Penalize high power usage NOW if sun is coming soon
                        solar_anticipation_cost += (
                            0.02 * power_schedule[i] * future_solar_avg * dt
                        )

            # --- Predictive wind/rain pre-heating incentive ---
            # Incentivize pre-heating BEFORE bad weather arrives
            pre_heat_incentive = 0.0
            for i in range(min(n_steps - 1, int(12 / dt))):
                future_start = i + int(2 / dt)
                future_end = min(i + int(8 / dt), n_steps)
                if future_end > future_start:
                    future_loss = np.mean(
                        forecast_heat_loss_factors[future_start:future_end]
                    )
                    if future_loss > 1.15 and prices[i] < np.median(prices):
                        # Cheap electricity now AND bad weather coming
                        # Incentivize higher power to pre-charge thermal mass
                        pre_heat_incentive -= (
                            0.01 * power_schedule[i] * (future_loss - 1.0) * dt
                        )

            return (
                energy_cost + penalty + comfort_cost + smoothness
                + solar_anticipation_cost + pre_heat_incentive
            )

        # Initial guess: smart initialization considering forecasts
        price_normalized = prices / (np.mean(prices) + 1e-6)
        initial_power = p_max * np.clip(1.5 - price_normalized, 0.2, 1.0)

        # Apply predictive adjustments to initial guess
        for i in range(n_steps):
            # Reduce power before solar periods
            initial_power[i] *= anticipatory_weights[i]

        initial_power = np.clip(initial_power, p_min, p_max)
        bounds = [(p_min, p_max)] * n_steps

        try:
            result = minimize(
                objective,
                initial_power,
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 200, "ftol": 1e-6, "disp": False},
            )
            optimal_power = result.x
            status = "optimal" if result.success else f"suboptimal ({result.message})"
        except Exception as e:
            _LOGGER.error("Optimization failed: %s", e)
            optimal_power = initial_power
            status = f"failed ({e})"

        # Simulate with optimal schedule
        room_temps, slab_temps, upper_temps, lower_temps = (
            self.model.simulate_trajectory(
                initial_state=initial_state,
                power_schedule=optimal_power,
                outdoor_temps=outdoor_temps,
                wind_speeds=wind_speeds,
                precipitation=precipitation,
                solar_radiation=solar_radiation,
                dt_hours=dt,
            )
        )

        solar_gains = [self.model.compute_solar_gain(sr) for sr in solar_radiation]

        # Compute baseline cost
        baseline_power = self._compute_baseline_power(
            initial_state, outdoor_temps, wind_speeds, precipitation,
            solar_radiation, dt,
        )
        baseline_cost = float(np.sum(prices * baseline_power * dt))
        predicted_cost = float(np.sum(prices * optimal_power * dt))
        savings = baseline_cost - predicted_cost

        timestamps = [
            start_time + timedelta(hours=i * dt) for i in range(n_steps)
        ]

        optimal_setpoints = self._power_to_setpoints(
            optimal_power, room_temps[:-1], outdoor_temps
        )

        upper_setpoints = []
        lower_setpoints = []
        if two_zone:
            for i, power in enumerate(optimal_power):
                p_norm = (power - p_min) / max(p_max - p_min, 0.1)
                p_norm = np.clip(p_norm, 0, 1)
                upper_sp = self.config.min_temp + p_norm * (
                    self.config.max_temp - self.config.min_temp
                )
                lower_sp = self.config.min_temp + p_norm * (
                    self.config.max_temp - self.config.min_temp + 1.0
                )
                upper_setpoints.append(round(float(upper_sp), 1))
                lower_setpoints.append(round(float(lower_sp), 1))

        t_elapsed = (time.monotonic() - t_start) * 1000

        _LOGGER.info(
            "Optimization completed in %.0fms: cost=%.2f, baseline=%.2f, savings=%.1f%%, "
            "solar_reduction=%.2f, wind_factor=%.2f",
            t_elapsed, predicted_cost, baseline_cost,
            (savings / baseline_cost * 100) if baseline_cost > 0 else 0,
            forecast_analysis["solar_reduction_factor"],
            forecast_analysis["wind_anticipation_factor"],
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
            savings_percentage=(
                (savings / baseline_cost * 100) if baseline_cost > 0 else 0
            ),
            optimal_setpoints=optimal_setpoints,
            status=status,
            solve_time_ms=t_elapsed,
            upper_temp_trajectory=upper_temps.tolist(),
            lower_temp_trajectory=lower_temps.tolist(),
            solar_gain_trajectory=solar_gains,
            upper_setpoints=upper_setpoints,
            lower_setpoints=lower_setpoints,
        )

    def _optimize_with_dhw(
        self,
        initial_state: ThermalState,
        prices: np.ndarray,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray,
        precipitation: np.ndarray,
        solar_radiation: np.ndarray,
        start_time: datetime,
        n_steps: int,
        dt: float,
        comfort_targets: np.ndarray,
        temp_min_bounds: np.ndarray,
        temp_max_bounds: np.ndarray,
        step_hours: np.ndarray,
        solar_gains_per_step: np.ndarray,
        forecast_heat_loss_factors: np.ndarray,
        forecast_analysis: dict,
        t_start: float,
    ) -> OptimizationResult:
        """Optimize coordinated space heating + DHW heating.

        Decision variables: [P_space[0..n-1], P_dhw[0..n-1]]
        The heat pump can allocate power to space OR DHW, with total
        constrained by max capacity.
        """
        import time

        p_min = self.model.params.min_electrical_power
        p_max = self.model.params.max_electrical_power
        two_zone = self.model.params.two_zone_enabled
        dhw_min_temp = self.model.params.dhw_min_temp
        dhw_setpoint = self.model.params.dhw_setpoint

        start_hour = (
            start_time.hour + start_time.minute / 60.0
        )

        # Anticipatory weights (same as space-only)
        anticipatory_weights = np.ones(n_steps)
        for i in range(n_steps):
            lookahead_end = min(i + int(8 / dt), n_steps)
            if lookahead_end > i:
                future_solar = np.mean(solar_gains_per_step[i:lookahead_end])
                if future_solar > 0.5:
                    anticipatory_weights[i] *= max(0.6, 1.0 - future_solar * 0.3)
                future_loss = np.mean(forecast_heat_loss_factors[i:lookahead_end])
                if future_loss > 1.1:
                    anticipatory_weights[i] *= min(1.5, future_loss)

        def objective(x: np.ndarray) -> float:
            """Joint space + DHW optimization objective."""
            space_power = x[:n_steps]
            dhw_power = x[n_steps:]

            # Simulate space heating trajectory
            room_temps, slab_temps, upper_temps, lower_temps, dhw_temps = (
                self.model.simulate_trajectory_with_dhw(
                    initial_state=initial_state,
                    space_power_schedule=space_power,
                    dhw_power_schedule=dhw_power,
                    outdoor_temps=outdoor_temps,
                    wind_speeds=wind_speeds,
                    precipitation=precipitation,
                    solar_radiation=solar_radiation,
                    start_hour=start_hour,
                    dt_hours=dt,
                )
            )

            # --- Electricity cost (total: space + DHW) ---
            total_power = space_power + dhw_power
            energy_cost = (
                np.sum(prices * total_power * dt) * self.config.price_weight
            )

            # --- Space heating comfort penalty ---
            if two_zone:
                upper_t = upper_temps[1:]
                lower_t = lower_temps[1:]
                undershoot_u = np.maximum(0, temp_min_bounds - upper_t)
                overshoot_u = np.maximum(0, upper_t - temp_max_bounds)
                undershoot_l = np.maximum(0, temp_min_bounds - lower_t)
                overshoot_l = np.maximum(0, lower_t - temp_max_bounds)
                space_penalty = self.config.comfort_weight * (
                    np.sum(undershoot_u ** 2 * anticipatory_weights) * 10.0
                    + np.sum(overshoot_u ** 2) * 5.0
                    + np.sum(undershoot_l ** 2 * anticipatory_weights) * 10.0
                    + np.sum(overshoot_l ** 2) * 5.0
                )
                comfort_dev_u = upper_t - comfort_targets
                comfort_dev_l = lower_t - comfort_targets
                comfort_cost = 0.05 * self.config.comfort_weight * (
                    np.sum(comfort_dev_u ** 2) + np.sum(comfort_dev_l ** 2)
                )
            else:
                room_t = room_temps[1:]
                undershoot = np.maximum(0, temp_min_bounds - room_t)
                overshoot = np.maximum(0, room_t - temp_max_bounds)
                space_penalty = self.config.comfort_weight * (
                    np.sum(undershoot ** 2 * anticipatory_weights) * 10.0
                    + np.sum(overshoot ** 2) * 5.0
                )
                comfort_deviation = room_t - comfort_targets
                comfort_cost = 0.05 * self.config.comfort_weight * np.sum(
                    comfort_deviation ** 2
                )

            # --- DHW temperature penalty ---
            # Hard-ish constraint: DHW must stay above minimum
            dhw_t = dhw_temps[1:]
            dhw_undershoot = np.maximum(0, dhw_min_temp - dhw_t)
            # Very high penalty for DHW below minimum (safety/comfort)
            dhw_penalty = 15.0 * self.config.comfort_weight * np.sum(
                dhw_undershoot ** 2
            )

            # Soft penalty for DHW below setpoint (preference, not hard constraint)
            dhw_below_setpoint = np.maximum(0, dhw_setpoint - dhw_t)
            dhw_comfort = 0.5 * np.sum(dhw_below_setpoint ** 2)

            # Prefer DHW heating during cheap periods
            # (already handled by energy cost, but add slight preference)
            dhw_cheap_bonus = 0.0

            # --- Capacity constraint penalty ---
            # Total power must not exceed max capacity
            over_capacity = np.maximum(0, total_power - p_max)
            capacity_penalty = 50.0 * np.sum(over_capacity ** 2)

            # Smoothness
            smoothness = 0.0
            if n_steps > 1:
                smoothness += 0.01 * np.sum(np.diff(space_power) ** 2)
                smoothness += 0.01 * np.sum(np.diff(dhw_power) ** 2)

            # Solar anticipation cost (same as space-only)
            solar_anticipation_cost = 0.0
            for i in range(min(n_steps - 1, int(12 / dt))):
                future_start = i + int(2 / dt)
                future_end = min(i + int(8 / dt), n_steps)
                if future_end > future_start:
                    future_solar_avg = np.mean(
                        solar_gains_per_step[future_start:future_end]
                    )
                    if future_solar_avg > 0.3:
                        solar_anticipation_cost += (
                            0.02 * space_power[i] * future_solar_avg * dt
                        )

            return (
                energy_cost + space_penalty + comfort_cost
                + dhw_penalty + dhw_comfort + dhw_cheap_bonus
                + capacity_penalty + smoothness + solar_anticipation_cost
            )

        # Initial guess
        price_normalized = prices / (np.mean(prices) + 1e-6)

        # Space heating: inversely proportional to price
        init_space = p_max * 0.6 * np.clip(1.5 - price_normalized, 0.2, 1.0)
        for i in range(n_steps):
            init_space[i] *= anticipatory_weights[i]
        init_space = np.clip(init_space, p_min * 0.5, p_max * 0.8)

        # DHW: heat during cheap periods, less during expensive
        init_dhw = np.zeros(n_steps)
        # If DHW is below setpoint, prioritize heating during cheap periods
        if initial_state.dhw_temperature < dhw_setpoint:
            # Find cheapest 25% of periods and allocate DHW power there
            price_threshold = np.percentile(prices, 25)
            cheap_mask = prices <= price_threshold
            init_dhw[cheap_mask] = p_max * 0.3
        init_dhw = np.clip(init_dhw, 0.0, p_max * 0.5)

        x0 = np.concatenate([init_space, init_dhw])

        # Bounds: space [0, p_max], dhw [0, p_max]
        bounds = [(0.0, p_max)] * n_steps + [(0.0, p_max)] * n_steps

        try:
            result = minimize(
                objective,
                x0,
                method="L-BFGS-B",
                bounds=bounds,
                options={"maxiter": 300, "ftol": 1e-6, "disp": False},
            )
            optimal_x = result.x
            status = "optimal" if result.success else f"suboptimal ({result.message})"
        except Exception as e:
            _LOGGER.error("DHW optimization failed: %s", e)
            optimal_x = x0
            status = f"failed ({e})"

        optimal_space = optimal_x[:n_steps]
        optimal_dhw = optimal_x[n_steps:]

        # Simulate with optimal schedule
        room_temps, slab_temps, upper_temps, lower_temps, dhw_temps = (
            self.model.simulate_trajectory_with_dhw(
                initial_state=initial_state,
                space_power_schedule=optimal_space,
                dhw_power_schedule=optimal_dhw,
                outdoor_temps=outdoor_temps,
                wind_speeds=wind_speeds,
                precipitation=precipitation,
                solar_radiation=solar_radiation,
                start_hour=start_hour,
                dt_hours=dt,
            )
        )

        solar_gains = [self.model.compute_solar_gain(sr) for sr in solar_radiation]

        # Baseline cost
        baseline_power = self._compute_baseline_power(
            initial_state, outdoor_temps, wind_speeds, precipitation,
            solar_radiation, dt,
        )
        # Add baseline DHW power (constant)
        baseline_dhw = np.full(n_steps, self.model.params.dhw_draw_power / max(
            self.model.compute_cop_dhw(np.mean(outdoor_temps), dhw_setpoint), 1.0
        ))
        baseline_cost = float(np.sum(prices * (baseline_power + baseline_dhw) * dt))
        total_optimal_power = optimal_space + optimal_dhw
        predicted_cost = float(np.sum(prices * total_optimal_power * dt))
        dhw_cost = float(np.sum(prices * optimal_dhw * dt))
        savings = baseline_cost - predicted_cost

        timestamps = [
            start_time + timedelta(hours=i * dt) for i in range(n_steps)
        ]

        optimal_setpoints = self._power_to_setpoints(
            optimal_space, room_temps[:-1], outdoor_temps
        )

        upper_setpoints = []
        lower_setpoints = []
        if two_zone:
            for i, power in enumerate(optimal_space):
                p_norm = (power - p_min) / max(p_max - p_min, 0.1)
                p_norm = np.clip(p_norm, 0, 1)
                upper_sp = self.config.min_temp + p_norm * (
                    self.config.max_temp - self.config.min_temp
                )
                lower_sp = self.config.min_temp + p_norm * (
                    self.config.max_temp - self.config.min_temp + 1.0
                )
                upper_setpoints.append(round(float(upper_sp), 1))
                lower_setpoints.append(round(float(lower_sp), 1))

        t_elapsed = (time.monotonic() - t_start) * 1000

        _LOGGER.info(
            "DHW+Space optimization completed in %.0fms: cost=%.2f (DHW=%.2f), "
            "baseline=%.2f, savings=%.1f%%",
            t_elapsed, predicted_cost, dhw_cost, baseline_cost,
            (savings / baseline_cost * 100) if baseline_cost > 0 else 0,
        )

        return OptimizationResult(
            power_schedule=optimal_space.tolist(),
            room_temp_trajectory=room_temps.tolist(),
            slab_temp_trajectory=slab_temps.tolist(),
            timestamps=timestamps,
            prices=prices.tolist(),
            predicted_cost=predicted_cost,
            baseline_cost=baseline_cost,
            predicted_savings=savings,
            savings_percentage=(
                (savings / baseline_cost * 100) if baseline_cost > 0 else 0
            ),
            optimal_setpoints=optimal_setpoints,
            status=status,
            solve_time_ms=t_elapsed,
            upper_temp_trajectory=upper_temps.tolist(),
            lower_temp_trajectory=lower_temps.tolist(),
            solar_gain_trajectory=solar_gains,
            upper_setpoints=upper_setpoints,
            lower_setpoints=lower_setpoints,
            dhw_power_schedule=optimal_dhw.tolist(),
            dhw_temp_trajectory=dhw_temps.tolist(),
            dhw_heating_cost=dhw_cost,
        )

    def _compute_baseline_power(
        self,
        initial_state: ThermalState,
        outdoor_temps: np.ndarray,
        wind_speeds: np.ndarray,
        precipitation: np.ndarray,
        solar_radiation: np.ndarray,
        dt: float,
    ) -> np.ndarray:
        """Compute baseline power schedule (constant temperature strategy)."""
        n_steps = len(outdoor_temps)
        target = self.config.target_temp
        p = self.model.params

        baseline_power = np.zeros(n_steps)
        state = initial_state

        for i in range(n_steps):
            u_eff = self.model.effective_heat_loss_coefficient(
                p.heat_loss_coefficient, wind_speeds[i], precipitation[i]
            )
            heat_loss = u_eff * (state.room_temperature - outdoor_temps[i])
            cop = self.model.compute_cop(outdoor_temps[i])
            q_solar = self.model.compute_solar_gain(solar_radiation[i])

            thermal_need = max(0, heat_loss - p.internal_gains - q_solar)
            temp_error = target - state.room_temperature
            correction = p.slab_heat_transfer * temp_error * 0.5

            electrical_power = max(0, (thermal_need + correction) / cop)
            electrical_power = np.clip(
                electrical_power, p.min_electrical_power, p.max_electrical_power
            )
            baseline_power[i] = electrical_power

            state = self.model.simulate_step(
                state, electrical_power, outdoor_temps[i],
                wind_speeds[i], precipitation[i], solar_radiation[i], dt,
            )

        return baseline_power

    def _power_to_setpoints(
        self,
        power_schedule: np.ndarray,
        room_temps: np.ndarray,
        outdoor_temps: np.ndarray,
    ) -> list[float]:
        """Convert power schedule to equivalent temperature setpoints."""
        setpoints = []
        p_range = (
            self.model.params.max_electrical_power
            - self.model.params.min_electrical_power
        )

        for i, (power, room_t) in enumerate(zip(power_schedule, room_temps)):
            p_norm = (
                power - self.model.params.min_electrical_power
            ) / max(p_range, 0.1)
            p_norm = np.clip(p_norm, 0, 1)
            displacement = p_norm * (self.config.max_temp - self.config.min_temp)
            setpoint = self.config.min_temp + displacement
            setpoints.append(round(float(setpoint), 1))

        return setpoints

    def get_current_action(
        self, result: OptimizationResult, current_time: datetime
    ) -> dict[str, Any]:
        """Get the current recommended action from the optimization result."""
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
                i = len(result.timestamps) - 1
                break

        power = result.power_schedule[i]
        setpoint = result.optimal_setpoints[i]
        price = result.prices[i]

        p_range = (
            self.model.params.max_electrical_power
            - self.model.params.min_electrical_power
        )
        p_norm = (
            power - self.model.params.min_electrical_power
        ) / max(p_range, 0.1)

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

        action = {
            "power": round(power, 2),
            "setpoint": setpoint,
            "mode": mode,
            "price": round(price, 4),
            "power_normalized": round(p_norm, 2),
        }

        # Add zone-specific setpoints if available
        if result.upper_setpoints and i < len(result.upper_setpoints):
            action["upper_setpoint"] = result.upper_setpoints[i]
        if result.lower_setpoints and i < len(result.lower_setpoints):
            action["lower_setpoint"] = result.lower_setpoints[i]

        # Add solar gain info
        if result.solar_gain_trajectory and i < len(result.solar_gain_trajectory):
            action["solar_gain_kw"] = round(result.solar_gain_trajectory[i], 3)

        # Add DHW info
        if result.dhw_power_schedule and i < len(result.dhw_power_schedule):
            dhw_power = result.dhw_power_schedule[i]
            action["dhw_power"] = round(dhw_power, 2)
            action["dhw_heating_active"] = dhw_power > 0.1
        if result.dhw_temp_trajectory and i < len(result.dhw_temp_trajectory):
            action["dhw_temperature"] = round(result.dhw_temp_trajectory[i], 1)

        # Add predictive info
        if result.predictive_info:
            action["solar_reduction_factor"] = round(
                result.predictive_info.get("solar_reduction_factor", 1.0), 2
            )
            action["wind_anticipation_factor"] = round(
                result.predictive_info.get("wind_anticipation_factor", 1.0), 2
            )
            action["pre_heat_urgency"] = round(
                result.predictive_info.get("pre_heat_urgency", 0.0), 2
            )

        return action
