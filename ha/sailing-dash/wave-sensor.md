```yaml
rest:
  - resource_template: >-
      https://marine-api.open-meteo.com/v1/marine?latitude={{ states('sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_latitude') | float(42.43) }}&longitude={{ states('sensor.position_rapid_update_raymarine_display_1180407_pk_dbdf6a933ca2a0c28e21602200f43fa1_longitude') | float(18.60) }}&hourly=wave_height,wave_direction,wave_period,swell_wave_height&timezone=UTC
    scan_interval: 900
    sensor:
      - name: 'Wave Forecast REST'
        unique_id: wave_forecast_rest
        icon: mdi:waves
        value_template: "{{ value_json.hourly.wave_height | list | first | round(1) }}"
        unit_of_measurement: 'm'
        json_attributes:
          - hourly

template:
  - sensor:
      # Flattened open-meteo wave forecast — used by the apexcharts-card
      # "Wave History & Forecast" series (dashboard-sailing.yaml).
      - name: Wave Forecast Flat
        unique_id: wave_forecast_flat
        unit_of_measurement: 'm'
        icon: mdi:waves
        state: "{{ states('sensor.wave_forecast_rest') }}"
        attributes:
          forecast_time: "{{ state_attr('sensor.wave_forecast_rest', 'hourly')['time'] if state_attr('sensor.wave_forecast_rest', 'hourly') else [] }}"
          wave_height: "{{ state_attr('sensor.wave_forecast_rest', 'hourly')['wave_height'] if state_attr('sensor.wave_forecast_rest', 'hourly') else [] }}"
          wave_direction: "{{ state_attr('sensor.wave_forecast_rest', 'hourly')['wave_direction'] if state_attr('sensor.wave_forecast_rest', 'hourly') else [] }}"
          wave_period: "{{ state_attr('sensor.wave_forecast_rest', 'hourly')['wave_period'] if state_attr('sensor.wave_forecast_rest', 'hourly') else [] }}"

      # Next full forecast hour from open-meteo, exposed as a plain numeric
      # sensor for wave height.
      - name: Wave Height Next Hour
        unique_id: wave_height_next_hour
        unit_of_measurement: 'm'
        icon: mdi:waves
        state: >
          {% set times = state_attr('sensor.wave_forecast_flat', 'forecast_time') or [] %}
          {% set vals = state_attr('sensor.wave_forecast_flat', 'wave_height') or [] %}
          {% set ns = namespace(v = none) %}
          {% for t in times %}
            {% if ns.v is none and as_timestamp(t ~ '+00:00', 0) >= as_timestamp(now()) %}
              {% set ns.v = vals[loop.index0] %}
            {% endif %}
          {% endfor %}
          {{ (ns.v | float(0)) | round(1) if ns.v is not none else 'unknown' }}

      # Next full forecast hour from open-meteo, exposed as a plain numeric
      # sensor for wave period.
      - name: Wave Period Next Hour
        unique_id: wave_period_next_hour
        unit_of_measurement: 's'
        icon: mdi:waves
        state: >
          {% set times = state_attr('sensor.wave_forecast_flat', 'forecast_time') or [] %}
          {% set vals = state_attr('sensor.wave_forecast_flat', 'wave_period') or [] %}
          {% set ns = namespace(v = none) %}
          {% for t in times %}
            {% if ns.v is none and as_timestamp(t ~ '+00:00', 0) >= as_timestamp(now()) %}
              {% set ns.v = vals[loop.index0] %}
            {% endif %}
          {% endfor %}
          {{ (ns.v | float(0)) | round(1) if ns.v is not none else 'unknown' }}

# Entity list with units
- entity_id: sensor.wave_forecast_flat
  unit_of_measurement: m
- entity_id: sensor.wave_height_next_hour
  unit_of_measurement: m
- entity_id: sensor.wave_period_next_hour
  unit_of_measurement: s
```
