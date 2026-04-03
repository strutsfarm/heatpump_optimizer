"""Heat Pump Cost Optimizer integration for Home Assistant.

This integration optimizes heat pump operation to minimize electricity costs
using Model Predictive Control (MPC). It integrates with Tibber for electricity
prices and Home Assistant weather entities for temperature forecasts.

The optimization accounts for:
- Thermal mass of slab floor heating (slow response)
- Weather-dependent heat loss
- COP variation with outdoor temperature
- Pre-heating before expensive periods
- Temperature setback during expensive periods
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_RUN_OPTIMIZATION,
    SERVICE_SET_MODE,
    SERVICE_SET_THERMAL_PARAMS,
    CONF_TIBBER_TOKEN,
    CONF_WEATHER_ENTITY,
    MODE_AUTO,
    MODE_COMFORT,
    MODE_ECONOMY,
    MODE_OFF,
    MODE_BOOST,
)
from .coordinator import HeatPumpOptimizerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORM_LIST = [Platform.SENSOR, Platform.CLIMATE, Platform.SWITCH]

SERVICE_SCHEMA_RUN_OPTIMIZATION = vol.Schema({})

SERVICE_SCHEMA_SET_MODE = vol.Schema(
    {
        vol.Required("mode"): vol.In(
            [MODE_AUTO, MODE_COMFORT, MODE_ECONOMY, MODE_OFF, MODE_BOOST]
        ),
    }
)

SERVICE_SCHEMA_SET_THERMAL_PARAMS = vol.Schema(
    {
        vol.Optional("house_thermal_mass"): vol.Coerce(float),
        vol.Optional("house_heat_loss_coefficient"): vol.Coerce(float),
        vol.Optional("slab_thermal_mass"): vol.Coerce(float),
        vol.Optional("slab_heat_transfer"): vol.Coerce(float),
        vol.Optional("heat_pump_cop_nominal"): vol.Coerce(float),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Heat Pump Optimizer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    coordinator = HeatPumpOptimizerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORM_LIST)

    # Register services
    async def handle_run_optimization(call: ServiceCall) -> None:
        """Handle the run_optimization service call."""
        _LOGGER.info("Manual optimization triggered via service call")
        for entry_id, coord in hass.data[DOMAIN].items():
            if isinstance(coord, HeatPumpOptimizerCoordinator):
                await coord.async_run_optimization()

    async def handle_set_mode(call: ServiceCall) -> None:
        """Handle the set_mode service call."""
        mode = call.data["mode"]
        _LOGGER.info("Setting optimizer mode to: %s", mode)
        for entry_id, coord in hass.data[DOMAIN].items():
            if isinstance(coord, HeatPumpOptimizerCoordinator):
                await coord.async_set_mode(mode)

    async def handle_set_thermal_params(call: ServiceCall) -> None:
        """Handle the set_thermal_parameters service call."""
        params = dict(call.data)
        _LOGGER.info("Updating thermal parameters: %s", params)
        for entry_id, coord in hass.data[DOMAIN].items():
            if isinstance(coord, HeatPumpOptimizerCoordinator):
                await coord.async_update_thermal_params(params)

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_OPTIMIZATION,
        handle_run_optimization,
        schema=SERVICE_SCHEMA_RUN_OPTIMIZATION,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MODE,
        handle_set_mode,
        schema=SERVICE_SCHEMA_SET_MODE,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_THERMAL_PARAMS,
        handle_set_thermal_params,
        schema=SERVICE_SCHEMA_SET_THERMAL_PARAMS,
    )

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORM_LIST)

    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

    # Remove services if no more entries
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_RUN_OPTIMIZATION)
        hass.services.async_remove(DOMAIN, SERVICE_SET_MODE)
        hass.services.async_remove(DOMAIN, SERVICE_SET_THERMAL_PARAMS)

    return unload_ok
