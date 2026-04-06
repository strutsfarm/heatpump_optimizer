# Heat Pump Cost Optimizer for Home Assistant

A custom Home Assistant integration that uses **Model Predictive Control (MPC)** to optimize heat pump operation and minimize electricity costs, while maintaining indoor comfort and domestic hot water availability. Integrates with **Tibber** for dynamic electricity prices and Home Assistant weather entities for temperature, wind, rain, and solar forecasts.

## Features

- **True Predictive MPC** — uses FULL 24-hour weather forecast trajectories for anticipatory control
- **Solar Anticipation** — reduces pre-heating when sunny weather is forecasted (the sun will heat for free)
- **Wind/Rain Anticipation** — increases pre-heating before forecasted bad weather (higher heat loss coming)
- **Two-zone thermal model** — separately models upper floor (radiators) and lower floor (slab floor heating)
- **DHW (Domestic Hot Water) optimization** — coordinates hot water heating with space heating
- **Enhanced heat loss model** — wind speed increases convective loss, rain increases envelope U-value
- **Solar heat gain calculation** — accounts for passive solar gains through windows
- **Buffer tank dynamics** — models the heat pump buffer tank coupling both heating circuits
- **Tibber integration** — uses real-time and day-ahead electricity prices
- **COP modeling** — adjusts for outdoor temperature–dependent heat pump efficiency
- **Real sensor feedback** — uses floor heating return temperature for slab state estimation
- **Multiple operation modes** — Auto, Comfort, Economy, Boost, Off
- **Rich sensor entities** — 27 sensors including DHW, predictive insights, per-zone temperatures
- **Climate entity** — virtual thermostat with full HA climate integration
- **Service calls** — manual optimization, mode changes, runtime parameter tuning

## How the Predictive Optimization Works

### True Anticipatory Control (Not Just Reactive!)

The key differentiator of this optimizer is that it uses **forecasted weather data** to make decisions about **current** actions. This is what makes it true Model Predictive Control:

#### Solar Anticipation Strategy
```
Current time: 22:00 (night, cheap electricity)
Forecast: Tomorrow 10:00-16:00 → 400-600 W/m² solar radiation

Traditional optimizer: Pre-heat slab during cheap night electricity ✓
THIS optimizer: REDUCE slab pre-heating because solar will heat it for free! 💰

Result: Less overnight heating → sun heats the slab tomorrow → SAVINGS
```

The optimizer analyzes the solar radiation forecast for the next 6-12 hours. If significant solar gain is expected (>200 W/m²), it reduces slab pre-heating by up to 40% because the sun will provide free heat.

#### Wind/Rain Anticipation Strategy
```
Current time: 14:00 (afternoon, moderate price)
Forecast: Tonight 20:00-06:00 → 8-12 m/s wind + rain

Traditional optimizer: React to wind when it arrives (too late!)
THIS optimizer: INCREASE pre-heating NOW while electricity is cheaper! 🏠

Result: Thermal mass pre-charged → house stays warm through bad weather → COMFORT
```

The optimizer analyzes wind speed and precipitation forecasts. When bad weather is coming:
- **Wind effect**: Convective heat loss increases by `wind_sensitivity × wind_speed` (default 15% per m/s)
- **Rain effect**: Wet building envelope U-value increases by `rain_multiplier` (default 15%)
- Pre-heating is prioritized during cheap periods before the bad weather arrives

### DHW (Domestic Hot Water) Optimization

The optimizer coordinates DHW heating with space heating:

```
Heat Pump Capacity: 5 kW total
├── Space Heating: 0-5 kW (variable)
└── DHW Heating:   0-5 kW (variable)
    Total:         ≤ 5 kW (capacity constraint)
```

**Priority**: Space heating comfort > DHW minimum temperature > Cost optimization

- DHW is heated preferentially during cheap electricity periods
- DHW temperature must stay above minimum (default 45°C) at all times
- The optimizer models a time-of-day hot water draw pattern (morning/evening peaks)
- DHW tank thermal dynamics include standby losses and consumption draws

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
          │   Wind → ↑ convective loss     │
          │   Rain → ↑ envelope U-value    │
          └───────────────────────────────┘
```

### Enhanced Heat Loss Model

The heat loss model accounts for forecasted weather at EACH time step:

**Wind effect** (convective heat transfer):
```
U_effective = U_base × (1 + wind_sensitivity × wind_speed)
```
- Default: 15% increase per m/s wind speed
- Example: 5 m/s wind → 75% higher heat loss coefficient
- Uses FORECASTED wind speed at each future time step

**Rain effect** (wet building envelope):
```
U_effective = U_wind_adjusted × rain_multiplier (when raining)
```
- Default: 15% increase during rain (rain_multiplier = 1.15)
- Scales with precipitation intensity (light rain → partial multiplier)
- Uses FORECASTED precipitation at each future time step

### Solar Heat Gain

Solar radiation through windows reduces heating need:
```
Q_solar = solar_radiation × window_area × orientation_factor × SHGC / 1000
```

Solar gains are split between zones:
- Upper floor: 40% (default) — light reaches upper level
- Lower floor: 60% (default) — sun hits lower floor through large windows

### Floor Return Temperature Feedback

When a floor heating return temperature sensor is configured, the optimizer uses it to correct the slab temperature model:
```
T_slab_estimated = 0.7 × (T_return + 1°C) + 0.3 × T_slab_model
```

### Backward Compatibility

When two-zone parameters are not configured, the model falls back to single-zone operation. DHW optimization is only active when a DHW temperature sensor or DHW tank volume is configured.

## Configuration

### Step 1: API & Entity Selection
| Parameter | Description | Required |
|---|---|---|
| Tibber API token | Get from https://developer.tibber.com | Yes |
| Weather entity | HA weather entity for forecasts | Yes |
| Indoor temp sensor | Room temperature sensor | No |
| Outdoor temp sensor | Outdoor temperature sensor | No |
| Heat pump climate entity | To control the heat pump | No |
| Heat pump switch | On/off switch for heat pump | No |
| Solar radiation sensor | W/m² irradiance sensor | No |
| Floor return temp sensor | Floor heating return temp | No |
| DHW temp sensor | Hot water tank temperature | No |

### Step 2: Temperature Settings
| Parameter | Default | Range |
|---|---|---|
| Target temperature | 21.0°C | 15-28°C |
| Min temperature | 19.0°C | 14-25°C |
| Max temperature | 23.0°C | 18-28°C |
| Comfort temp (day) | 21.0°C | 16-26°C |
| Comfort temp (night) | 19.5°C | 15-24°C |
| Day starts | 07:00 | 0-12 |
| Day ends | 22:00 | 18-23 |

### Step 3: Thermal Model
| Parameter | Default | Unit |
|---|---|---|
| House thermal mass | 10.0 | kWh/°C |
| Heat loss coefficient | 0.15 | kW/°C |
| Slab thermal mass | 5.0 | kWh/°C |
| Slab heat transfer | 0.8 | kW/°C |
| HP nominal COP | 3.5 | - |
| HP max power | 5.0 | kW |
| HP min power | 1.0 | kW |

### Step 4: Two-Zone & Solar (Optional)
| Parameter | Default | Unit |
|---|---|---|
| Upper floor thermal mass | 3.0 | kWh/°C |
| Lower floor thermal mass | 8.0 | kWh/°C |
| Upper floor heat loss | 0.08 | kW/°C |
| Lower floor heat loss | 0.07 | kW/°C |
| Inter-zone transfer | 0.5 | kW/°C |
| Radiator power fraction | 0.4 | 0-1 |
| Buffer tank volume | 35 | L |
| Window area | 10 | m² |
| Solar orientation factor | 0.7 | 0-1 |
| SHGC | 0.7 | 0-1 |

### Step 5: DHW Configuration (Optional)
| Parameter | Default | Unit |
|---|---|---|
| DHW tank volume | 200 | L |
| DHW setpoint | 55 | °C |
| DHW minimum temperature | 45 | °C |
| Daily consumption | 150 | L/day |

### Step 6: Weather Sensitivity
| Parameter | Default | Description |
|---|---|---|
| Wind sensitivity | 0.15 | 15% heat loss increase per m/s wind |
| Rain multiplier | 1.15 | 15% heat loss increase when raining |

## Entities Created

### Sensors (27 total)
| Sensor | Description |
|---|---|
| Optimization Mode | Current mode (auto/comfort/economy/boost/off) |
| Optimization Status | Solver status (optimal/suboptimal/failed) |
| Predicted Savings | Cost savings vs. baseline (SEK) |
| Savings Percentage | Savings as percentage |
| Predicted Cost | Optimized 24h cost (SEK) |
| Baseline Cost | Non-optimized 24h cost (SEK) |
| Current Electricity Price | Current Tibber price (SEK/kWh) |
| Optimal Setpoint | Current recommended setpoint (°C) |
| Recommended Power | Current recommended power (kW) |
| Estimated COP | COP at current outdoor temp |
| Indoor Temperature | Current indoor temp (optimizer) |
| Outdoor Temperature | Current outdoor temp (optimizer) |
| Slab Temperature | Estimated slab temperature (°C) |
| Next Optimization | Timestamp of next optimization run |
| Last Optimization | Timestamp of last optimization run |
| Heat Pump Action | Current action (off/eco/normal/pre_heat/boost) |
| Optimization Schedule | Full 24h schedule (in attributes) |
| Upper Floor Temperature | Upper floor (radiator zone) temp |
| Lower Floor Temperature | Lower floor (slab zone) temp |
| Floor Heating Return Temp | Floor return sensor reading |
| Solar Radiation | Current solar radiation (W/m²) |
| Solar Heat Gain | Current solar gain (kW) |
| Buffer Tank Temperature | Modeled buffer tank temp |
| **DHW Temperature** | Current hot water temperature |
| **DHW Heating Schedule** | Planned DHW heating periods |
| **DHW Heating Cost** | Estimated DHW heating cost |
| **Predictive Insight** | Anticipatory control status |

### Climate Entity
- Virtual thermostat with HVAC modes and presets
- Attributes include zone temperatures, DHW status, and predictive optimization insights

### Switch Entity
- Enable/disable the optimizer

## Services

### `heatpump_optimizer.run_optimization`
Manually trigger a predictive optimization run.

### `heatpump_optimizer.set_mode`
Set operation mode: auto, comfort, economy, boost, off.

### `heatpump_optimizer.set_thermal_parameters`
Runtime parameter tuning:
```yaml
service: heatpump_optimizer.set_thermal_parameters
data:
  house_thermal_mass: 12.0
  wind_sensitivity_factor: 0.20
  rain_heat_loss_multiplier: 1.20
  dhw_setpoint: 55
  dhw_min_temperature: 45
  window_area: 15.0
  solar_heat_gain_coefficient: 0.65
```

## Troubleshooting

### Temperature swings between zones
- Adjust `inter_zone_heat_transfer` (higher for open layouts)

### Solar over-heating in summer
- Reduce `window_area` or `solar_heat_gain_coefficient`
- Consider seasonal shading effects

### Floor heating slow response
- This is expected — slab has high thermal mass
- The optimizer accounts for this by pre-heating during cheap periods

### DHW too cold / too often heated
- Increase `dhw_min_temperature` if water feels cold
- Decrease `dhw_daily_consumption` if tank stays warm enough
- The optimizer prioritizes DHW above cost — it should never drop below minimum

### Predictive optimization not working
- Check that your weather entity provides hourly forecasts
- Check the "Predictive Insight" sensor for forecast analysis
- Solar anticipation requires solar irradiance in weather data
- Wind/rain anticipation requires wind_speed and precipitation in forecasts

## Architecture

```
custom_components/heatpump_optimizer/
├── __init__.py          # Entry point, service registration
├── const.py             # Constants (incl. DHW + weather sensitivity)
├── config_flow.py       # UI config (6 steps: user, temp, thermal, zones, dhw, weather)
├── coordinator.py       # Data fetching, full 24h forecasts, DHW state
├── thermal_model.py     # Two-zone model + DHW tank + enhanced wind/rain loss
├── optimizer.py         # Predictive MPC with solar/wind/rain anticipation + DHW
├── sensor.py            # 27 sensors (incl. DHW + predictive insights)
├── climate.py           # Virtual climate entity with DHW status
├── switch.py            # Enable/disable switch
├── services.yaml        # Service definitions (incl. DHW + weather params)
├── strings.json         # UI strings
├── translations/
│   ├── en.json          # English translations
│   └── sv.json          # Swedish translations
└── manifest.json        # Integration manifest
```

## Requirements

- Home Assistant 2024.1.0 or newer
- Tibber account with API access
- Weather integration with hourly forecasts (recommended: Met.no or similar)
- Python packages: `numpy`, `scipy`

## Installation

### HACS (Recommended)
1. Add this repository to HACS as a custom repository
2. Install "Heat Pump Cost Optimizer"
3. Restart Home Assistant
4. Configure via Settings → Integrations → Add → Heat Pump Cost Optimizer

### Manual
1. Copy `custom_components/heatpump_optimizer` to your HA `custom_components` folder
2. Restart Home Assistant
3. Configure via the UI

## License

MIT
