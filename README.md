# 🔥 Heat Pump Cost Optimizer for Home Assistant

A custom Home Assistant integration that optimizes air-to-water heat pump operation to **minimize electricity costs** using **Model Predictive Control (MPC)**. Designed for houses with **slab floor heating** (large thermal mass) and **Tibber** electricity pricing.

## ✨ Features

- **Smart Cost Optimization**: Uses MPC to find the optimal heating schedule over a 24-hour horizon
- **Tibber Integration**: Fetches real-time and forecast electricity prices (15-minute resolution)
- **Weather-Aware**: Uses weather forecasts to predict heat loss (temperature, wind, rain)
- **Thermal Mass Exploitation**: Pre-heats during cheap periods, allows temperature drop during expensive periods
- **COP Modeling**: Accounts for heat pump efficiency variation with outdoor temperature
- **Slab Floor Modeling**: Two-node thermal model capturing the slow dynamics of concrete slab heating
- **Multiple Modes**: Auto (full optimization), Comfort, Economy, Boost, and Off
- **Rich Sensors**: 17 sensor entities for monitoring optimization performance
- **Climate Entity**: Virtual thermostat with preset mode support
- **Service Calls**: Manually trigger optimization, change modes, update thermal parameters
- **Swedish Translation**: Full Swedish (sv) language support included

## 📊 How It Works

### Thermal Model

The integration models your house as a two-node thermal network:

```
                    ┌─────────────────┐
    Heat Pump ──►   │   Concrete Slab  │  ──► Slab-to-Room ──► ┌────────────┐
    (Q_hp)          │   (T_slab)       │      Heat Transfer     │  Room Air   │ ──► Heat Loss
                    │   High thermal   │      (k_slab)          │  (T_room)   │     to outside
                    │   mass           │                        │  + furniture │     (U × ΔT)
                    └─────────────────┘                        └────────────┘
```

**Key thermal dynamics:**
- `C_room × dT_room/dt = k_slab × (T_slab - T_room) - U_eff × (T_room - T_outdoor) + Q_internal`
- `C_slab × dT_slab/dt = COP × P_electrical - k_slab × (T_slab - T_room)`

### Optimization Algorithm

The optimizer solves the following problem every 30 minutes:

```
minimize    Σ price[k] × P_el[k] × Δt                    (electricity cost)
          + comfort_weight × Σ penalty(T_room[k])          (comfort violation)
          + smoothness × Σ (P[k+1] - P[k])²               (avoid cycling)

subject to  T_min ≤ T_room[k] ≤ T_max                     (soft constraints)
            P_min ≤ P_el[k] ≤ P_max                        (power limits)
            Thermal dynamics model                          (physics)
```

The solver uses **L-BFGS-B** (scipy) which efficiently handles box-constrained optimization.

### Pre-heating Strategy

When the optimizer detects an upcoming expensive period:
1. It increases heating power during the preceding cheap period
2. The slab floor stores thermal energy (due to high thermal mass)
3. During the expensive period, heating power is reduced
4. The slab slowly releases stored heat, maintaining comfort

### COP Variation

The heat pump COP is modeled as a function of outdoor temperature:
```
COP ≈ COP_nominal × max(0.3, 1 + 0.025 × (T_outdoor - 7°C))
```
This means the optimizer also prefers heating when it's warmer outside (higher COP).

## 🚀 Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click "Integrations" → "⋮" (three dots) → "Custom repositories"
3. Add this repository URL and select "Integration" as the category
4. Click "Download" to install
5. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/heatpump_optimizer` folder to your Home Assistant's `custom_components` directory:

```bash
# From your Home Assistant config directory:
mkdir -p custom_components
cp -r /path/to/ha_heatpump_optimizer/custom_components/heatpump_optimizer custom_components/
```

2. Restart Home Assistant

## ⚙️ Configuration

### Step 1: Add the Integration

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **"Heat Pump Cost Optimizer"**
3. Follow the setup wizard:

### Step 2: API & Entities

| Field | Description |
|-------|-------------|
| **Tibber API Token** | Get from [developer.tibber.com](https://developer.tibber.com) |
| **Weather Entity** | Your HA weather entity (e.g., `weather.home`) |
| **Indoor Temp Sensor** | (Optional) Temperature sensor inside the house |
| **Outdoor Temp Sensor** | (Optional) Temperature sensor outside |
| **Heat Pump Climate Entity** | (Optional) Your heat pump's climate entity |
| **Heat Pump Switch Entity** | (Optional) On/off switch for the heat pump |

### Step 3: Temperature Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Target Temperature | 21°C | Desired room temperature |
| Minimum Temperature | 19°C | Lowest acceptable temperature |
| Maximum Temperature | 23°C | Highest acceptable temperature |
| Daytime Comfort | 21°C | Target during day hours |
| Nighttime Comfort | 19.5°C | Target during night hours |
| Day Start | 07:00 | When "day" mode begins |
| Day End | 22:00 | When "night" mode begins |

### Step 4: Thermal Model Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| House Thermal Mass | 10 kWh/°C | Total thermal inertia of building |
| Heat Loss Coefficient | 0.15 kW/°C | Overall insulation quality |
| Slab Thermal Mass | 5 kWh/°C | Thermal inertia of concrete slab |
| Slab Heat Transfer | 0.8 kW/°C | Slab-to-room heat transfer rate |
| Nominal COP | 3.5 | Heat pump COP at 7°C outdoor |
| Max Power | 5 kW | Maximum electrical input |
| Min Power | 1 kW | Minimum electrical input |
| Optimization Interval | 30 min | How often to re-optimize |
| Price Weight | 1.0 | Priority of cost savings |
| Comfort Weight | 5.0 | Priority of temperature comfort |

### Estimating Your Thermal Parameters

**House Thermal Mass (kWh/°C):**
- Light construction (timber frame): 3-6 kWh/°C
- Medium construction: 6-12 kWh/°C
- Heavy construction (concrete/brick): 12-25 kWh/°C

**Heat Loss Coefficient (kW/°C):**
- Well-insulated new house: 0.08-0.15 kW/°C
- Average house: 0.15-0.25 kW/°C
- Older, less insulated: 0.25-0.50 kW/°C

**Quick test:** If your house drops 1°C in X hours when heating is off and it's 0°C outside at 20°C inside:
- `Heat Loss Coefficient ≈ House Thermal Mass × 1°C / (X hours × 20°C)`

**Slab Thermal Mass:**
- 10cm concrete slab: ~3 kWh/°C per 100m²
- 15cm concrete slab: ~5 kWh/°C per 100m²
- 20cm concrete slab: ~7 kWh/°C per 100m²

## 📱 Entities Created

### Sensors

| Entity | Description |
|--------|-------------|
| `sensor.optimization_mode` | Current mode (auto/comfort/economy/boost/off) |
| `sensor.optimization_status` | Solver status (optimal/suboptimal/failed) |
| `sensor.predicted_savings` | Predicted savings in SEK |
| `sensor.savings_percentage` | Savings as percentage |
| `sensor.predicted_cost` | Predicted optimized cost |
| `sensor.baseline_cost` | Cost without optimization |
| `sensor.current_electricity_price` | Current Tibber price |
| `sensor.optimal_setpoint` | Current recommended setpoint |
| `sensor.recommended_power` | Recommended power level |
| `sensor.estimated_cop` | Current estimated COP |
| `sensor.indoor_temperature_optimizer` | Indoor temp used by optimizer |
| `sensor.outdoor_temperature_optimizer` | Outdoor temp used by optimizer |
| `sensor.slab_temperature_estimated` | Estimated slab temperature |
| `sensor.next_optimization` | When next optimization runs |
| `sensor.last_optimization` | When last optimization ran |
| `sensor.heat_pump_action` | Current action (off/eco/normal/pre_heat/boost) |
| `sensor.optimization_schedule` | Full schedule as attributes |

### Climate

| Entity | Description |
|--------|-------------|
| `climate.heat_pump_optimizer` | Virtual thermostat with preset modes |

### Switch

| Entity | Description |
|--------|-------------|
| `switch.optimizer_active` | Enable/disable the optimizer |

## 🔧 Services

### `heatpump_optimizer.run_optimization`
Manually trigger an optimization run.

```yaml
service: heatpump_optimizer.run_optimization
```

### `heatpump_optimizer.set_mode`
Set the optimizer mode.

```yaml
service: heatpump_optimizer.set_mode
data:
  mode: auto  # auto, comfort, economy, boost, off
```

### `heatpump_optimizer.set_thermal_parameters`
Update thermal model parameters at runtime.

```yaml
service: heatpump_optimizer.set_thermal_parameters
data:
  house_thermal_mass: 12.0
  house_heat_loss_coefficient: 0.12
  slab_thermal_mass: 6.0
  slab_heat_transfer: 0.9
  heat_pump_cop_nominal: 3.8
```

## 📈 Automation Examples

### Boost before guests arrive
```yaml
automation:
  - alias: "Pre-heat before guests"
    trigger:
      - platform: time
        at: "15:00"
    condition:
      - condition: state
        entity_id: input_boolean.guests_arriving
        state: "on"
    action:
      - service: heatpump_optimizer.set_mode
        data:
          mode: boost
      - delay: "02:00:00"
      - service: heatpump_optimizer.set_mode
        data:
          mode: auto
```

### Switch to economy when away
```yaml
automation:
  - alias: "Economy mode when away"
    trigger:
      - platform: state
        entity_id: group.family
        to: "not_home"
        for: "00:30:00"
    action:
      - service: heatpump_optimizer.set_mode
        data:
          mode: economy
  
  - alias: "Auto mode when home"
    trigger:
      - platform: state
        entity_id: group.family
        to: "home"
    action:
      - service: heatpump_optimizer.set_mode
        data:
          mode: auto
```

### Notification on high savings
```yaml
automation:
  - alias: "Notify on optimization savings"
    trigger:
      - platform: numeric_state
        entity_id: sensor.heatpump_optimizer_savings_percentage
        above: 20
    action:
      - service: notify.mobile
        data:
          title: "💰 Heat Pump Savings"
          message: >
            Optimizer saving {{ states('sensor.heatpump_optimizer_savings_percentage') }}%
            ({{ states('sensor.heatpump_optimizer_predicted_savings') }} SEK)
            over the next 24 hours!
```

## 🐛 Troubleshooting

### Common Issues

**"Invalid Tibber API token"**
- Get a valid token at [developer.tibber.com](https://developer.tibber.com)
- Make sure you're using the personal access token, not the OAuth token
- Check that your Tibber subscription is active

**"Not enough price data"**
- Tibber tomorrow prices are typically available after 13:00
- The optimizer needs at least 4 time steps (1 hour) of price data
- Check the `sensor.optimization_status` entity for details

**"Optimization failed"**
- Check Home Assistant logs for detailed error messages
- Verify that numpy and scipy are installed (they should be auto-installed)
- Try adjusting thermal parameters to more conservative values

**Temperature swings too large**
- Increase the `comfort_weight` parameter (e.g., from 5.0 to 10.0)
- Narrow the min/max temperature range
- The optimizer needs a few days to learn your house's thermal behavior

**Temperature swings too small (not saving enough)**
- Decrease the `comfort_weight` (e.g., from 5.0 to 2.0)
- Increase the `price_weight` (e.g., from 1.0 to 2.0)
- Widen the min/max temperature range

**Heat pump not responding**
- Verify the climate/switch entity IDs are correct
- Check that the entities support the `set_temperature` / `turn_on/off` services
- Some heat pumps need specific integration setup (e.g., Nibe, Thermia)

### Logs

Enable debug logging for detailed information:

```yaml
# In configuration.yaml
logger:
  logs:
    custom_components.heatpump_optimizer: debug
```

### Verifying the Thermal Model

1. Set mode to "off" and observe how quickly the house cools
2. Note the cooling rate (°C/hour) and outdoor temperature
3. Calculate: `Heat Loss Coefficient = Thermal Mass × Cooling Rate / (T_indoor - T_outdoor)`
4. Update parameters via the service call or options flow

## 🏗️ Architecture

```
custom_components/heatpump_optimizer/
├── __init__.py              # Integration setup, service registration
├── manifest.json            # HA integration manifest
├── const.py                 # Constants and defaults
├── config_flow.py           # UI configuration wizard
├── coordinator.py           # Data coordinator (Tibber, weather, optimization)
├── thermal_model.py         # Two-node thermal model
├── optimizer.py             # MPC optimization engine
├── sensor.py                # 17 sensor entities
├── climate.py               # Climate entity with presets
├── switch.py                # Enable/disable switch
├── services.yaml            # Service definitions
├── strings.json             # UI strings
└── translations/
    ├── en.json              # English translations
    └── sv.json              # Swedish translations
```

## 📋 Requirements

- Home Assistant 2024.1 or newer
- Tibber subscription with API access
- A weather integration (e.g., Met.no, OpenWeatherMap)
- Python packages: `numpy>=1.24.0`, `scipy>=1.10.0` (auto-installed)

## 🤝 Contributing

Contributions are welcome! Areas where help is needed:
- Support for more electricity price providers (Nord Pool, Entso-E)
- Auto-learning of thermal parameters from historical data
- Support for multi-zone heating
- Integration with specific heat pump brands (Nibe, Thermia, IVT, etc.)
- Dashboard cards for visualization

## 📄 License

MIT License - see LICENSE file for details.

## 🙏 Acknowledgments

- [Tibber](https://tibber.com) for the electricity price API
- [Home Assistant](https://www.home-assistant.io) for the amazing platform
- [scipy](https://scipy.org) for the optimization solver
- The Home Assistant community for inspiration and testing
