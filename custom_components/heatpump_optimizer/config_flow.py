"""Config flow for Heat Pump Cost Optimizer integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

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
    CONF_OPTIMIZATION_HORIZON,
    CONF_OPTIMIZATION_INTERVAL,
    CONF_TIME_STEP,
    CONF_PRICE_WEIGHT,
    CONF_COMFORT_WEIGHT,
    DEFAULT_TARGET_TEMP,
    DEFAULT_MIN_TEMP,
    DEFAULT_MAX_TEMP,
    DEFAULT_COMFORT_TEMP_DAY,
    DEFAULT_COMFORT_TEMP_NIGHT,
    DEFAULT_DAY_START_HOUR,
    DEFAULT_DAY_END_HOUR,
    DEFAULT_HOUSE_THERMAL_MASS,
    DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT,
    DEFAULT_SLAB_THERMAL_MASS,
    DEFAULT_SLAB_HEAT_TRANSFER,
    DEFAULT_HEAT_PUMP_COP_NOMINAL,
    DEFAULT_HEAT_PUMP_MAX_POWER,
    DEFAULT_HEAT_PUMP_MIN_POWER,
    DEFAULT_OPTIMIZATION_HORIZON,
    DEFAULT_OPTIMIZATION_INTERVAL,
    DEFAULT_TIME_STEP,
    DEFAULT_PRICE_WEIGHT,
    DEFAULT_COMFORT_WEIGHT,
)

_LOGGER = logging.getLogger(__name__)

TIBBER_API_URL = "https://api.tibber.com/v1-beta/gql"


async def validate_tibber_token(token: str) -> bool:
    """Validate the Tibber API token."""
    query = '{ "query": "{ viewer { name } }" }'
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                TIBBER_API_URL, data=query, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return "errors" not in data
                return False
    except Exception:
        return False


class HeatPumpOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Heat Pump Optimizer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - API credentials and entity selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate Tibber token
            if not await validate_tibber_token(user_input[CONF_TIBBER_TOKEN]):
                errors[CONF_TIBBER_TOKEN] = "invalid_tibber_token"
            else:
                self._data.update(user_input)
                return await self.async_step_temperature()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default="Heat Pump Optimizer"): str,
                    vol.Required(CONF_TIBBER_TOKEN): str,
                    vol.Required(CONF_WEATHER_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="weather")
                    ),
                    vol.Optional(CONF_INDOOR_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Optional(CONF_OUTDOOR_TEMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                    ),
                    vol.Optional(CONF_HEAT_PUMP_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="climate")
                    ),
                    vol.Optional(CONF_HEAT_PUMP_SWITCH_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="switch")
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "tibber_info": "Get your token from https://developer.tibber.com",
            },
        )

    async def async_step_temperature(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle temperature configuration step."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_thermal()

        return self.async_show_form(
            step_id="temperature",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TARGET_TEMP, default=DEFAULT_TARGET_TEMP
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=15, max=28, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=14, max=25, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=18, max=28, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_COMFORT_TEMP_DAY, default=DEFAULT_COMFORT_TEMP_DAY
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=16, max=26, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_COMFORT_TEMP_NIGHT, default=DEFAULT_COMFORT_TEMP_NIGHT
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=15, max=24, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_DAY_START_HOUR, default=DEFAULT_DAY_START_HOUR
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=12, step=1, mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_DAY_END_HOUR, default=DEFAULT_DAY_END_HOUR
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=18, max=23, step=1, mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
        )

    async def async_step_thermal(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle thermal model configuration step."""
        if user_input is not None:
            self._data.update(user_input)
            # Create the config entry
            return self.async_create_entry(
                title=self._data.get(CONF_NAME, "Heat Pump Optimizer"),
                data=self._data,
            )

        return self.async_show_form(
            step_id="thermal",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOUSE_THERMAL_MASS, default=DEFAULT_HOUSE_THERMAL_MASS
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=2, max=50, step=0.5, unit_of_measurement="kWh/°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_HOUSE_HEAT_LOSS_COEFFICIENT, default=DEFAULT_HOUSE_HEAT_LOSS_COEFFICIENT
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.05, max=1.0, step=0.01, unit_of_measurement="kW/°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_SLAB_THERMAL_MASS, default=DEFAULT_SLAB_THERMAL_MASS
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=30, step=0.5, unit_of_measurement="kWh/°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_SLAB_HEAT_TRANSFER, default=DEFAULT_SLAB_HEAT_TRANSFER
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.1, max=5.0, step=0.1, unit_of_measurement="kW/°C",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_HEAT_PUMP_COP_NOMINAL, default=DEFAULT_HEAT_PUMP_COP_NOMINAL
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1.5, max=6.0, step=0.1, mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_HEAT_PUMP_MAX_POWER, default=DEFAULT_HEAT_PUMP_MAX_POWER
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=20, step=0.5, unit_of_measurement="kW",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_HEAT_PUMP_MIN_POWER, default=DEFAULT_HEAT_PUMP_MIN_POWER
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0, max=10, step=0.5, unit_of_measurement="kW",
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_INTERVAL, default=DEFAULT_OPTIMIZATION_INTERVAL
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10, max=120, step=5, unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_PRICE_WEIGHT, default=DEFAULT_PRICE_WEIGHT
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.1, max=10, step=0.1, mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_COMFORT_WEIGHT, default=DEFAULT_COMFORT_WEIGHT
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.1, max=20, step=0.1, mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> HeatPumpOptimizerOptionsFlow:
        """Get the options flow for this handler."""
        return HeatPumpOptimizerOptionsFlow(config_entry)


class HeatPumpOptimizerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Heat Pump Optimizer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TARGET_TEMP,
                        default=current.get(CONF_TARGET_TEMP, DEFAULT_TARGET_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=15, max=28, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_MIN_TEMP,
                        default=current.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=14, max=25, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_MAX_TEMP,
                        default=current.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=18, max=28, step=0.5, unit_of_measurement="°C",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_PRICE_WEIGHT,
                        default=current.get(CONF_PRICE_WEIGHT, DEFAULT_PRICE_WEIGHT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.1, max=10, step=0.1, mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_COMFORT_WEIGHT,
                        default=current.get(CONF_COMFORT_WEIGHT, DEFAULT_COMFORT_WEIGHT),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=0.1, max=20, step=0.1, mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_OPTIMIZATION_INTERVAL,
                        default=current.get(CONF_OPTIMIZATION_INTERVAL, DEFAULT_OPTIMIZATION_INTERVAL),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10, max=120, step=5, unit_of_measurement="min",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                }
            ),
        )
