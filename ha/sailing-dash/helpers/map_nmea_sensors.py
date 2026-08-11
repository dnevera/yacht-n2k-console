#!/usr/bin/env python3
"""
NMEA 2000 Sensor Auto-Discovery Engine for Sailing Dashboard.

Scans Home Assistant entity registry (.storage/core.entity_registry or HA REST API)
for PGN-based NMEA2000 entities and maps them to canonical alias template definitions.
"""

import argparse
import json
import os
import sys
import urllib.request
import yaml

DEFAULT_FALLBACKS = {
    "stw": "sensor.speed_water_referenced",
    "depth": "sensor.water_depth",
    "wind_speed": "sensor.wind_speed",
    "wind_angle": "sensor.wind_angle",
    "cog": "sensor.cog",
    "sog": "sensor.sog",
    "latitude": "sensor.latitude",
    "longitude": "sensor.longitude",
    "pressure": "sensor.pressure",
    "heading": "sensor.vessel_heading",
    "variation": "sensor.magnetic_variation",
}


def find_entity_registry_path(config_dir):
    paths = [
        os.path.join(config_dir, ".storage", "core.entity_registry"),
        os.path.join(config_dir, "core.entity_registry"),
    ]
    for p in paths:
        if os.path.isfile(p):
            return p
    return None


def discover_from_registry_file(reg_path):
    if not reg_path or not os.path.isfile(reg_path):
        return {}

    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to parse entity registry {reg_path}: {e}")
        return {}

    entities = data.get("data", {}).get("entities", [])
    return match_entities([e.get("entity_id") for e in entities if e.get("entity_id") and not e.get("disabled_by")])


def discover_from_api(base_url, token):
    url = base_url.rstrip("/") + "/api/states"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            states = json.loads(response.read().decode("utf-8"))
            entity_ids = [s.get("entity_id") for s in states if "entity_id" in s]
            return match_entities(entity_ids)
    except Exception as e:
        print(f"[WARN] Failed to query HA API at {url}: {e}")
        return {}


def match_entities(entity_list):
    discovered = {}

    def find_match(suffixes):
        for eid in entity_list:
            if not isinstance(eid, str):
                continue
            for suf in suffixes:
                if eid.endswith(suf) or f"_{suf}" in eid or suf in eid:
                    return eid
        return None

    # Specific patterns for N2K entities
    stw = find_match(["speed_water_referenced", "stw"])
    if stw: discovered["stw"] = stw

    depth = find_match(["water_depth", "depth"])
    if depth: discovered["depth"] = depth

    wind_speed = find_match(["wind_speed"])
    if wind_speed: discovered["wind_speed"] = wind_speed

    wind_angle = find_match(["wind_angle"])
    if wind_angle: discovered["wind_angle"] = wind_angle

    # PGN 129026 publishes both `..._cog` (degrees) and `..._cog_reference`
    # (True/Magnetic enum). A loose match picked the *_reference enum, so Boat
    # COG showed the reference instead of the course — require an exact suffix.
    cog = None
    for eid in entity_list:
        if isinstance(eid, str) and eid.endswith("_cog"):
            cog = eid
            break
    if cog: discovered["cog"] = cog

    # "_sog" is a substring of "cog_sog_rapid_update_..._cog_reference", so a
    # loose match happily returned the COG entity as SOG (Boat SOG then showed
    # the course, not the speed). Require the entity to really end in the SOG
    # field name.
    sog = None
    for eid in entity_list:
        if isinstance(eid, str) and eid.endswith("_sog"):
            sog = eid
            break
    if sog: discovered["sog"] = sog

    lat = find_match(["_latitude"])
    if lat: discovered["latitude"] = lat

    lon = find_match(["_longitude"])
    if lon: discovered["longitude"] = lon

    pressure = find_match(["_pressure", "barometer", "pressure"])
    if pressure: discovered["pressure"] = pressure

    heading = None
    for eid in entity_list:
        if isinstance(eid, str) and "vessel_heading" in eid and (eid.endswith("_heading") or eid.endswith("_heading_magnetic")):
            heading = eid
            break
    if not heading:
        for eid in entity_list:
            if isinstance(eid, str) and (eid.endswith("_heading") or eid.endswith("_heading_magnetic")) and "message_count" not in eid:
                heading = eid
                break
    if heading: discovered["heading"] = heading

    variation = None
    for eid in entity_list:
        if isinstance(eid, str) and eid.endswith("_variation") and "message_count" not in eid:
            variation = eid
            break
    if variation: discovered["variation"] = variation

    return discovered


def generate_derived_yaml(mappings, out_path):
    stw_entity = mappings.get("stw", DEFAULT_FALLBACKS["stw"])
    depth_entity = mappings.get("depth", DEFAULT_FALLBACKS["depth"])
    wind_speed_entity = mappings.get("wind_speed", DEFAULT_FALLBACKS["wind_speed"])
    wind_angle_entity = mappings.get("wind_angle", DEFAULT_FALLBACKS["wind_angle"])
    cog_entity = mappings.get("cog", DEFAULT_FALLBACKS["cog"])
    sog_entity = mappings.get("sog", DEFAULT_FALLBACKS["sog"])
    lat_entity = mappings.get("latitude", DEFAULT_FALLBACKS["latitude"])
    lon_entity = mappings.get("longitude", DEFAULT_FALLBACKS["longitude"])
    pressure_entity = mappings.get("pressure", DEFAULT_FALLBACKS["pressure"])
    heading_entity = mappings.get("heading", DEFAULT_FALLBACKS["heading"])
    variation_entity = mappings.get("variation", DEFAULT_FALLBACKS["variation"])

    content = f"""# Generated by map_nmea_sensors.py — DO NOT EDIT MANUALLY
template:
  - sensor:
      - name: Boat STW
        unique_id: boat_stw
        unit_of_measurement: 'kts'
        device_class: speed
        availability: "{{{{ states('{stw_entity}') | is_number }}}}"
        state: >
          {{{{ states('{stw_entity}') | float(0) | round(1) }}}}

      - name: Boat Depth
        unique_id: boat_depth
        unit_of_measurement: 'm'
        availability: "{{{{ states('{depth_entity}') | is_number }}}}"
        state: >
          {{{{ states('{depth_entity}') | float(0) | round(1) }}}}

      # `availability` matters for every alias below: without it the `| float(0)`
      # default turns "no data on the bus" into a hard 0, and the charts then draw
      # a flat line of zeroes (and, for the position aliases, make open-meteo
      # forecast 0°N/0°E) instead of simply having a gap.
      - name: Boat Wind Speed
        unique_id: boat_wind_speed
        unit_of_measurement: 'kts'
        device_class: wind_speed
        availability: "{{{{ states('{wind_speed_entity}') | is_number }}}}"
        state: >
          {{{{ states('{wind_speed_entity}') | float(0) | round(1) }}}}

      - name: Boat Wind Angle
        unique_id: boat_wind_angle
        unit_of_measurement: '°'
        icon: mdi:compass-rose
        availability: "{{{{ states('{wind_angle_entity}') | is_number }}}}"
        state: >
          {{{{ states('{wind_angle_entity}') | float(0) | round(0) }}}}

      - name: Boat COG
        unique_id: boat_cog
        unit_of_measurement: '°'
        icon: mdi:compass-rose
        availability: "{{{{ states('{cog_entity}') | is_number }}}}"
        state: >
          {{{{ states('{cog_entity}') | float(0) | round(0) }}}}

      - name: Boat SOG
        unique_id: boat_sog
        unit_of_measurement: 'kts'
        device_class: speed
        availability: "{{{{ states('{sog_entity}') | is_number }}}}"
        state: >
          {{{{ states('{sog_entity}') | float(0) | round(1) }}}}

      - name: Boat Heading Magnetic
        unique_id: boat_heading_magnetic
        unit_of_measurement: '°'
        icon: mdi:compass-rose
        availability: "{{{{ states('{heading_entity}') | is_number }}}}"
        state: >
          {{{{ states('{heading_entity}') | float(0) | round(0) }}}}

      - name: Boat Magnetic Variation
        unique_id: boat_magnetic_variation
        unit_of_measurement: '°'
        icon: mdi:compass-rose
        availability: "{{{{ states('{heading_entity}') | is_number }}}}"
        state: >
          {{% set raw = states('{variation_entity}') %}}
          {{{{ raw | float(0) | round(1) if raw | is_number else 0.0 }}}}

      - name: Boat Heading
        unique_id: boat_heading
        unit_of_measurement: '°'
        icon: mdi:compass-rose
        availability: "{{{{ states('{heading_entity}') | is_number }}}}"
        state: >
          {{% set hdg = states('sensor.boat_heading_magnetic') | float(0) %}}
          {{% set var = states('sensor.boat_magnetic_variation') | float(0) %}}
          {{{{ ((hdg + var) % 360) | round(0) }}}}

      - name: Boat Latitude Raw
        unique_id: boat_latitude_raw
        availability: "{{{{ states('{lat_entity}') | is_number }}}}"
        state: >
          {{{{ states('{lat_entity}') | float(0) }}}}

      - name: Boat Longitude Raw
        unique_id: boat_longitude_raw
        availability: "{{{{ states('{lon_entity}') | is_number }}}}"
        state: >
          {{{{ states('{lon_entity}') | float(0) }}}}

      - name: Boat Pressure Raw
        unique_id: boat_pressure_raw
        availability: "{{{{ states('{pressure_entity}') | is_number }}}}"
        state: >
          {{{{ states('{pressure_entity}') | float(0) }}}}

      # Plain recorder history for the wind vector chart. Reads the raw entity
      # directly (as the working version did) so it does not inherit an extra
      # template hop, and it goes unavailable instead of reporting 0° = North.
      - name: Wind Direction History
        unique_id: wind_direction_history
        unit_of_measurement: '°'
        icon: mdi:compass-rose
        availability: "{{{{ states('{wind_angle_entity}') | is_number }}}}"
        state: >
          {{{{ states('{wind_angle_entity}') | float(0) | round(0) }}}}

      - name: Barometer mmHg
        unique_id: barometer_mmhg
        unit_of_measurement: mmHg
        device_class: pressure
        state: >
          {{% set p = states('sensor.boat_pressure_raw') | float(0) %}}
          {{{{ (p * 0.750062) | round(1) if p > 200 else (p * 7.50062) | round(1) }}}}

      - name: Boat Latitude
        unique_id: boat_latitude
        icon: mdi:latitude
        state: >
          {{% set v = states('sensor.boat_latitude_raw') | float(0) %}}
          {{% set d = v | abs | int %}}{{% set m = ((v | abs) - d) * 60 %}}
          {{{{ d }}}}°{{{{ '%.2f' | format(m) }}}}'{{{{ 'N' if v >= 0 else 'S' }}}}

      - name: Boat Longitude
        unique_id: boat_longitude
        icon: mdi:longitude
        state: >
          {{% set v = states('sensor.boat_longitude_raw') | float(0) %}}
          {{% set d = v | abs | int %}}{{% set m = ((v | abs) - d) * 60 %}}
          {{{{ d }}}}°{{{{ '%.2f' | format(m) }}}}'{{{{ 'E' if v >= 0 else 'W' }}}}

  - device_tracker:
      - name: Nevera
        unique_id: nevera_boat_gps
        latitude: >
          {{{{ states('sensor.boat_latitude_raw') | float(0) }}}}
        longitude: >
          {{{{ states('sensor.boat_longitude_raw') | float(0) }}}}
        icon: mdi:sail-boat
"""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content.strip() + "\n")
    print(f"[INFO] Successfully generated sensor definitions in {out_path}")


def main():
    parser = argparse.ArgumentParser(description="NMEA 2000 Sensor Auto-Discovery Engine")
    parser.add_argument("--config-dir", default="/config", help="Path to HA config directory")
    parser.add_argument("--entity-registry", help="Explicit path to core.entity_registry JSON file")
    parser.add_argument("--out-path", help="Path to output derived_n2k.yaml")
    parser.add_argument("--api-url", help="HA base URL for REST API discovery (http://host:8123)")
    parser.add_argument("--host", help="HA host for REST API discovery (combined with --port)")
    parser.add_argument("--token", "--api-token", dest="token", help="HA bearer token for REST API discovery")
    parser.add_argument("--port", type=int, default=8123, help="HA REST API port (used with --host)")
    parser.add_argument(
        "--target", help="Target profile from .env: takes its HA url/token when neither is given explicitly"
    )

    args = parser.parse_args()

    api_url = args.api_url or (f"http://{args.host}:{args.port}" if args.host else "")
    token = args.token or ""
    if args.target and not (api_url and token):
        # The REST endpoint belongs to the target PROFILE — never a hardcoded host.
        from env_profile import load_profile
        profile = load_profile(args.target)
        api_url = api_url or profile.ha_url
        token = token or profile.ha_token

    # This script lives in ha/sailing-dash/helpers/; src/ is one level up.
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = args.out_path or os.path.join(root_dir, "src", "yaml", "sensors", "derived_n2k.yaml")

    mappings = {}

    # 1. Try explicit registry file or config_dir
    reg_path = args.entity_registry or find_entity_registry_path(args.config_dir)
    if reg_path:
        print(f"[INFO] Scanning entity registry file: {reg_path}")
        mappings = discover_from_registry_file(reg_path)

    # 2. Try REST API if host & token provided or if no registry file found
    if not mappings and api_url and token:
        print(f"[INFO] Scanning HA REST API at {api_url}")
        mappings = discover_from_api(api_url, token)

    print("[INFO] Discovered N2K sensor mapping:")
    for k, v in mappings.items():
        print(f"  - {k}: {v}")

    generate_derived_yaml(mappings, out_path)


if __name__ == "__main__":
    main()
