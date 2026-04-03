"""Data coordinator for Heat Pump Cost Optimizer.

The coordinator manages:
1. Fetching electricity prices from Tibber API
2. Fetching weather forecasts from Home Assistant weather entities
3. Running the MPC optimization on a regular schedule
4. Applying optimization results to heat pump control
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import numpy as np

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_TIBBER_TOKEN,
    CONF_WEATHER_ENTITY,
    CONF_INDOOR_TEMP_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_HEAT_PUMP_ENTITY,
    CONF_HEAT_PUMP_SWITCH_ENTITY,
    CONF_TARGET_TEMP,
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    CONF_COMFORT_TEMP_DAY,
    CONF_COMFORT_TEMP_NIGHT,
    CONF_DAY_START_HOUR,
    CONF_DAY_END_HOUR,
    CONF_HOUSE_THERMAL_MASS,
    CONF_HOUSE_HEAT_LOSS_COEFFICIENT,
    CONF_SLAB_THERMAL_MASS,
    CONF_SLAB_HEAT_TRANSFER,
    CONF_HEAT_PUMP_COP_NOMINAL,
    CONF_HEAT_PUMP_MAX_POWER,
    CONF_HEAT_PUMP_MIN_POWER,
    CONF_OPTIMIZATION_INTERVAL,
    CONF_PRICE_WEIGHT,
    CONF_COMFORT_WEIGHT,
    DEFAULT_TARGET_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
    DEFAULT_COMFORT_TEMP_DAY,
    DEFAULT_COMFORT_TEMP_NIGHT,
    DEFAULT_DAY_START_HOUR,
    DEFAULT_DAY_END_HOUR,
    DEFAULT_OPTIMIZATION_INTERVAL,
    DEFAULT_PRICE_WEIGHT,
    DEFAULT_COMFORT_WEIGHT,
    MODE_AUTO,
    MODE_COMFORT,
    MODE_ECONOMY,
    MODE_OFF,
    MODE_BOOST,
    UPDATE_INTERVAL_OPTIMIZATION,
)
from .thermal_model import ThermalModel, ThermalParameters, ThermalState
from .optimizer import HeatPumpOptimizer, OptimizationConfig, OptimizationResult

_LOGGER = logging.getLogger(__name__)

TIBBER_API_URL = "https://api.tibber.com/v1-beta/gql"

# Tibber GraphQL query for price data
TIBBER_PRICE_QUERY = """
{
  viewer {
    homes {
      currentSubscription {
        priceInfo {
          current {
            total
            startsAt
            level
          }
          today {
            total
            startsAt
            level
          }
          tomorrow {
            total
            startsAt
            level
          }
        }
      }
    }
  }
}
"""


class HeatPumpOptimizerCoordinator(DataUpdateCoordinator):
    """Coordinator for Heat Pump Cost Optimizer."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry
        self._config = {**entry.data, **entry.options}

        # Get optimization interval
        interval_min = self._config.get(
            CONF_OPTIMIZATION_INTERVAL, DEFAULT_OPTIMIZATION_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval_min),
        )

        # Initialize thermal model
        self._thermal_params = ThermalParameters.from_config(self._config)
        self._thermal_model = ThermalModel(self._thermal_params)

        # Initialize optimizer config
        self._opt_config = OptimizationConfig(
            target_temp=self._config.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP),
            min_temp=self._config.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP),
            max_temp=self._config.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP),
            comfort_temp_day=self._config.get(CONF_COMFORT_TEMP_DAY, DEFAULT_COMFORT_TEMP_DAY),
            comfort_temp_night=self._config.get(CONF_COMFORT_TEMP_NIGHT, DEFAULT_COMFORT_TEMP_NIGHT),
            day_start_hour=int(self._config.get(CONF_DAY_START_HOUR, DEFAULT_DAY_START_HOUR)),
            day_end_hour=int(self._config.get(CONF_DAY_END_HOUR, DEFAULT_DAY_END_HOUR)),
            price_weight=self._config.get(CONF_PRICE_WEIGHT, DEFAULT_PRICE_WEIGHT),
            comfort_weight=self._config.get(CONF_COMFORT_WEIGHT, DEFAULT_COMFORT_WEIGHT),
        )

        # Initialize optimizer
        self._optimizer = HeatPumpOptimizer(self._thermal_model, self._opt_config)

        # State
        self._mode: str = MODE_AUTO
        self._optimization_result: OptimizationResult | None = None
        self._last_optimization: datetime | None = None
        self._next_optimization: datetime | None = None
        self._prices: list[dict] = []
        self._weather_forecast: list[dict] = []
        self._current_state = ThermalState()
        self._current_action: dict[str, Any] = {}
        self._unsub_timer: Any = None

    @property
    def mode(self) -> str:
        """Return current operation mode."""
        return self._mode

    @property
    def optimization_result(self) -> OptimizationResult | None:
        """Return the latest optimization result."""
        return self._optimization_result

    @property
    def last_optimization(self) -> datetime | None:
        """Return the time of the last optimization."""
        return self._last_optimization

    @property
    def next_optimization(self) -> datetime | None:
        """Return the time of the next scheduled optimization."""
        return self._next_optimization

    @property
    def current_action(self) -> dict[str, Any]:
        """Return the current recommended action."""
        return self._current_action

    @property
    def current_state(self) -> ThermalState:
        """Return the current thermal state."""
        return self._current_state

    @property
    def prices(self) -> list[dict]:
        """Return current price data."""
        return self._prices

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data and run optimization."""
        try:
            # Update current state from sensors
            await self._update_current_state()

            # Fetch prices from Tibber
            await self._fetch_tibber_prices()

            # Fetch weather forecast
            await self._fetch_weather_forecast()

            # Run optimization if in auto mode
            if self._mode in (MODE_AUTO, MODE_ECONOMY):
                await self.async_run_optimization()
            elif self._mode == MODE_COMFORT:
                self._current_action = {
                    "power": self._thermal_model.params.max_electrical_power * 0.7,
                    "setpoint": self._opt_config.target_temp,
                    "mode": "comfort",
                    "price": self._get_current_price(),
                    "power_normalized": 0.7,
                }
            elif self._mode == MODE_BOOST:
                self._current_action = {
                    "power": self._thermal_model.params.max_electrical_power,
                    "setpoint": self._opt_config.max_temp,
                    "mode": "boost",
                    "price": self._get_current_price(),
                    "power_normalized": 1.0,
                }
            elif self._mode == MODE_OFF:
                self._current_action = {
                    "power": 0.0,
                    "setpoint": self._opt_config.min_temp,
                    "mode": "off",
                    "price": self._get_current_price(),
                    "power_normalized": 0.0,
                }

            # Apply current action to heat pump
            await self._apply_action()

            self._next_optimization = dt_util.now() + timedelta(
                minutes=self._config.get(CONF_OPTIMIZATION_INTERVAL, DEFAULT_OPTIMIZATION_INTERVAL)
            )

            return self._build_data_dict()

        except Exception as err:
            _LOGGER.error("Error updating Heat Pump Optimizer: %s", err, exc_info=True)
            raise UpdateFailed(f"Error updating data: {err}") from err

    async def async_run_optimization(self) -> None:
        """Run the MPC optimization."""
        _LOGGER.info("Running heat pump optimization")

        try:
            # Prepare price and weather arrays
            prices, outdoor_temps, wind_speeds, precipitation = (
                self._prepare_forecast_data()
            )

            if len(prices) < 4:
                _LOGGER.warning(
                    "Not enough price data for optimization (got %d steps)", len(prices)
                )
                return

            # Run optimization in executor to avoid blocking
            result = await self.hass.async_add_executor_job(
                self._optimizer.optimize,
                self._current_state,
                prices,
                outdoor_temps,
                wind_speeds,
                precipitation,
                dt_util.now(),
            )

            self._optimization_result = result
            self._last_optimization = dt_util.now()

            # Update current action
            self._current_action = self._optimizer.get_current_action(
                result, dt_util.now()
            )

            _LOGGER.info(
                "Optimization complete: savings=%.1f%%, cost=%.2f, status=%s",
                result.savings_percentage,
                result.predicted_cost,
                result.status,
            )

        except Exception as err:
            _LOGGER.error("Optimization failed: %s", err, exc_info=True)

    async def async_set_mode(self, mode: str) -> None:
        """Set the operation mode."""
        self._mode = mode
        _LOGGER.info("Operation mode set to: %s", mode)
        await self.async_request_refresh()

    async def async_update_thermal_params(self, params: dict[str, Any]) -> None:
        """Update thermal model parameters."""
        if "house_thermal_mass" in params:
            self._thermal_params.room_thermal_mass = params["house_thermal_mass"]
        if "house_heat_loss_coefficient" in params:
            self._thermal_params.heat_loss_coefficient = params["house_heat_loss_coefficient"]
        if "slab_thermal_mass" in params:
            self._thermal_params.slab_thermal_mass = params["slab_thermal_mass"]
        if "slab_heat_transfer" in params:
            self._thermal_params.slab_heat_transfer = params["slab_heat_transfer"]
        if "heat_pump_cop_nominal" in params:
            self._thermal_params.cop_nominal = params["heat_pump_cop_nominal"]

        self._thermal_model = ThermalModel(self._thermal_params)
        self._optimizer = HeatPumpOptimizer(self._thermal_model, self._opt_config)

        _LOGGER.info("Thermal parameters updated, re-running optimization")
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    async def _update_current_state(self) -> None:
        """Update current thermal state from HA entities."""
        # Try to get indoor temperature
        indoor_entity = self._config.get(CONF_INDOOR_TEMP_ENTITY)
        if indoor_entity:
            state = self.hass.states.get(indoor_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._current_state.room_temperature = float(state.state)
                except (ValueError, TypeError):
                    pass

        # Try to get outdoor temperature
        outdoor_entity = self._config.get(CONF_OUTDOOR_TEMP_ENTITY)
        if outdoor_entity:
            state = self.hass.states.get(outdoor_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._current_state.outdoor_temperature = float(state.state)
                except (ValueError, TypeError):
                    pass

        # Estimate slab temperature if we don't have a sensor
        # Slab is typically 1-2°C above room temperature when heating
        if not hasattr(self, "_slab_temp_initialized"):
            self._current_state.slab_temperature = (
                self._current_state.room_temperature + 1.0
            )
            self._slab_temp_initialized = True

    async def _fetch_tibber_prices(self) -> None:
        """Fetch electricity prices from Tibber API."""
        token = self._config.get(CONF_TIBBER_TOKEN)
        if not token:
            _LOGGER.error("No Tibber token configured")
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        query_data = '{"query": "' + TIBBER_PRICE_QUERY.replace("\n", " ").replace('"', '\\"') + '"}'

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TIBBER_API_URL,
                    data=query_data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.error("Tibber API error: %s", resp.status)
                        return

                    data = await resp.json()

            if "errors" in data:
                _LOGGER.error("Tibber API errors: %s", data["errors"])
                return

            # Parse price data
            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                _LOGGER.error("No homes found in Tibber data")
                return

            price_info = (
                homes[0]
                .get("currentSubscription", {})
                .get("priceInfo", {})
            )

            prices = []
            for period in ["today", "tomorrow"]:
                period_prices = price_info.get(period, [])
                if period_prices:
                    for p in period_prices:
                        prices.append({
                            "total": p.get("total", 0),
                            "starts_at": p.get("startsAt", ""),
                            "level": p.get("level", "NORMAL"),
                        })

            self._prices = prices
            _LOGGER.debug("Fetched %d price entries from Tibber", len(prices))

        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching Tibber prices: %s", err)
        except Exception as err:
            _LOGGER.error("Unexpected error fetching prices: %s", err, exc_info=True)

    async def _fetch_weather_forecast(self) -> None:
        """Fetch weather forecast from Home Assistant weather entity."""
        weather_entity = self._config.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            _LOGGER.warning("No weather entity configured")
            return

        try:
            # Use the weather.get_forecasts service
            result = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )

            if result and weather_entity in result:
                forecast_data = result[weather_entity].get("forecast", [])
                self._weather_forecast = forecast_data
                _LOGGER.debug(
                    "Fetched %d weather forecast entries", len(forecast_data)
                )
            else:
                _LOGGER.warning("No forecast data returned for %s", weather_entity)

        except Exception as err:
            _LOGGER.warning(
                "Error fetching weather forecast: %s. Using fallback.", err
            )
            # Fallback: try to use the weather entity state directly
            state = self.hass.states.get(weather_entity)
            if state:
                try:
                    temp = float(state.attributes.get("temperature", 5.0))
                    wind = float(state.attributes.get("wind_speed", 0.0))
                    # Create simple forecast with current conditions
                    self._weather_forecast = [
                        {
                            "datetime": (
                                dt_util.now() + timedelta(hours=i)
                            ).isoformat(),
                            "temperature": temp,
                            "wind_speed": wind,
                            "precipitation": 0.0,
                        }
                        for i in range(48)
                    ]
                except (ValueError, TypeError):
                    pass

    def _prepare_forecast_data(
        self,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Prepare arrays from price and weather data for the optimizer.

        Interpolates hourly prices to 15-minute intervals and aligns
        weather forecast data with the optimization time steps.
        """
        dt_minutes = 15
        n_steps = self._opt_config.n_steps
        now = dt_util.now()

        # Process prices - Tibber gives hourly, we need 15-min intervals
        prices_15min = []
        if self._prices:
            for price_entry in self._prices:
                total = price_entry.get("total", 0)
                # Each hourly price maps to 4 x 15-minute intervals
                for _ in range(4):
                    prices_15min.append(total)
        else:
            # Fallback: use a flat price
            prices_15min = [0.5] * (n_steps + 10)

        # Find the offset: how many 15-min steps from midnight to now
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        minutes_since_midnight = (now - midnight).total_seconds() / 60
        step_offset = int(minutes_since_midnight / dt_minutes)

        # Slice prices starting from current time
        if step_offset < len(prices_15min):
            prices = prices_15min[step_offset: step_offset + n_steps]
        else:
            prices = prices_15min[:n_steps]

        # Pad if needed
        while len(prices) < n_steps:
            prices.append(prices[-1] if prices else 0.5)

        # Process weather forecast
        outdoor_temps = []
        wind_speeds = []
        precipitation_rates = []

        if self._weather_forecast:
            # Interpolate hourly forecast to 15-min intervals
            for fc in self._weather_forecast:
                temp = fc.get("temperature", 5.0)
                wind = fc.get("wind_speed", 0.0)
                # Convert wind from km/h to m/s if needed
                if wind > 30:  # likely km/h
                    wind = wind / 3.6
                precip = fc.get("precipitation", 0.0) or 0.0

                # Each hourly forecast covers 4 x 15-min steps
                for _ in range(4):
                    outdoor_temps.append(temp)
                    wind_speeds.append(wind)
                    precipitation_rates.append(precip)
        else:
            # Fallback to current outdoor temp
            base_temp = self._current_state.outdoor_temperature
            for i in range(n_steps):
                outdoor_temps.append(base_temp)
                wind_speeds.append(0.0)
                precipitation_rates.append(0.0)

        # Truncate/pad to n_steps
        for arr in [outdoor_temps, wind_speeds, precipitation_rates]:
            while len(arr) < n_steps:
                arr.append(arr[-1] if arr else 0.0)

        return (
            np.array(prices[:n_steps], dtype=float),
            np.array(outdoor_temps[:n_steps], dtype=float),
            np.array(wind_speeds[:n_steps], dtype=float),
            np.array(precipitation_rates[:n_steps], dtype=float),
        )

    def _get_current_price(self) -> float:
        """Get the current electricity price."""
        if not self._prices:
            return 0.0

        now = dt_util.now()
        for price_entry in self._prices:
            starts_at = price_entry.get("starts_at", "")
            if starts_at:
                try:
                    ts = datetime.fromisoformat(starts_at)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts <= now < ts + timedelta(hours=1):
                        return price_entry.get("total", 0)
                except (ValueError, TypeError):
                    continue

        # Return first/last price as fallback
        return self._prices[0].get("total", 0) if self._prices else 0.0

    async def _apply_action(self) -> None:
        """Apply the current optimization action to the heat pump."""
        if not self._current_action:
            return

        # Apply to climate entity if configured
        climate_entity = self._config.get(CONF_HEAT_PUMP_ENTITY)
        if climate_entity:
            setpoint = self._current_action.get("setpoint", self._opt_config.target_temp)
            try:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {
                        "entity_id": climate_entity,
                        "temperature": setpoint,
                    },
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Error setting heat pump temperature: %s", err)

        # Apply to switch entity if configured
        switch_entity = self._config.get(CONF_HEAT_PUMP_SWITCH_ENTITY)
        if switch_entity:
            mode = self._current_action.get("mode", "normal")
            if mode == "off":
                try:
                    await self.hass.services.async_call(
                        "switch",
                        "turn_off",
                        {"entity_id": switch_entity},
                        blocking=True,
                    )
                except Exception as err:
                    _LOGGER.error("Error turning off heat pump: %s", err)
            else:
                try:
                    await self.hass.services.async_call(
                        "switch",
                        "turn_on",
                        {"entity_id": switch_entity},
                        blocking=True,
                    )
                except Exception as err:
                    _LOGGER.error("Error turning on heat pump: %s", err)

    def _build_data_dict(self) -> dict[str, Any]:
        """Build the data dictionary for the coordinator."""
        result = self._optimization_result

        data = {
            "mode": self._mode,
            "current_action": self._current_action,
            "current_price": self._get_current_price(),
            "indoor_temperature": self._current_state.room_temperature,
            "outdoor_temperature": self._current_state.outdoor_temperature,
            "slab_temperature": self._current_state.slab_temperature,
            "last_optimization": self._last_optimization,
            "next_optimization": self._next_optimization,
            "prices_available": len(self._prices),
            "weather_forecast_available": len(self._weather_forecast),
        }

        if result:
            data.update({
                "predicted_cost": result.predicted_cost,
                "baseline_cost": result.baseline_cost,
                "predicted_savings": result.predicted_savings,
                "savings_percentage": result.savings_percentage,
                "optimization_status": result.status,
                "solve_time_ms": result.solve_time_ms,
                "schedule": [
                    {
                        "time": ts.isoformat(),
                        "power": p,
                        "setpoint": s,
                        "price": pr,
                        "room_temp": rt,
                    }
                    for ts, p, s, pr, rt in zip(
                        result.timestamps[:24],  # first 6 hours for attribute
                        result.power_schedule[:24],
                        result.optimal_setpoints[:24],
                        result.prices[:24],
                        result.room_temp_trajectory[1:25],
                    )
                ],
            })
        else:
            data.update({
                "predicted_cost": None,
                "baseline_cost": None,
                "predicted_savings": None,
                "savings_percentage": None,
                "optimization_status": "not_run",
                "solve_time_ms": 0,
                "schedule": [],
            })

        return data
