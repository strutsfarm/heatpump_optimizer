"""Data coordinator for Heat Pump Cost Optimizer.

The coordinator manages:
1. Fetching electricity prices from Tibber API
2. Fetching weather forecasts from Home Assistant weather entities
3. Fetching solar radiation, floor return temperature, and DHW temperature
4. Running the MPC optimization (with predictive weather anticipation + DHW)
5. Applying optimization results to heat pump control
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import numpy as np

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_TIBBER_TOKEN,
    CONF_WEATHER_ENTITY,
    CONF_INDOOR_TEMP_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_HEAT_PUMP_ENTITY,
    CONF_HEAT_PUMP_SWITCH_ENTITY,
    CONF_SOLAR_RADIATION_ENTITY,
    CONF_FLOOR_RETURN_TEMP_ENTITY,
    CONF_DHW_TEMP_ENTITY,
    CONF_ECL110_COMMAND_TOPIC,
    CONF_ECL110_DISPLACE_SET_TOPIC,
    CONF_ECL110_STATE_TOPIC,
    CONF_ECL110_QOS,
    CONF_ECL110_RETAIN,
    CONF_ECL110_DISPLACE_MIN,
    CONF_ECL110_DISPLACE_MAX,
    CONF_ECL110_PID_TIME_CONSTANT,
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
    CONF_DHW_SETPOINT,
    CONF_DHW_MIN_TEMP,
    CONF_WIND_SENSITIVITY,
    CONF_RAIN_HEAT_LOSS_MULTIPLIER,
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
    DEFAULT_DHW_SETPOINT,
    DEFAULT_DHW_MIN_TEMP,
    DEFAULT_ECL110_COMMAND_TOPIC,
    DEFAULT_ECL110_DISPLACE_SET_TOPIC,
    DEFAULT_ECL110_STATE_TOPIC,
    DEFAULT_ECL110_QOS,
    DEFAULT_ECL110_RETAIN,
    DEFAULT_ECL110_DISPLACE_MIN,
    DEFAULT_ECL110_DISPLACE_MAX,
    DEFAULT_ECL110_PID_TIME_CONSTANT,
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

DHW_PROFILE_STORE_VERSION = 1
DHW_PROFILE_EWMA_ALPHA = 0.12
DHW_PROFILE_MIN_INTENSITY = 0.2
DHW_PROFILE_MAX_INTENSITY = 3.5

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
            comfort_temp_day=self._config.get(
                CONF_COMFORT_TEMP_DAY, DEFAULT_COMFORT_TEMP_DAY
            ),
            comfort_temp_night=self._config.get(
                CONF_COMFORT_TEMP_NIGHT, DEFAULT_COMFORT_TEMP_NIGHT
            ),
            day_start_hour=int(
                self._config.get(CONF_DAY_START_HOUR, DEFAULT_DAY_START_HOUR)
            ),
            day_end_hour=int(
                self._config.get(CONF_DAY_END_HOUR, DEFAULT_DAY_END_HOUR)
            ),
            price_weight=self._config.get(CONF_PRICE_WEIGHT, DEFAULT_PRICE_WEIGHT),
            comfort_weight=self._config.get(
                CONF_COMFORT_WEIGHT, DEFAULT_COMFORT_WEIGHT
            ),
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

        # Solar / return temp state
        self._solar_radiation: float = 0.0
        self._floor_return_temp: float | None = None
        self._solar_radiation_forecast: list[float] = []

        # DHW state
        self._dhw_temperature: float | None = None
        self._last_dhw_temp_sample: float | None = None
        self._last_dhw_sample_time: datetime | None = None
        self._dhw_hourly_profile: list[float] = (
            self._thermal_params.dhw_hourly_draw_pattern.copy()
        )
        self._dhw_profile_store: Store = Store(
            hass,
            DHW_PROFILE_STORE_VERSION,
            f"{DOMAIN}_{entry.entry_id}_dhw_profile",
        )

        # ECL110 MQTT state
        self._ecl110_command_topic: str = self._config.get(
            CONF_ECL110_COMMAND_TOPIC, DEFAULT_ECL110_COMMAND_TOPIC
        )
        self._ecl110_displace_set_topic: str = self._config.get(
            CONF_ECL110_DISPLACE_SET_TOPIC, DEFAULT_ECL110_DISPLACE_SET_TOPIC
        )
        self._ecl110_state_topic: str = self._config.get(
            CONF_ECL110_STATE_TOPIC, DEFAULT_ECL110_STATE_TOPIC
        )
        self._ecl110_qos: int = int(self._config.get(CONF_ECL110_QOS, DEFAULT_ECL110_QOS))
        self._ecl110_retain: bool = bool(self._config.get(CONF_ECL110_RETAIN, DEFAULT_ECL110_RETAIN))
        self._ecl110_displace_min: float = float(
            self._config.get(CONF_ECL110_DISPLACE_MIN, DEFAULT_ECL110_DISPLACE_MIN)
        )
        self._ecl110_displace_max: float = float(
            self._config.get(CONF_ECL110_DISPLACE_MAX, DEFAULT_ECL110_DISPLACE_MAX)
        )
        self._ecl110_current_displace: float = 0.0
        self._ecl110_last_payload: dict[str, Any] = {}
        self._unsub_ecl110_state: Any = None

        # Subscribe to ECL110 state topic if MQTT is available
        hass.async_create_task(self._async_setup_ecl110_state_subscription())

        # Load learned DHW usage profile (persisted across restarts)
        hass.async_create_task(self._async_load_dhw_profile())

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
        return self._last_optimization

    @property
    def next_optimization(self) -> datetime | None:
        return self._next_optimization

    @property
    def current_action(self) -> dict[str, Any]:
        return self._current_action
    async def _async_setup_ecl110_state_subscription(self) -> None:
        """Subscribe to ECL110 MQTT state updates if MQTT integration is available."""
        if not self._ecl110_state_topic:
            return
        try:
            self._unsub_ecl110_state = await mqtt.async_subscribe(
                self.hass,
                self._ecl110_state_topic,
                self._async_handle_ecl110_state_message,
                qos=self._ecl110_qos,
            )
            _LOGGER.debug("Subscribed to ECL110 state topic: %s", self._ecl110_state_topic)
        except Exception as err:
            _LOGGER.debug("ECL110 MQTT state subscription not available: %s", err)

    @callback
    def _async_handle_ecl110_state_message(self, msg: Any) -> None:
        """Handle ECL110 MQTT state payload updates."""
        payload = msg.payload
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="ignore")
            data = json.loads(payload) if isinstance(payload, str) else payload

            displace: float | None = None
            if isinstance(data, dict):
                # Legacy state payload shape
                displace_raw = data.get("displace")
                if displace_raw is None and isinstance(data.get("command"), dict):
                    displace_raw = data["command"].get("displace")
                if displace_raw is not None:
                    displace = float(displace_raw)

                effective = data.get("effective_displace")
                if effective is not None:
                    self._current_state.ecl110_effective_displace = float(effective)
            elif isinstance(data, (int, float)):
                # New direct topic payload shape: scalar JSON value
                displace = float(data)

            if displace is not None:
                self._ecl110_current_displace = displace
                self._current_state.ecl110_displace_command = displace
        except Exception:
            # Ignore malformed payloads
            return

    @property
    def current_state(self) -> ThermalState:
        return self._current_state

    @property
    def prices(self) -> list[dict]:
        return self._prices

    @property
    def solar_radiation(self) -> float:
        """Current solar radiation reading."""
        return self._solar_radiation

    @property
    def floor_return_temp(self) -> float | None:
        """Current floor heating return temperature."""
        return self._floor_return_temp

    @property
    def dhw_temperature(self) -> float | None:
        """Current DHW temperature."""
        return self._dhw_temperature

    def _normalize_dhw_profile(self, profile: list[float]) -> list[float]:
        """Normalize and clamp DHW hourly profile (average ~= 1.0)."""
        if len(profile) != 24:
            profile = self._thermal_params.dhw_hourly_draw_pattern.copy()

        cleaned = [
            float(np.clip(v, DHW_PROFILE_MIN_INTENSITY, DHW_PROFILE_MAX_INTENSITY))
            for v in profile
        ]
        avg = float(np.mean(cleaned)) if cleaned else 1.0
        if avg <= 0:
            return self._thermal_params.dhw_hourly_draw_pattern.copy()

        normalized = [float(np.clip(v / avg, DHW_PROFILE_MIN_INTENSITY, DHW_PROFILE_MAX_INTENSITY)) for v in cleaned]
        return normalized

    async def _async_load_dhw_profile(self) -> None:
        """Load persisted DHW usage profile and apply it to the thermal model."""
        try:
            stored = await self._dhw_profile_store.async_load()
            if not stored:
                return

            profile = stored.get("hourly_profile")
            if not isinstance(profile, list) or len(profile) != 24:
                return

            self._dhw_hourly_profile = self._normalize_dhw_profile(profile)
            self._thermal_params.dhw_hourly_draw_pattern = self._dhw_hourly_profile.copy()
            _LOGGER.info("Loaded learned DHW usage profile from storage")
        except Exception as err:
            _LOGGER.debug("Could not load learned DHW profile: %s", err)

    async def _async_save_dhw_profile(self) -> None:
        """Persist learned DHW profile to Home Assistant storage."""
        try:
            await self._dhw_profile_store.async_save(
                {
                    "hourly_profile": self._dhw_hourly_profile,
                    "updated_at": dt_util.now().isoformat(),
                }
            )
        except Exception as err:
            _LOGGER.debug("Could not persist DHW profile: %s", err)

    async def _async_learn_dhw_usage(self, dhw_temp: float) -> None:
        """Learn hourly DHW usage profile from observed temperature drops."""
        now = dt_util.now()

        if self._last_dhw_temp_sample is None or self._last_dhw_sample_time is None:
            self._last_dhw_temp_sample = dhw_temp
            self._last_dhw_sample_time = now
            return

        dt_h = (now - self._last_dhw_sample_time).total_seconds() / 3600.0
        if dt_h <= 0.02 or dt_h > 6.0:
            self._last_dhw_temp_sample = dhw_temp
            self._last_dhw_sample_time = now
            return

        temp_drop = self._last_dhw_temp_sample - dhw_temp
        self._last_dhw_temp_sample = dhw_temp
        self._last_dhw_sample_time = now

        # Learn only on meaningful drops while DHW is not actively heated.
        if temp_drop < 0.15:
            return
        if bool(self._current_action.get("dhw_heating_active", False)):
            return

        draw_intensity = temp_drop / dt_h
        hour = now.hour

        profile = self._dhw_hourly_profile.copy()
        profile[hour] = (
            (1.0 - DHW_PROFILE_EWMA_ALPHA) * profile[hour]
            + DHW_PROFILE_EWMA_ALPHA * draw_intensity
        )
        self._dhw_hourly_profile = self._normalize_dhw_profile(profile)
        self._thermal_params.dhw_hourly_draw_pattern = self._dhw_hourly_profile.copy()

        _LOGGER.debug(
            "Learned DHW usage hour=%d drop=%.2f°C dt=%.2fh intensity=%.2f",
            hour,
            temp_drop,
            dt_h,
            draw_intensity,
        )
        await self._async_save_dhw_profile()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data and run optimization."""
        try:
            # Update current state from sensors
            await self._update_current_state()

            # Fetch prices from Tibber
            await self._fetch_tibber_prices()

            # Fetch weather forecast (full 24h for solar, wind, rain, temp)
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
                    "heat_pump_on": True,
                    "displace_value": min(4.0, self._ecl110_displace_max),
                }
            elif self._mode == MODE_BOOST:
                self._current_action = {
                    "power": self._thermal_model.params.max_electrical_power,
                    "setpoint": self._opt_config.max_temp,
                    "mode": "boost",
                    "price": self._get_current_price(),
                    "power_normalized": 1.0,
                    "heat_pump_on": True,
                    "displace_value": self._ecl110_displace_max,
                }
            elif self._mode == MODE_OFF:
                self._current_action = {
                    "power": 0.0,
                    "setpoint": self._opt_config.min_temp,
                    "mode": "off",
                    "price": self._get_current_price(),
                    "power_normalized": 0.0,
                    "heat_pump_on": False,
                    "displace_value": self._ecl110_displace_min,
                }

            # Apply current action to heat pump
            await self._apply_action()

            self._next_optimization = dt_util.now() + timedelta(
                minutes=self._config.get(
                    CONF_OPTIMIZATION_INTERVAL, DEFAULT_OPTIMIZATION_INTERVAL
                )
            )

            return self._build_data_dict()

        except Exception as err:
            _LOGGER.error(
                "Error updating Heat Pump Optimizer: %s", err, exc_info=True
            )
            raise UpdateFailed(f"Error updating data: {err}") from err

    async def async_run_optimization(self) -> None:
        """Run the MPC optimization."""
        _LOGGER.info("Running heat pump optimization (predictive MPC)")

        try:
            prices, outdoor_temps, wind_speeds, precipitation, solar_rad = (
                self._prepare_forecast_data()
            )

            if len(prices) < 4:
                _LOGGER.warning(
                    "Not enough price data for optimization (got %d steps)",
                    len(prices),
                )
                return

            _LOGGER.debug(
                "Forecast data: %d steps, wind range=%.1f-%.1f m/s, "
                "precip range=%.1f-%.1f mm/h, solar range=%.0f-%.0f W/m²",
                len(prices),
                float(np.min(wind_speeds)), float(np.max(wind_speeds)),
                float(np.min(precipitation)), float(np.max(precipitation)),
                float(np.min(solar_rad)), float(np.max(solar_rad)),
            )

            # Run optimization in executor to avoid blocking
            result = await self.hass.async_add_executor_job(
                self._optimizer.optimize,
                self._current_state,
                prices,
                outdoor_temps,
                wind_speeds,
                precipitation,
                solar_rad,
                dt_util.now(),
            )

            self._optimization_result = result
            self._last_optimization = dt_util.now()

            self._current_action = self._optimizer.get_current_action(
                result, dt_util.now()
            )

            _LOGGER.info(
                "Optimization complete: savings=%.1f%%, cost=%.2f, status=%s, "
                "dhw_enabled=%s",
                result.savings_percentage,
                result.predicted_cost,
                result.status,
                self._thermal_params.dhw_enabled,
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
            self._thermal_params.heat_loss_coefficient = params[
                "house_heat_loss_coefficient"
            ]
        if "ecl110_displace_min" in params:
            self._thermal_params.ecl110_displace_min = params["ecl110_displace_min"]
            self._ecl110_displace_min = params["ecl110_displace_min"]
        if "ecl110_displace_max" in params:
            self._thermal_params.ecl110_displace_max = params["ecl110_displace_max"]
            self._ecl110_displace_max = params["ecl110_displace_max"]
            self._thermal_params.slab_thermal_mass = params["slab_thermal_mass"]
        if "slab_heat_transfer" in params:
            self._thermal_params.slab_heat_transfer = params["slab_heat_transfer"]
        if "heat_pump_cop_nominal" in params:
            self._thermal_params.cop_nominal = params["heat_pump_cop_nominal"]
        # Two-zone params
        if "upper_floor_thermal_mass" in params:
            self._thermal_params.upper_floor_thermal_mass = params[
                "upper_floor_thermal_mass"
            ]
        if "lower_floor_thermal_mass" in params:
            self._thermal_params.lower_floor_thermal_mass = params[
                "lower_floor_thermal_mass"
            ]
        if "inter_zone_heat_transfer" in params:
            self._thermal_params.inter_zone_transfer = params[
                "inter_zone_heat_transfer"
            ]
        if "radiator_power_fraction" in params:
            self._thermal_params.radiator_power_fraction = params[
                "radiator_power_fraction"
            ]
        if "window_area" in params:
            self._thermal_params.window_area = params["window_area"]
        if "solar_heat_gain_coefficient" in params:
            self._thermal_params.solar_heat_gain_coefficient = params[
                "solar_heat_gain_coefficient"
            ]
        # DHW params
        if "dhw_tank_volume" in params:
            self._thermal_params.dhw_tank_volume = params["dhw_tank_volume"]
        if "dhw_setpoint" in params:
            self._thermal_params.dhw_setpoint = params["dhw_setpoint"]
        if "ecl110_pid_time_constant_hours" in params:
            self._thermal_params.ecl110_pid_time_constant_hours = params[
                "ecl110_pid_time_constant_hours"
            ]
        if "dhw_min_temperature" in params:
            self._thermal_params.dhw_min_temp = params["dhw_min_temperature"]
        if "dhw_daily_consumption" in params:
            self._thermal_params.dhw_daily_consumption = params["dhw_daily_consumption"]
        # Weather sensitivity params
        if "wind_sensitivity_factor" in params:
            self._thermal_params.wind_sensitivity = params["wind_sensitivity_factor"]
        if "rain_heat_loss_multiplier" in params:
            self._thermal_params.rain_heat_loss_multiplier = params[
                "rain_heat_loss_multiplier"
            ]

        self._thermal_model = ThermalModel(self._thermal_params)
        self._optimizer = HeatPumpOptimizer(self._thermal_model, self._opt_config)

        _LOGGER.info("Thermal parameters updated, re-running optimization")
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Shut down the coordinator."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_ecl110_state:
            self._unsub_ecl110_state()
            self._unsub_ecl110_state = None

    async def _update_current_state(self) -> None:
        """Update current thermal state from HA entities."""
        # Indoor temperature
        indoor_entity = self._config.get(CONF_INDOOR_TEMP_ENTITY)
        if indoor_entity:
            state = self.hass.states.get(indoor_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._current_state.room_temperature = float(state.state)
                    # For two-zone: indoor sensor is typically upper floor
                    self._current_state.upper_floor_temperature = float(state.state)
                except (ValueError, TypeError):
                    pass

        # Outdoor temperature
        outdoor_entity = self._config.get(CONF_OUTDOOR_TEMP_ENTITY)
        if outdoor_entity:
            state = self.hass.states.get(outdoor_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._current_state.outdoor_temperature = float(state.state)
                except (ValueError, TypeError):
                    pass

        # Floor heating return temperature sensor
        floor_return_entity = self._config.get(CONF_FLOOR_RETURN_TEMP_ENTITY)
        if floor_return_entity:
            state = self.hass.states.get(floor_return_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._floor_return_temp = float(state.state)
                    self._current_state.floor_return_temperature = (
                        self._floor_return_temp
                    )
                    # Update slab temperature estimate from return temp
                    self._thermal_model.update_slab_from_return_temp(
                        self._current_state, self._floor_return_temp
                    )
                    # Lower floor temp ~ return temp (rough estimate)
                    self._current_state.lower_floor_temperature = (
                        self._floor_return_temp + 0.5
                    )
                except (ValueError, TypeError):
                    pass

        # Solar radiation sensor
        solar_entity = self._config.get(CONF_SOLAR_RADIATION_ENTITY)
        if solar_entity:
            state = self.hass.states.get(solar_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._solar_radiation = float(state.state)
                    self._current_state.solar_radiation = self._solar_radiation
                except (ValueError, TypeError):
                    pass

        # DHW temperature sensor
        dhw_entity = self._config.get(CONF_DHW_TEMP_ENTITY)
        if dhw_entity:
            state = self.hass.states.get(dhw_entity)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    self._dhw_temperature = float(state.state)
                    self._current_state.dhw_temperature = self._dhw_temperature
                    await self._async_learn_dhw_usage(self._dhw_temperature)
                except (ValueError, TypeError):
                    pass

        # Update ECL110 effective displace state (PID/PI lag approximation)
        if "displace_value" in self._current_action:
            displace_cmd = float(self._current_action.get("displace_value", 0.0))
            dt_h = self._opt_config.dt_hours
            self._thermal_model.update_ecl110_displace_state(
                self._current_state,
                displace_cmd,
                dt_h,
            )
            self._ecl110_current_displace = self._current_state.ecl110_displace_command

        # If no floor return sensor, estimate slab from room temp
        if not floor_return_entity:
            if not hasattr(self, "_slab_temp_initialized"):
                self._current_state.slab_temperature = (
                    self._current_state.room_temperature + 1.0
                )
                self._current_state.lower_floor_temperature = (
                    self._current_state.room_temperature
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

        query_data = (
            '{"query": "'
            + TIBBER_PRICE_QUERY.replace("\n", " ").replace('"', '\\"')
            + '"}'
        )

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

            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                _LOGGER.error("No homes found in Tibber data")
                return

            price_info = (
                homes[0].get("currentSubscription", {}).get("priceInfo", {})
            )

            prices = []
            for period in ["today", "tomorrow"]:
                period_prices = price_info.get(period, [])
                if period_prices:
                    for p in period_prices:
                        prices.append(
                            {
                                "total": p.get("total", 0),
                                "starts_at": p.get("startsAt", ""),
                                "level": p.get("level", "NORMAL"),
                            }
                        )

            self._prices = prices
            _LOGGER.debug("Fetched %d price entries from Tibber", len(prices))

        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching Tibber prices: %s", err)
        except Exception as err:
            _LOGGER.error(
                "Unexpected error fetching prices: %s", err, exc_info=True
            )

    async def _fetch_weather_forecast(self) -> None:
        """Fetch full 24-hour weather forecast from Home Assistant weather entity.

        Extracts per-hour forecasts for:
        - Temperature (°C)
        - Wind speed (m/s)
        - Precipitation (mm/h)
        - Solar radiation / irradiance (W/m²)

        These FORECAST values (not current conditions) are what enable
        true predictive/anticipatory control in the MPC optimizer.
        """
        weather_entity = self._config.get(CONF_WEATHER_ENTITY)
        if not weather_entity:
            _LOGGER.warning("No weather entity configured")
            return

        try:
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

                # Extract solar radiation forecast if present in weather data
                self._solar_radiation_forecast = []
                for fc in forecast_data:
                    # Some weather integrations provide solar irradiance
                    sr = fc.get("solar_irradiance") or fc.get(
                        "native_solar_irradiance", 0.0
                    )
                    self._solar_radiation_forecast.append(float(sr or 0.0))

                _LOGGER.debug(
                    "Fetched %d weather forecast entries (full 24h+ trajectory: "
                    "temp, wind, rain, solar)",
                    len(forecast_data),
                )
            else:
                _LOGGER.warning(
                    "No forecast data returned for %s", weather_entity
                )

        except Exception as err:
            _LOGGER.warning(
                "Error fetching weather forecast: %s. Using fallback.", err
            )
            state = self.hass.states.get(weather_entity)
            if state:
                try:
                    temp = float(state.attributes.get("temperature", 5.0))
                    wind = float(state.attributes.get("wind_speed", 0.0))
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
                    self._solar_radiation_forecast = [0.0] * 48
                except (ValueError, TypeError):
                    pass

    def _prepare_forecast_data(
        self,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Prepare full 24-hour forecast arrays for the optimizer.

        CRITICAL: This provides the FORECAST TRAJECTORIES (not just current values)
        that enable true predictive optimization. Each array contains per-step
        forecasted values for the entire optimization horizon.

        Returns: (prices, outdoor_temps, wind_speeds, precipitation, solar_radiation)
        """
        dt_minutes = 15
        n_steps = self._opt_config.n_steps
        now = dt_util.now()

        # --- Prices ---
        prices_15min = []
        if self._prices:
            for price_entry in self._prices:
                total = price_entry.get("total", 0)
                for _ in range(4):
                    prices_15min.append(total)
        else:
            prices_15min = [0.5] * (n_steps + 10)

        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        minutes_since_midnight = (now - midnight).total_seconds() / 60
        step_offset = int(minutes_since_midnight / dt_minutes)

        if step_offset < len(prices_15min):
            prices = prices_15min[step_offset : step_offset + n_steps]
        else:
            prices = prices_15min[:n_steps]

        while len(prices) < n_steps:
            prices.append(prices[-1] if prices else 0.5)

        # --- Weather forecast (FULL 24h trajectories) ---
        outdoor_temps = []
        wind_speeds = []
        precipitation_rates = []
        solar_rad = []

        if self._weather_forecast:
            for idx, fc in enumerate(self._weather_forecast):
                temp = fc.get("temperature", 5.0)
                wind = fc.get("wind_speed", 0.0)
                if wind > 30:
                    wind = wind / 3.6  # Convert km/h to m/s
                precip = fc.get("precipitation", 0.0) or 0.0

                # Solar radiation: from forecast or from separate list
                sr = 0.0
                if idx < len(self._solar_radiation_forecast):
                    sr = self._solar_radiation_forecast[idx]
                if sr == 0.0:
                    sr = float(
                        fc.get("solar_irradiance")
                        or fc.get("native_solar_irradiance", 0.0)
                        or 0.0
                    )

                # Interpolate hourly forecast to 15-min steps
                for _ in range(4):
                    outdoor_temps.append(temp)
                    wind_speeds.append(wind)
                    precipitation_rates.append(precip)
                    solar_rad.append(sr)
        else:
            # Fallback: use current conditions (NOT ideal for predictive MPC)
            base_temp = self._current_state.outdoor_temperature
            current_sr = self._solar_radiation
            _LOGGER.warning(
                "No weather forecast available — using current conditions. "
                "Predictive optimization will be limited."
            )
            for _ in range(n_steps):
                outdoor_temps.append(base_temp)
                wind_speeds.append(0.0)
                precipitation_rates.append(0.0)
                solar_rad.append(current_sr)

        # Pad to ensure we have enough data points
        for arr in [outdoor_temps, wind_speeds, precipitation_rates, solar_rad]:
            while len(arr) < n_steps:
                arr.append(arr[-1] if arr else 0.0)

        return (
            np.array(prices[:n_steps], dtype=float),
            np.array(outdoor_temps[:n_steps], dtype=float),
            np.array(wind_speeds[:n_steps], dtype=float),
            np.array(precipitation_rates[:n_steps], dtype=float),
            np.array(solar_rad[:n_steps], dtype=float),
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

        return self._prices[0].get("total", 0) if self._prices else 0.0

    async def async_publish_ecl110_command(
        self,
        displace_value: float,
        heat_pump_on: bool,
        reason: str = "optimizer",
    ) -> None:
        """Publish ECL110 displace command via direct `/set` topic and optional legacy JSON topic."""
        displace = float(
            np.clip(displace_value, self._ecl110_displace_min, self._ecl110_displace_max)
        )
        displace_int = int(round(displace))

        legacy_payload = {
            "source": DOMAIN,
            "reason": reason,
            "timestamp": dt_util.now().isoformat(),
            "command": {
                "type": "ecl110_control",
                "heat_pump_on": bool(heat_pump_on),
                "displace": displace_int,
            },
            "context": {
                "price": self._current_action.get("price"),
                "mode": self._current_action.get("mode"),
                "pre_heat_urgency": self._current_action.get("pre_heat_urgency"),
            },
        }

        self._ecl110_last_payload = legacy_payload
        self._ecl110_current_displace = float(displace_int)

        if not self._ecl110_displace_set_topic and not self._ecl110_command_topic:
            return

        # Preferred path: write plain numeric payload directly to /set topic.
        if self._ecl110_displace_set_topic:
            try:
                await self.hass.services.async_call(
                    "mqtt",
                    "publish",
                    {
                        "topic": self._ecl110_displace_set_topic,
                        "payload": str(displace_int),
                        "qos": int(self._ecl110_qos),
                        "retain": bool(self._ecl110_retain),
                    },
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Error publishing ECL110 direct displace MQTT command: %s", err)

        # Backward compatibility path: optional legacy JSON command topic.
        if self._ecl110_command_topic:
            try:
                await self.hass.services.async_call(
                    "mqtt",
                    "publish",
                    {
                        "topic": self._ecl110_command_topic,
                        "payload": json.dumps(legacy_payload),
                        "qos": int(self._ecl110_qos),
                        "retain": bool(self._ecl110_retain),
                    },
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Error publishing ECL110 legacy MQTT command: %s", err)

    async def async_publish_current_action(self, reason: str = "optimizer") -> None:
        """Publish MQTT command for the currently selected optimizer action."""
        if not self._current_action:
            return
        await self.async_publish_ecl110_command(
            displace_value=float(self._current_action.get("displace_value", 0.0)),
            heat_pump_on=bool(self._current_action.get("heat_pump_on", False)),
            reason=reason,
        )

    async def _apply_action(self) -> None:
        """Apply current action as (heat_pump_on, displace_value)."""
        if not self._current_action:
            return

        heat_pump_on = bool(self._current_action.get("heat_pump_on", False))

        # 1) Toggle heat pump supply (ON/OFF)
        switch_entity = self._config.get(CONF_HEAT_PUMP_SWITCH_ENTITY)
        if switch_entity:
            try:
                await self.hass.services.async_call(
                    "switch",
                    "turn_on" if heat_pump_on else "turn_off",
                    {"entity_id": switch_entity},
                    blocking=True,
                )
            except Exception as err:
                _LOGGER.error("Error toggling heat pump switch: %s", err)

        # 2) Publish ECL110 displace command
        await self.async_publish_current_action(reason="scheduled_update")
    def _build_data_dict(self) -> dict[str, Any]:
        """Build the data dictionary for the coordinator."""
        result = self._optimization_result

        # Compute current solar gain
        current_solar_gain = self._thermal_model.compute_solar_gain(
            self._solar_radiation
        )

        data = {
            "mode": self._mode,
            "current_action": self._current_action,
            "current_price": self._get_current_price(),
            "indoor_temperature": self._current_state.room_temperature,
            "outdoor_temperature": self._current_state.outdoor_temperature,
            "slab_temperature": self._current_state.slab_temperature,
            "upper_floor_temperature": self._current_state.upper_floor_temperature,
            "lower_floor_temperature": self._current_state.lower_floor_temperature,
            "buffer_tank_temperature": self._current_state.buffer_tank_temperature,
            "floor_return_temperature": self._floor_return_temp,
            "solar_radiation": self._solar_radiation,
            "solar_heat_gain": current_solar_gain,
            "two_zone_enabled": self._thermal_params.two_zone_enabled,
            "dhw_enabled": self._thermal_params.dhw_enabled,
            "dhw_temperature": self._dhw_temperature or self._current_state.dhw_temperature,
            "dhw_setpoint": self._thermal_params.dhw_setpoint,
            "dhw_min_temperature": self._thermal_params.dhw_min_temp,
            "dhw_usage_profile": self._dhw_hourly_profile,
            "last_optimization": self._last_optimization,
            "next_optimization": self._next_optimization,
            "prices_available": len(self._prices),
            "weather_forecast_available": len(self._weather_forecast),
            "ecl110_command_topic": self._ecl110_command_topic,
            "ecl110_state_topic": self._ecl110_state_topic,
            "ecl110_displace": self._current_action.get("displace_value", self._ecl110_current_displace),
            "ecl110_effective_displace": self._current_state.ecl110_effective_displace,
            "ecl110_last_payload": self._ecl110_last_payload,
        }

        if result:
            # DHW schedule data
            dhw_schedule = []
            if result.dhw_power_schedule:
                for i, (ts, dp, dt_val) in enumerate(zip(
                    result.timestamps[:24],
                    result.dhw_power_schedule[:24],
                    result.dhw_temp_trajectory[1:25] if result.dhw_temp_trajectory else [0.0] * 24,
                )):
                    dhw_schedule.append({
                        "time": ts.isoformat(),
                        "dhw_power": round(dp, 2),
                        "dhw_temp": round(dt_val, 1),
                    })

            data.update(
                {
                    "predicted_cost": result.predicted_cost,
                    "baseline_cost": result.baseline_cost,
                    "predicted_savings": result.predicted_savings,
                    "savings_percentage": result.savings_percentage,
                    "optimization_status": result.status,
                    "solve_time_ms": result.solve_time_ms,
                    "dhw_heating_cost": result.dhw_heating_cost,
                    "dhw_heating_active": self._current_action.get("dhw_heating_active", False),
                    "dhw_schedule": dhw_schedule,
                    # Predictive info
                    "predictive_info": result.predictive_info,
                    "schedule": [
                        {
                            "time": ts.isoformat(),
                            "power": p,
                            "setpoint": s,
                            "price": pr,
                            "room_temp": rt,
                            "upper_temp": ut,
                            "lower_temp": lt,
                            "solar_gain": sg,
                            "displace": (
                                result.displace_schedule[idx]
                                if result.displace_schedule and idx < len(result.displace_schedule)
                                else 0.0
                            ),
                            "heat_pump_on": (
                                result.heat_pump_on_schedule[idx]
                                if result.heat_pump_on_schedule and idx < len(result.heat_pump_on_schedule)
                                else p > 0.1
                            ),
                        }
                        for idx, (ts, p, s, pr, rt, ut, lt, sg) in enumerate(zip(
                            result.timestamps[:24],
                            result.power_schedule[:24],
                            result.optimal_setpoints[:24],
                            result.prices[:24],
                            result.room_temp_trajectory[1:25],
                            (
                                result.upper_temp_trajectory[1:25]
                                if result.upper_temp_trajectory
                                else result.room_temp_trajectory[1:25]
                            ),
                            (
                                result.lower_temp_trajectory[1:25]
                                if result.lower_temp_trajectory
                                else result.room_temp_trajectory[1:25]
                            ),
                            (
                                result.solar_gain_trajectory[:24]
                                if result.solar_gain_trajectory
                                else [0.0] * 24
                            ),
                        ))
                    ],
                }
            )
        else:
            data.update(
                {
                    "predicted_cost": None,
                    "baseline_cost": None,
                    "predicted_savings": None,
                    "savings_percentage": None,
                    "optimization_status": "not_run",
                    "solve_time_ms": 0,
                    "dhw_heating_cost": 0.0,
                    "dhw_heating_active": False,
                    "dhw_schedule": [],
                    "predictive_info": {},
                    "schedule": [],
                }
            )

        return data