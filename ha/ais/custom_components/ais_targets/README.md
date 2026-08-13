# `ais_targets` — AIS-to-`geo_location` bridge integration

Small custom Home Assistant integration that turns the raw per-field
`nmea2000` entities produced for AIS PGNs into dynamic
`geo_location.ais_<mmsi>` entities, one per tracked vessel, so they can be
plotted on the stock `map` Lovelace card via `geo_location_sources`
(the same mechanism used by HA core's built-in `adsb`/`opensky`
integrations for an unbounded, changing set of map objects).

## What it does

1. Every `scan_interval` seconds (default 30s, configurable), scans
   `hass.states` for every entity the entity registry attributes to the
   `nmea2000` platform.
2. Matches entities that look like they were decoded from:
   - PGN 129038 (`aisClassAPositionReport`)
   - PGN 129039 (`aisClassBPositionReport`)
   - PGN 129040 (`aisClassBExtendedPositionReport`)
   - PGN 129794 (`aisClassAStaticAndVoyageRelatedData`)
3. Groups matched entities by MMSI and merges them into one aggregated
   reading per vessel: `mmsi`, `latitude`, `longitude`, `sog`, `cog`,
   `heading`, `nav_status`, `rate_of_turn` from the position PGNs, plus
   `vessel_name`, `callsign`, `ship_type`, `length`, `beam`, `destination`,
   `eta` from PGN 129794 whenever available (static data never blocks a
   position-only entity).
4. Creates/updates one `geo_location.ais_<mmsi>` entity per vessel with the
   fields above as `extra_state_attributes`, plus a `last_seen` ISO
   timestamp.
5. Removes (unregisters) any target whose `last_seen` exceeds
   `stale_timeout` (default 10 minutes, configurable).

## ⚠️ IMPORTANT — entity_id/attribute shape is UNVERIFIED

This integration was developed and reviewed entirely in a sandboxed
environment with **no access to a live NMEA 2000 bus or a running Home
Assistant instance**. The exact entity_id / attribute layout that the
`nmea2000` HA integration produces for AIS PGNs (129038/129039/129040/129794)
could **not** be observed directly.

The grouping/extraction logic in `geo_location.py` is based on this
project's already-documented naming pattern for other nmea2000-decoded
sensors (see `ha/sailing-dash`'s notes on entities like
`sensor.wind_data_raymarine_20_442559_pk_a00872..._wind_speed`): each
decoded PGN *field* becomes its own HA entity, sharing a "group" prefix
(message id + a hash derived from the source/primary key) and ending in a
`_<field_name>` suffix — e.g. a hypothetical
`sensor.ais_class_a_position_report_pk_1a2b3c_latitude` alongside
`sensor.ais_class_a_position_report_pk_1a2b3c_user_id` (the MMSI, exposed as
that entity's numeric *state*).

Because this could not be confirmed against real traffic, the code:

- matches on **entity_id substrings** (message id + field-name suffix
  tables in `const.py`) **and** on an `mmsi` **attribute**, if the real
  integration happens to expose one directly (whichever produces a match
  wins);
- logs a `WARNING`/`DEBUG` and skips gracefully whenever the expected
  shape is not found, instead of raising;
- never blocks position entities on the arrival of static/voyage data.

**Before trusting this integration in production, re-verify the following
against a live HA instance with a real AIS receiver on the bus:**

1. What entity_ids the `nmea2000` integration actually creates for PGNs
   129038/129039/129040/129794 (`ha_target.sh`'s `ha_cat
   /config/.storage/core.entity_registry | grep -i ais` is a good start).
2. Whether the MMSI is exposed as a field's numeric state (as assumed here),
   as an attribute, or some other shape — and update `const.py`'s
   `MMSI_FIELD_SUFFIXES` / the attribute lookup in `geo_location.py`
   accordingly.
3. The actual field-name suffixes used for latitude/longitude/SOG/COG/
   heading/nav status/rate of turn/vessel name/callsign/ship type/length/
   beam/destination/ETA, and update `POSITION_FIELD_SUFFIXES` /
   `STATIC_FIELD_SUFFIXES` in `const.py` to match.

## Configuration

Set up via the HA UI (Settings → Devices & services → Add integration →
"AIS Targets"). Single instance only. Two options, changeable later via the
integration's "Configure" button:

| Option | Default | Meaning |
|---|---|---|
| `scan_interval` | 30 (seconds) | How often `hass.states` is re-scanned. |
| `stale_timeout` | 10 (minutes) | How long without an update before a target is expired/removed. |
