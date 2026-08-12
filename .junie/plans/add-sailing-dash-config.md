---
sessionId: session-260811-135202-18xv
---

# Requirements

### Overview & Goals
Define a central configuration file (`ha/sailing-dash/config.yaml`) that controls installation, build, and deployment parameters for the sailing dashboard. The configuration file will allow users to:
1. Enable or disable specific sections and individual cards on the dashboard.
2. Set the measured history time window left of "Now" (in hours) for wind and wave charts.
3. Set the forecast time window right of "Now" (in days) for open-meteo REST forecast queries and wind/wave charts.
4. Interactively prompt and configure these parameters during setup via `install_wizard.sh --config`.

### Scope
- **In Scope:**
  - Creation of `ha/sailing-dash/config.yaml.template` and `config.yaml`.
  - Annotation of section cards in `src/yaml/dashboard/sections/*.yaml` with unique `id` identifiers.
  - Updating `helpers/build.py` to filter sections/cards and inject custom chart time windows (`history_hours`, `forecast_days`).
  - Adding `--config` CLI option to `install_wizard.sh` and an interactive configuration helper (`helpers/configure.py`).
  - Unit tests in `tests/test_sailing_dash.py` validating config parsing, card filtering, time window generation, and interactive helper functions.
  - Updating documentation in `README.md`, `INSTALLATION.md`, `SKILL.md`, and `CHANGELOG.md`.
- **Out of Scope:**
  - Changes to physical NMEA 2000 hardware PGN mappings (canonical virtual sensors `sensor.boat_*` remain unchanged).
  - Runtime UI config editors inside Home Assistant frontend.

### User Stories
- As a boat operator, I want to configure which cards appear on my sailing dashboard so that I can hide irrelevant cards or sections for my vessel setup.
- As a navigator, I want to adjust the history time window (e.g. 4 hours back) and forecast duration (e.g. 3 days ahead) in a single config file so that charts and open-meteo REST requests automatically adapt.
- As an installer, I want to run `./install_wizard.sh --config` so that I am guided through interactive CLI prompts to select active cards and chart time windows during initial installation.

### Functional Requirements
1. **Config File Format:** `ha/sailing-dash/config.yaml` using YAML syntax, with defaults provided in `config.yaml.template`.
2. **Card/Section Filtering:** `build.py` filters out any section where `enabled: false` and any card where `cards.<card_id>: false`.
3. **Chart Time Window Customization:**
   - `time_window.history_hours` sets `sensor.chart_time_window.attributes.history_hours` (default: `4`).
   - `time_window.forecast_days` sets `sensor.chart_time_window.attributes.forecast_hours` (`forecast_days * 24`, default: `3` days = `72` hours).
4. **Interactive Setup Wizard:** Passing `--config` to `./install_wizard.sh` launches `helpers/configure.py` (or `python3 helpers/build.py --configure`), allowing the installer to interactively review and customize time windows and card visibility before running `build.py`.
5. **Backward Compatibility:** If `config.yaml` is absent and `--config` is not passed, `build.py` falls back to default values from `config.yaml.template` so build and deploy remain fully functional out of the box.

# Technical Design

### Current Implementation
- `helpers/build.py` compiles `src/yaml/dashboard/sections/*.yaml` sequentially into `build/dashboard-sailing.yaml`.
- Chart time windows are hardcoded in `src/yaml/sensors/forecast.yaml` as `history_hours: 4` and `forecast_hours: 72`.
- `open_meteo.yaml` calculates REST `forecast_days` dynamically based on `sensor.chart_time_window.attributes.forecast_hours`.

### Key Decisions
- **Unified Config File (`ha/sailing-dash/config.yaml`):** Centralize build-time customizations in a single, user-editable YAML file.
- **Card-level `id` tagging:** Add `id` keys to card definitions in `src/yaml/dashboard/sections/*.yaml` to allow granular card-level visibility toggles alongside section-level toggles.
- **Days-to-Hours Conversion:** Accept `forecast_days` in days in `config.yaml` (as requested by user) and automatically convert to `forecast_hours = forecast_days * 24` in `build.py` when generating `sensor.chart_time_window` attributes.

### Proposed Changes
1. **New Config Template (`ha/sailing-dash/config.yaml.template`):**
```yaml

# Sailing Dashboard Build & Deployment Configuration

time_window:
  history_hours: 4
  forecast_days: 3

sections:
  sensors:
    enabled: true
    cards:
      stw_gauge: true
      depth_gauge: true
      sog_gauge: true
  position:
    enabled: true
    cards:
      hdg_compass: true
      cog_compass: true
      map: true
      latitude: true
      longitude: true
  conditions:
    enabled: true
    cards:
      windrose: true
      barometer_gauge: true
      barometer_trend: true
  wind:
    enabled: true
    cards:
      glance: true
      chart: true
  waves:
    enabled: true
    cards:
      glance: true
      chart: true
  forecast:
    enabled: true
    cards:
      windy_map: true
```

2. **`helpers/build.py` Updates:**
   - Implement `load_config()`: reads `config.yaml`, merges missing keys from defaults.
   - Update `build_dashboard()`: filters out sections/cards set to `false`. Removes temporary `id` tags from output YAML to keep live Lovelace config clean.
   - Update `build_sensors()`: overrides `sensor.chart_time_window` attributes (`history_hours` and `forecast_hours`) with config values.

3. **`install_wizard.sh` & `helpers/configure.py` Integration:**
   - Add `--config` argument parsing to `install_wizard.sh`.
   - Implement `helpers/configure.py`: terminal wizard that prompts the user for history hours, forecast days, and section/card enablement choices, saving the result to `ha/sailing-dash/config.yaml`.
   - Update Step 3 in `install_wizard.sh` to execute `helpers/configure.py` when `--config` is set prior to calling `build.py`.

### File Structure
- `ha/sailing-dash/config.yaml.template` (NEW)
- `ha/sailing-dash/config.yaml` (NEW, git-ignored or local default)
- `ha/sailing-dash/helpers/configure.py` (NEW, interactive CLI configuration wizard)
- `ha/sailing-dash/install_wizard.sh` (MODIFIED with `--config` flag support)
- `ha/sailing-dash/helpers/build.py` (MODIFIED)
- `ha/sailing-dash/src/yaml/dashboard/sections/*.yaml` (MODIFIED with `id` tags)
- `tests/test_sailing_dash.py` (MODIFIED with regression assertions)

# Testing

### Validation Approach
Verification will be performed via automated pytest tests and build script execution.

### Key Scenarios
1. **Default Build:** `build.py` without `config.yaml` uses default parameters (4h history, 3 days forecast, all cards enabled).
2. **Card Disabling:** Set `sections.sensors.cards.stw_gauge: false` and verify `sensor.boat_stw` gauge is omitted from `build/dashboard-sailing.yaml`.
3. **Section Disabling:** Set `sections.waves.enabled: false` and verify the entire Waves section is omitted from `build/dashboard-sailing.yaml`.
4. **Time Window Customization:** Set `history_hours: 6` and `forecast_days: 5` in `config.yaml`; verify `build/sensors-sailing.yaml` contains `history_hours: 6` and `forecast_hours: 120`.

### Test Changes
- Add `test_config_yaml_parsing_and_filtering()` to `tests/test_sailing_dash.py`.

# Delivery Steps

### ✓ Step 1: Create configuration schema and template for sailing-dash
Create `ha/sailing-dash/config.yaml.template` and tag dashboard section cards with `id` keys.

- Create `ha/sailing-dash/config.yaml.template` with default settings for `time_window` (`history_hours: 4`, `forecast_days: 3`) and section/card visibility toggles.
- Add explicit `id` attributes to individual cards in `src/yaml/dashboard/sections/*.yaml` (e.g., `stw_gauge`, `depth_gauge`, `sog_gauge`, `hdg_compass`, `cog_compass`, `map`, `latitude`, `longitude`, `windrose`, `barometer_gauge`, `barometer_trend`, `wind_glance`, `wind_chart`, `wave_glance`, `wave_chart`, `windy_map`).

### ✓ Step 2: Add interactive CLI configuration wizard and --config flag to install_wizard.sh
Implement `helpers/configure.py` and update `install_wizard.sh` to support interactive configuration via `--config`.

- Create `helpers/configure.py` to interactively prompt users for history hours, forecast days, and section/card enablement, saving selections to `ha/sailing-dash/config.yaml`.
- Update `install_wizard.sh` option parsing to accept `--config`.
- Integrate `helpers/configure.py` execution into `install_wizard.sh` prior to `build.py` when `--config` is specified.

### ✓ Step 3: Update build.py to parse config.yaml, filter sections/cards, and set time windows
Extend `helpers/build.py` to load `config.yaml`, filter dashboard elements, and inject chart time windows into sensors.

- Implement `load_config()` in `helpers/build.py` that parses `ha/sailing-dash/config.yaml` (falling back to `config.yaml.template` defaults if missing or partial).
- Update `build_dashboard()` to check section enablement (`enabled: false`) and card enablement (`cards.<card_id>: false`), stripping disabled items and temporary `id` keys before writing `build/dashboard-sailing.yaml`.
- Update `build_sensors()` in `helpers/build.py` to inject dynamic `history_hours` and `forecast_hours` (`forecast_days * 24`) attributes into `sensor.chart_time_window`.

### ✓ Step 4: Add regression tests and update documentation
Add pytest regression tests and document `config.yaml` and `./install_wizard.sh --config` usage.

- Add test cases in `tests/test_sailing_dash.py` for config parsing, card/section filtering, time window parameter generation, and `configure.py` functions.
- Document `config.yaml` structure and `./install_wizard.sh --config` options in `ha/sailing-dash/README.md`, `INSTALLATION.md`, `.agents/skills/nmea2000-setup/SKILL.md`, and `CHANGELOG.md`.