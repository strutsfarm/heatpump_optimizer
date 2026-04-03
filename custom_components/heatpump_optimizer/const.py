"""Constants for Heat Pump Cost Optimizer."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "heatpump_optimizer"
PLATFORMS: Final = ["sensor", "climate", "switch"]

# Configuration keys
CONF_TIBBER_TOKEN: Final = "tibber_token"
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_INDOOR_TEMP_ENTITY: Final = "indoor_temp_entity"
CONF_OUTDOOR_TEMP_ENTITY: Final = "outdoor_temp_entity"
CONF_HEAT_PUMP_ENTITY: Final = "heat_pump_entity"
CONF_HEAT_PUMP_SWITCH_ENTITY: Final = "heat_pump_switch_entity"

# Temperature settings
CONF_TARGET_TEMP: Final = "target_temperature"
CONF_MIN_TEMP: Final = "min_temperature"
CONF_MAX_TEMP: Final = "max_temperature"
CONF_COMFORT_TEMP_DAY: Final = "comfort_temp_day"
CONF_COMFORT_TEMP_NIGHT: Final = "comfort_temp_night"
CONF_DAY_START_HOUR: Final = "day_start_hour"
CONF_DAY_END_HOUR: Final = "day_end_hour"

# Thermal model parameters
CONF_HOUSE_THERMAL_MASS: Final = "house_thermal_mass"  # kWh/°C
CONF_HOUSE_HEAT_LOSS_COEFFICIENT: Final = "house_heat_loss_coefficient"  # kW/°C
CONF_SLAB_THERMAL_MASS: Final = "slab_thermal_mass"  # kWh/°C
CONF_SLAB_HEAT_TRANSFER: Final = "slab_heat_transfer"  # kW/°C
CONF_HEAT_PUMP_COP_NOMINAL: Final = "heat_pump_cop_nominal"
CONF_HEAT_PUMP_MAX_POWER: Final = "heat_pump_max_power"  # kW electrical
CONF_HEAT_PUMP_MIN_POWER: Final = "heat_pump_min_power"  # kW electrical

# Optimization settings
CONF_OPTIMIZATION_HORIZON: Final = "optimization_horizon"  # hours
CONF_OPTIMIZATION_INTERVAL: Final = "optimization_interval"  # minutes
CONF_TIME_STEP: Final = "time_step"  # minutes
CONF_PRICE_WEIGHT: Final = "price_weight"
CONF_COMFORT_WEIGHT: Final = "comfort_weight"

# Defaults
DEFAULT_TARGET_TEMP: Final = 21.0
DEFAULT_MIN_TEMP: Final = 19.0
DEFAULT_MAX_TEMP: Final = 23.0
DEFAULT_COMFORT_TEMP_DAY: Final = 21.0
DEFAULT_COMFORT_TEMP_NIGHT: Final = 19.5
DEFAULT_DAY_START_HOUR: Final = 7
DEFAULT_DAY_END_HOUR: Final = 22

DEFAULT_HOUSE_THERMAL_MASS: Final = 10.0  # kWh/°C - typical well-insulated house
DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT: Final = 0.15  # kW/°C
DEFAULT_SLAB_THERMAL_MASS: Final = 5.0  # kWh/°C - concrete slab
DEFAULT_SLAB_HEAT_TRANSFER: Final = 0.8  # kW/°C - slab to room
DEFAULT_HEAT_PUMP_COP_NOMINAL: Final = 3.5
DEFAULT_HEAT_PUMP_MAX_POWER: Final = 5.0  # kW
DEFAULT_HEAT_PUMP_MIN_POWER: Final = 1.0  # kW

DEFAULT_OPTIMIZATION_HORIZON: Final = 24  # hours
DEFAULT_OPTIMIZATION_INTERVAL: Final = 30  # minutes
DEFAULT_TIME_STEP: Final = 15  # minutes
DEFAULT_PRICE_WEIGHT: Final = 1.0
DEFAULT_COMFORT_WEIGHT: Final = 5.0

# Update intervals
UPDATE_INTERVAL_PRICES: Final = timedelta(minutes=15)
UPDATE_INTERVAL_WEATHER: Final = timedelta(minutes=30)
UPDATE_INTERVAL_OPTIMIZATION: Final = timedelta(minutes=30)

# Optimization modes
MODE_COMFORT: Final = "comfort"
MODE_ECONOMY: Final = "economy"
MODE_OFF: Final = "off"
MODE_BOOST: Final = "boost"
MODE_AUTO: Final = "auto"

# Service names
SERVICE_RUN_OPTIMIZATION: Final = "run_optimization"
SERVICE_SET_MODE: Final = "set_mode"
SERVICE_SET_THERMAL_PARAMS: Final = "set_thermal_parameters"

# Attributes
ATTR_NEXT_OPTIMIZATION: Final = "next_optimization"
ATTR_LAST_OPTIMIZATION: Final = "last_optimization"
ATTR_CURRENT_SCHEDULE: Final = "current_schedule"
ATTR_PREDICTED_SAVINGS: Final = "predicted_savings"
ATTR_PREDICTED_COST: Final = "predicted_cost"
ATTR_BASELINE_COST: Final = "baseline_cost"
ATTR_OPTIMIZATION_STATUS: Final = "optimization_status"
ATTR_CURRENT_PRICE: Final = "current_price"
ATTR_AVG_PRICE_24H: Final = "average_price_24h"
ATTR_INDOOR_TEMP: Final = "indoor_temperature"
ATTR_OUTDOOR_TEMP: Final = "outdoor_temperature"
ATTR_HEAT_PUMP_STATE: Final = "heat_pump_state"
ATTR_HEAT_PUMP_SETPOINT: Final = "heat_pump_setpoint"
ATTR_COP_CURRENT: Final = "current_cop"

# Wind chill factor (additional heat loss per m/s wind)
WIND_CHILL_FACTOR: Final = 0.005  # kW/°C per m/s
# Rain cooling factor
RAIN_COOLING_FACTOR: Final = 0.01  # kW/°C per mm/h
