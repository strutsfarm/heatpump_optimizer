# Heat Pump Cost Optimizer for Home Assistant

A custom Home Assistant integration that uses **Model Predictive Control (MPC)** to optimize heat pump operation and minimize electricity costs, while maintaining indoor comfort. Integrates with **Tibber** for dynamic electricity prices and Home Assistant weather entities for temperature and solar forecasts.

## Features

- **MPC-based optimization** — plans heat pump operation over a 24-hour rolling horizon
- **Two-zone thermal model** — separately models upper floor (radiators) and lower floor (slab floor heating)
- **Solar heat gain calculation** — accounts for passive solar gains through windows
- **Buffer tank dynamics** — models the heat pump buffer tank coupling both heating circuits
- **Tibber integration** — uses real-time and day-ahead electricity prices
- **Weather-aware** — wind chill, rain cooling, and solar radiation effects
- **COP modeling** — adjusts for outdoor temperature–dependent heat pump efficiency
- **Real sensor feedback** — uses floor heating return temperature for slab state estimation
- **Multiple operation modes** — Auto, Comfort, Economy, Boost, Off
- **Rich sensor entities** — 23 sensors including per-zone temperatures, solar gain, and schedule
- **Climate entity** — virtual thermostat with full HA climate integration
- **Service calls** — manual optimization, mode changes, runtime parameter tuning

## How It Works

### Two-Zone Thermal Model

The house is modeled as two thermal zones served by a single air-to-water heat pump with a buffer tank:

```
                    ┌─────────────────────────┐
                    │    Heat Pump (COP)       │
                    └────────┬────────────────┘
                             │ Q_hp
                    ┌────────▼────────────────┐
                    │   Buffer Tank (35L)      │
                    └──┬──────────────────┬───┘
           Q_rad (40%) │                  │ Q_floor (60%)
                       │                  │
        ┌──────────────▼───┐   ┌─────────▼────────────┐
        │  Zone 1: Upper   │   │  Zone 2: Lower Floor  │
        │  Floor (Radiator)│   │  (Slab Floor Heating)  │
        │  Low thermal mass│   │  High thermal mass     │
        │  Fast response   │   │  Slow response         │
        └──────┬───────────┘   └──────┬────────────────┘
               │  Q_inter (open       │
               │◄─layout heat─────────┤
               │   transfer)          │
               │                      │
          ┌────▼──────────────────────▼───┐
          │   Outdoor environment          │
          │   (Q_loss_upper + Q_loss_lower)│
          └───────────────────────────────┘
```

**Zone 1 — Upper Floor (Radiators):**
- Low thermal mass (~3 kWh/°C) — responds quickly to heating changes
- Heated by radiators drawing from the buffer tank
- Radiators provide rapid temperature adjustment

**Zone 2 — Lower Floor (Slab Heating):**
- High thermal mass (~8 kWh/°C) — stores large amounts of heat
- Heated by floor heating pipes embedded in the concrete slab
- Ideal for pre-heating during low-price periods

**Inter-Zone Heat Transfer:**
- Open layout allows warm air to circulate between floors
- Modeled as `Q_inter = k_inter × (T_lower − T_upper)`

**Buffer Tank:**
- 35L buffer tank couples the heat pump to both heating circuits
- Small thermal mass (~0.04 kWh/°C) but important for system dynamics
- Heat pump heats the buffer; buffer supplies both circuits

### Solar Heat Gain

The optimizer accounts for passive solar heat gains through windows:

```
Q_solar = solar_radiation × window_area × orientation_factor × SHGC / 1000
```

Where:
- `solar_radiation` — from weather forecast or dedicated sensor (W/m²)
- `window_area` — total glazing area (m²)
- `orientation_factor` — correction for window orientation (1.0 = fully south-facing)
- `SHGC` — Solar Heat Gain Coefficient of the glazing (typically 0.3–0.8)

Solar gains are split between zones based on the `solar_upper_fraction` parameter (default: 40% upper, 60% lower for typical open-plan houses with south-facing lower floor).

### Floor Return Temperature Feedback

If a floor heating return temperature sensor is configured:
- The optimizer uses the actual return temperature as feedback for slab thermal state
- More accurate than pure model-based estimation
- Merged with model prediction: 70% sensor, 30% model for noise smoothing

### Optimization Algorithm

The MPC solves at each interval:

```
minimize   Σ price[k] × P_el[k] × dt                         (electricity cost)
         + comfort_weight × Σ penalty(T_upper, T_lower)       (comfort violations)
         + 0.05 × comfort_weight × Σ (T - T_comfort)²         (comfort tracking)
         + 0.01 × Σ ΔP²                                       (smoothness)

subject to  P_min ≤ P_el[k] ≤ P_max
            Two-zone thermal dynamics
```

The solver (L-BFGS-B) finds the optimal power schedule for 24 hours at 15-minute resolution. Both zone temperatures are penalized for deviating from comfort bounds.

### Backward Compatibility

The component automatically falls back to the original single-zone model when two-zone parameters are not configured. All existing functionality is preserved.

## Installation

### HACS (Recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/your-username/ha_heatpump_optimizer` as category: Integration
3. Install "Heat Pump Cost Optimizer"
4. Restart Home Assistant

### Manual

1. Copy `custom_components/heatpump_optimizer/` to your `config/custom_components/`
2. Restart Home Assistant

## Configuration

The integration is configured through the Home Assistant UI in 4 steps:

### Step 1: API & Entities

| Parameter | Description |
|---|---|
| Tibber API token | Get from https://developer.tibber.com |
| Weather entity | Your weather integration (e.g., `weather.home`) |
| Indoor temp sensor | Optional: temperature sensor for main living area |
| Outdoor temp sensor | Optional: outdoor temperature sensor |
| Heat pump entity | Optional: climate entity for heat pump control |
| Heat pump switch | Optional: switch entity for on/off control |
| **Solar radiation sensor** | Optional: sensor providing W/m² (e.g., from weather integration) |
| **Floor return temp sensor** | Optional: floor heating return temperature sensor |

### Step 2: Temperature Settings

| Parameter | Default | Description |
|---|---|---|
| Target temperature | 21.0°C | Desired indoor temperature |
| Min temperature | 19.0°C | Lowest acceptable temperature |
| Max temperature | 23.0°C | Highest acceptable temperature |
| Comfort temp (day) | 21.0°C | Target during daytime |
| Comfort temp (night) | 19.5°C | Target during nighttime |
| Day start hour | 7 | When "day" begins |
| Day end hour | 22 | When "day" ends |

### Step 3: Thermal Model & Optimization

| Parameter | Default | Description |
|---|---|---|
| House thermal mass | 10.0 kWh/°C | Overall thermal mass (single-zone fallback) |
| Heat loss coefficient | 0.15 kW/°C | Envelope heat loss rate |
| Slab thermal mass | 5.0 kWh/°C | Concrete slab thermal mass |
| Slab heat transfer | 0.8 kW/°C | Slab-to-room conductance |
| HP nominal COP | 3.5 | COP at 7°C outdoor |
| HP max power | 5.0 kW | Maximum electrical input |
| HP min power | 1.0 kW | Minimum electrical input |
| Optimization interval | 30 min | How often to re-optimize |
| Price weight | 1.0 | Cost sensitivity |
| Comfort weight | 5.0 | Comfort sensitivity |

### Step 4: Two-Zone & Solar (Optional)

| Parameter | Default | Description |
|---|---|---|
| Upper floor thermal mass | 3.0 kWh/°C | Radiator zone (lighter) |
| Lower floor thermal mass | 8.0 kWh/°C | Slab zone (heavier) |
| Upper floor heat loss | 0.08 kW/°C | Upper floor envelope loss |
| Lower floor heat loss | 0.07 kW/°C | Lower floor envelope loss |
| Inter-zone transfer | 0.5 kW/°C | Heat transfer between floors |
| Radiator power fraction | 0.4 | Share of HP output to radiators (0–1) |
| Upper floor area ratio | 0.5 | Upper floor share of total area |
| Buffer tank volume | 35 L | Buffer tank size |
| Window area | 10.0 m² | Total glazing area |
| Orientation factor | 0.7 | Window solar orientation (1.0=full south) |
| SHGC | 0.7 | Solar heat gain coefficient |

## Finding the Right Sensors

### Solar Radiation Sensor

Solar radiation data can come from:
- **Weather integration**: Some weather integrations expose `solar_irradiance` in their forecast
- **Dedicated sensor**: e.g., a solar radiation meter connected via ESP/Zigbee
- **OpenWeatherMap**: The OpenWeatherMap integration can provide solar radiation data
- **Met.no**: The Met.no integration may include solar data in forecasts

If no sensor is configured, solar gains are set to zero (conservative estimate).

### Floor Heating Return Temperature Sensor

The floor heating return temperature is the temperature of water returning from the floor heating circuit. It's a proxy for average slab temperature. You can find this sensor from:
- **Heat pump integration**: Many heat pump integrations expose return water temperature
- **Add-on temperature sensor**: A temperature probe clamped to the return pipe
- **Underfloor heating controller**: Some controllers report return temperature

Example entity IDs:
- `sensor.heat_pump_return_temperature`
- `sensor.floor_heating_return_temp`
- `sensor.nibe_return_line_temperature`

### Configuring Window Parameters

**Window area**: Measure or estimate the total glazing area of windows that receive significant solar radiation. Include sliding doors. A typical Scandinavian house might have 8–15 m² of glazing.

**Orientation factor**: This accounts for the mix of window orientations:
- 1.0 = All windows face south (maximum solar gain)
- 0.7 = Mostly south-facing with some east/west (typical)
- 0.5 = Evenly distributed (all directions)
- 0.3 = Mostly north-facing (minimal solar gain)

**SHGC (Solar Heat Gain Coefficient)**: Depends on glazing type:
- Single clear glass: ~0.8
- Double clear glass: ~0.7
- Double low-e: ~0.5–0.65
- Triple low-e: ~0.3–0.5

## Entities Created

### Sensors (23 total)

| Entity | Description |
|---|---|
| `sensor.optimization_mode` | Current mode (auto/comfort/economy/boost/off) |
| `sensor.optimization_status` | Solver status and solve time |
| `sensor.predicted_savings` | Predicted savings vs baseline (SEK) |
| `sensor.savings_percentage` | Savings as percentage |
| `sensor.predicted_cost` | Predicted optimized cost |
| `sensor.baseline_cost` | Cost without optimization |
| `sensor.current_electricity_price` | Current Tibber price |
| `sensor.optimal_setpoint` | Current recommended setpoint |
| `sensor.recommended_power` | Current recommended power (kW) |
| `sensor.estimated_cop` | COP at current outdoor temp |
| `sensor.indoor_temperature_optimizer` | Indoor temp used by optimizer |
| `sensor.outdoor_temperature_optimizer` | Outdoor temp used by optimizer |
| `sensor.slab_temperature_estimated` | Estimated slab temperature |
| `sensor.next_optimization` | Next optimization timestamp |
| `sensor.last_optimization` | Last optimization timestamp |
| `sensor.heat_pump_action` | Current action with attributes |
| `sensor.optimization_schedule` | Full 24h schedule as attributes |
| `sensor.upper_floor_temperature` | **Upper floor (radiator zone) temp** |
| `sensor.lower_floor_temperature` | **Lower floor (slab zone) temp** |
| `sensor.floor_heating_return_temperature` | **Real floor return temp** |
| `sensor.solar_radiation_optimizer` | **Solar radiation (W/m²)** |
| `sensor.solar_heat_gain` | **Current solar gain (kW)** |
| `sensor.buffer_tank_temperature_model` | **Modeled buffer tank temp** |

### Climate Entity

| Entity | Description |
|---|---|
| `climate.heat_pump_optimizer` | Virtual thermostat with HVAC modes + presets. Shows zone temperatures in attributes. |

### Switch

| Entity | Description |
|---|---|
| `switch.optimizer_active` | Quick toggle for optimizer on/off |

## Services

### `heatpump_optimizer.run_optimization`

Manually trigger optimization:
```yaml
service: heatpump_optimizer.run_optimization
```

### `heatpump_optimizer.set_mode`

Set operating mode:
```yaml
service: heatpump_optimizer.set_mode
data:
  mode: auto  # auto, comfort, economy, boost, off
```

### `heatpump_optimizer.set_thermal_parameters`

Tune thermal model at runtime (supports all parameters including two-zone):
```yaml
service: heatpump_optimizer.set_thermal_parameters
data:
  house_thermal_mass: 12.0
  heat_pump_cop_nominal: 3.8
  radiator_power_fraction: 0.45
  window_area: 12.0
  solar_heat_gain_coefficient: 0.65
  inter_zone_heat_transfer: 0.6
```

## Automation Examples

### Pre-heat lower floor before expensive period

```yaml
automation:
  - alias: "Pre-heat slab before price peak"
    trigger:
      - platform: numeric_state
        entity_id: sensor.heat_pump_action
        attribute: price
        above: 2.0
    condition:
      - condition: numeric_state
        entity_id: sensor.current_electricity_price
        below: 1.0
    action:
      - service: heatpump_optimizer.set_mode
        data:
          mode: boost
      - delay: "01:00:00"
      - service: heatpump_optimizer.set_mode
        data:
          mode: auto
```

### Economy mode when away

```yaml
automation:
  - alias: "Economy when nobody home"
    trigger:
      - platform: state
        entity_id: group.family
        to: "not_home"
    action:
      - service: heatpump_optimizer.set_mode
        data:
          mode: economy
```

## Troubleshooting

### Temperature swings

If temperatures swing too much, increase `comfort_weight` (options flow) or decrease `price_weight`. For the two-zone model, check that `inter_zone_heat_transfer` matches your layout (higher for open plan, lower for closed rooms).

### Solar over-heating

If the model overestimates solar gain, reduce `window_area`, `orientation_factor`, or `SHGC`. You can adjust via the options flow or the `set_thermal_parameters` service.

### Floor heating slow response

This is expected — slab floor heating has very high thermal mass. The optimizer pre-heats the slab during cheap periods. If the lower floor is too cold, check that `radiator_power_fraction` isn't too high (leaving too little heat for the floor circuit).

### Debug logging

```yaml
logger:
  default: info
  logs:
    custom_components.heatpump_optimizer: debug
```

## Architecture

```
custom_components/heatpump_optimizer/
├── __init__.py          # Integration setup and service registration
├── config_flow.py       # 4-step UI configuration flow
├── const.py             # Constants and defaults (single + two-zone)
├── coordinator.py       # Data fetching, optimization scheduling, action application
├── thermal_model.py     # Two-zone thermal model with buffer tank and solar gains
├── optimizer.py         # MPC optimizer (L-BFGS-B) for both zone models
├── sensor.py            # 23 sensor entities including zone and solar sensors
├── climate.py           # Virtual climate entity with zone attributes
├── switch.py            # Optimizer enable/disable switch
├── services.yaml        # Service definitions
├── strings.json         # Default UI strings
├── translations/
│   ├── en.json          # English translations
│   └── sv.json          # Swedish translations
└── manifest.json        # Integration metadata
```

## Requirements

- Home Assistant 2024.1.0 or later
- Tibber account with API token
- Weather integration (e.g., Met.no, OpenWeatherMap)
- Python packages: `numpy`, `scipy` (installed automatically)

## Contributing

Contributions are welcome! Please open an issue or pull request.

## License

MIT License

## Acknowledgments

- [Tibber](https://tibber.com) for the electricity price API
- [Home Assistant](https://www.home-assistant.io/) community
- Inspired by research on MPC for building energy optimization
