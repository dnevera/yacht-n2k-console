"""Constants for the ais_targets integration.

--------------------------------------------------------------------------
NOTE ON ENTITY/ATTRIBUTE SHAPE — MUST BE RE-VERIFIED AGAINST A LIVE HA BUS
--------------------------------------------------------------------------
This environment has no access to a live NMEA 2000 bus or a running Home
Assistant instance, so the exact entity_id / attribute layout produced by
the `nmea2000` custom integration for AIS PGNs (129038/129039/129040 for
position reports, 129794 for static & voyage data) could not be observed
directly and is inferred from this project's documented naming pattern for
other nmea2000-decoded sensors (e.g. `sensor.wind_data_raymarine_20_442559_
pk_a00872..._wind_speed` in ha/sailing-dash): each decoded PGN *field*
becomes its own HA entity, sharing a "group" prefix (message id + a hash
derived from the source/primary_key) and ending in a "_<field_name>" suffix.
The MMSI itself (the field flagged `part_of_primary_key=True` in the fork,
named `userId`/`mmsiOfVesselOfOrigin` in nmea2000/pgns.py) is expected to be
exposed as the *value* of one such field entity, not necessarily as an
attribute shared by the sibling fields.

`geo_location.py` therefore groups entities defensively, by BOTH:
  1. entity_id suffix matching against the field-name tables below, and
  2. an `mmsi` (or `user_id`/`mmsi_of_vessel_of_origin`) attribute, if the
     integration happens to expose one directly on a field's state.
Anything that cannot be matched is logged (DEBUG/WARNING) and skipped
rather than raising. This MUST be re-checked against a real AIS receiver on
a live bus before this integration is trusted in production — see
custom_components/ais_targets/README.md.
--------------------------------------------------------------------------
"""

DOMAIN = "ais_targets"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_STALE_TIMEOUT = "stale_timeout"

# Seconds between scans of hass.states for AIS-derived nmea2000 entities.
DEFAULT_SCAN_INTERVAL = 30
# Minutes without an updated position before a target is expired/removed.
DEFAULT_STALE_TIMEOUT = 10

# Platform name the `nmea2000` HA integration registers its entities under
# (entity_registry entry.platform), used to scope the scan instead of
# guessing from the entity_id prefix alone.
NMEA2000_PLATFORM = "nmea2000"

# Message ids (from nmea2000/pgns.py `id` field) identifying AIS position
# reports. Matched against a normalized (lowercased, separators stripped)
# entity object_id, so "aisClassAPositionReport" and
# "ais_class_a_position_report" compare equal.
AIS_POSITION_ID_HINTS = (
    "aisclassapositionreport",
    "aisclassbpositionreport",
    "aisclassbextendedpositionreport",
)

# Message id identifying AIS static & voyage related data (PGN 129794).
AIS_STATIC_ID_HINTS = (
    "aisclassastaticandvoyagerelateddata",
)

# entity_id suffixes (after the last "_") that identify the MMSI/userId field.
MMSI_FIELD_SUFFIXES = (
    "user_id",
    "userid",
    "mmsi",
    "mmsi_of_vessel_of_origin",
)

# entity_id suffix -> normalized attribute name, for AIS position reports.
POSITION_FIELD_SUFFIXES = {
    "latitude": "latitude",
    "longitude": "longitude",
    "sog": "sog",
    "speed_over_ground": "sog",
    "cog": "cog",
    "course_over_ground": "cog",
    "heading": "heading",
    "true_heading": "heading",
    "nav_status": "nav_status",
    "navigational_status": "nav_status",
    "navstatus": "nav_status",
    "rate_of_turn": "rate_of_turn",
    "rateofturn": "rate_of_turn",
}

# entity_id suffix -> normalized attribute name, for static & voyage data.
STATIC_FIELD_SUFFIXES = {
    "vessel_name": "vessel_name",
    "name": "vessel_name",
    "call_sign": "callsign",
    "callsign": "callsign",
    "ship_type": "ship_type",
    "type_of_ship": "ship_type",
    "length": "length",
    "beam": "beam",
    "destination": "destination",
    "eta": "eta",
}

# `source` reported by every AisTarget geo_location entity — this is the
# value referenced by `geo_location_sources: ['ais_targets']` on the map card.
GEO_LOCATION_SOURCE = "ais_targets"
