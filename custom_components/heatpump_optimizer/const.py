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

# Two-zone sensor configuration
CONF_SOLAR_RADIATION_ENTITY: Final = "solar_radiation_entity"
CONF_FLOOR_RETURN_TEMP_ENTITY: Final = "floor_return_temp_entity"

# DHW sensor configuration
CONF_DHW_TEMP_ENTITY: Final = "dhw_temp_entity"

# Temperature settings
CONF_TARGET_TEMP: Final = "target_temperature"
CONF_MIN_TEMP: Final = "min_temperature"
CONF_MAX_TEMP: Final = "max_temperature"
CONF_COMFORT_TEMP_DAY: Final = "comfort_temp_day"
CONF_COMFORT_TEMP_NIGHT: Final = "comfort_temp_night"
CONF_DAY_START_HOUR: Final = "day_start_hour"
CONF_DAY_END_HOUR: Final = "day_end_hour"

# Thermal model parameters (legacy / general)
CONF_HOUSE_THERMAL_MASS: Final = "house_thermal_mass"  # kWh/°C
CONF_HOUSE_HEAT_LOSS_COEFFICIENT: Final = "house_heat_loss_coefficient"  # kW/°C
CONF_SLAB_THERMAL_MASS: Final = "slab_thermal_mass"  # kWh/°C
CONF_SLAB_HEAT_TRANSFER: Final = "slab_heat_transfer"  # kW/°C
CONF_HEAT_PUMP_COP_NOMINAL: Final = "heat_pump_cop_nominal"
CONF_HEAT_PUMP_MAX_POWER: Final = "heat_pump_max_power"  # kW electrical
CONF_HEAT_PUMP_MIN_POWER: Final = "heat_pump_min_power"  # kW electrical

# Two-zone model parameters
CONF_UPPER_FLOOR_THERMAL_MASS: Final = "upper_floor_thermal_mass"  # kWh/°C
CONF_LOWER_FLOOR_THERMAL_MASS: Final = "lower_floor_thermal_mass"  # kWh/°C
CONF_UPPER_FLOOR_HEAT_LOSS: Final = "upper_floor_heat_loss"  # kW/°C
CONF_LOWER_FLOOR_HEAT_LOSS: Final = "lower_floor_heat_loss"  # kW/°C
CONF_INTER_ZONE_TRANSFER: Final = "inter_zone_heat_transfer"  # kW/°C
CONF_RADIATOR_POWER_FRACTION: Final = "radiator_power_fraction"  # 0-1
CONF_UPPER_FLOOR_AREA_RATIO: Final = "upper_floor_area_ratio"  # 0-1
CONF_BUFFER_TANK_VOLUME: Final = "buffer_tank_volume"  # liters
CONF_BUFFER_TANK_LOSS: Final = "buffer_tank_heat_loss"  # kW/°C

# Solar gain parameters
CONF_WINDOW_AREA: Final = "window_area"  # m²
CONF_SOLAR_ORIENTATION_FACTOR: Final = "solar_orientation_factor"  # 0-1
CONF_SOLAR_HEAT_GAIN_COEFF: Final = "solar_heat_gain_coefficient"  # SHGC 0-1
CONF_SOLAR_UPPER_FRACTION: Final = "solar_upper_fraction"  # fraction going to upper floor

# DHW (Domestic Hot Water) parameters
CONF_DHW_TANK_VOLUME: Final = "dhw_tank_volume"  # liters
CONF_DHW_SETPOINT: Final = "dhw_setpoint"  # °C
CONF_DHW_MIN_TEMP: Final = "dhw_min_temperature"  # °C
CONF_DHW_DAILY_CONSUMPTION: Final = "dhw_daily_consumption"  # liters/day

# Weather sensitivity parameters
CONF_WIND_SENSITIVITY: Final = "wind_sensitivity_factor"  # fraction per m/s
CONF_RAIN_HEAT_LOSS_MULTIPLIER: Final = "rain_heat_loss_multiplier"  # multiplier

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

# Two-zone defaults
DEFAULT_UPPER_FLOOR_THERMAL_MASS: Final = 3.0  # kWh/°C - lighter (radiators + air)
DEFAULT_LOWER_FLOOR_THERMAL_MASS: Final = 8.0  # kWh/°C - heavy concrete slab
DEFAULT_UPPER_FLOOR_HEAT_LOSS: Final = 0.08  # kW/°C
DEFAULT_LOWER_FLOOR_HEAT_LOSS: Final = 0.07  # kW/°C
DEFAULT_INTER_ZONE_TRANSFER: Final = 0.5  # kW/°C - open layout heat transfer
DEFAULT_RADIATOR_POWER_FRACTION: Final = 0.4  # 40% to radiators, 60% to floor
DEFAULT_UPPER_FLOOR_AREA_RATIO: Final = 0.5  # equal floors
DEFAULT_BUFFER_TANK_VOLUME: Final = 35.0  # liters
DEFAULT_BUFFER_TANK_LOSS: Final = 0.01  # kW/°C - small tank

# Solar gain defaults
DEFAULT_WINDOW_AREA: Final = 10.0  # m² total glazing area
DEFAULT_SOLAR_ORIENTATION_FACTOR: Final = 0.7  # south-facing bias
DEFAULT_SOLAR_HEAT_GAIN_COEFF: Final = 0.7  # typical double-glazed low-e
DEFAULT_SOLAR_UPPER_FRACTION: Final = 0.4  # 40% upper, 60% lower (open layout, sun hits lower floor)

# DHW defaults
DEFAULT_DHW_TANK_VOLUME: Final = 200.0  # liters
DEFAULT_DHW_SETPOINT: Final = 55.0  # °C
DEFAULT_DHW_MIN_TEMP: Final = 45.0  # °C - legionella safety
DEFAULT_DHW_DAILY_CONSUMPTION: Final = 150.0  # liters/day average household

# Weather sensitivity defaults
DEFAULT_WIND_SENSITIVITY: Final = 0.15  # 15% heat loss increase per m/s wind
DEFAULT_RAIN_HEAT_LOSS_MULTIPLIER: Final = 1.15  # 15% increase when raining

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

# Two-zone attributes
ATTR_UPPER_FLOOR_TEMP: Final = "upper_floor_temperature"
ATTR_LOWER_FLOOR_TEMP: Final = "lower_floor_temperature"
ATTR_UPPER_FLOOR_SETPOINT: Final = "upper_floor_setpoint"
ATTR_LOWER_FLOOR_SETPOINT: Final = "lower_floor_setpoint"
ATTR_SLAB_TEMP: Final = "slab_temperature"
ATTR_BUFFER_TANK_TEMP: Final = "buffer_tank_temperature"
ATTR_SOLAR_GAIN: Final = "solar_heat_gain"
ATTR_SOLAR_RADIATION: Final = "solar_radiation"
ATTR_FLOOR_RETURN_TEMP: Final = "floor_return_temperature"

# DHW attributes
ATTR_DHW_TEMP: Final = "dhw_temperature"
ATTR_DHW_SETPOINT: Final = "dhw_setpoint"
ATTR_DHW_HEATING_ACTIVE: Final = "dhw_heating_active"
ATTR_DHW_HEATING_SCHEDULE: Final = "dhw_heating_schedule"
ATTR_DHW_HEATING_COST: Final = "dhw_heating_cost"

# Wind chill factor (additional heat loss per m/s wind) — legacy, now configurable
WIND_CHILL_FACTOR: Final = 0.005  # kW/°C per m/s
# Rain cooling factor — legacy, now configurable
RAIN_COOLING_FACTOR: Final = 0.01  # kW/°C per mm/h