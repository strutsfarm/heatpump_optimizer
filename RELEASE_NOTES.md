# Heat Pump Cost Optimizer — Release Notes

## v2.1.0
**Release date:** 2026-04-23

### Summary
This release improves Domestic Hot Water (DHW) optimization to prioritize cost savings while maintaining safe hot water availability.

### Highlights
- **Configured DHW minimum temperature is now actively enforced as the true floor** in optimization logic.
- **Predictive DHW pre-heating** now uses forecasted usage windows and estimated lead-time so the tank can coast near minimum between peaks.
- **Price-aware DHW control** reduces heating in expensive periods when no near-term hot water usage is predicted.
- **Learning usage patterns over time** from observed DHW temperature drops, persisted across restarts.
- **Post-install editability improvements** in the options flow for comfort/day-night schedule and DHW parameters.
- **Branding update**: added integration icon (`icon.png`) for HACS/repository branding.

## v2.0.1
**Release date:** 2026-04-21

### Summary
This patch release fixes a critical control logic issue that could prevent Domestic Hot Water (DHW) reheating when space heating demand was low or zero.

### Fixed bug
- **Critical ON/OFF control fix:**
  - The heat pump ON decision now correctly considers **both**:
    - space heating demand, and
    - DHW demand.
  - Previously, ON/OFF logic only evaluated space heating demand, which could keep the heat pump OFF even when DHW needed heating.

### Improvements and changes
- Updated ON/OFF schedule generation to use combined demand logic (`space OR dhw`) so the heat pump can activate for DHW-only demand periods.
- Added enhanced debug logging for optimizer decision-making, including:
  - per-step space heating power,
  - per-step DHW power,
  - threshold comparisons,
  - explicit decision reason tags (for example: `space_only`, `dhw_only`, `space_and_dhw`).
- Added clearer first-step decision summary logging to simplify troubleshooting during live operation.

### User impact and benefits
- Prevents missed DHW reheating cycles when there is no immediate space heating demand.
- Improves comfort and reliability by ensuring DHW demand can independently trigger heat pump operation.
- Makes behavior easier to diagnose with richer decision logs.
- Reduces risk of confusion where optimization output indicated DHW demand but physical heat pump stayed OFF.

### Upgrade instructions (HACS)
1. Open **HACS → Integrations**.
2. Find **Heat Pump Cost Optimizer**.
3. Click **Update** and install **v2.0.1**.
4. Restart Home Assistant (recommended after integration updates).
5. Verify operation:
   - Confirm integration version shows **2.0.1**.
   - Check logs at debug level if needed to validate combined ON/OFF decisions for space heating and DHW.

### Notes
- This is a backward-compatible patch release focused on control correctness and observability.
- No configuration migration is required.
