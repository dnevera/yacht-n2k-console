"""Constants for the ais_targets integration.

--------------------------------------------------------------------------
ARCHITECTURE (2026-08-13 re-architecture)
--------------------------------------------------------------------------
`ais_targets` does NOT read the `nmea2000` HA integration's entities anymore.
Letting that integration decode AIS floods HA's device/entity registry (and
the recorder DB) with one throwaway device + a batch of sensors per passing
MMSI. Instead this component opens a plain TCP socket to the YDNU-02 gateway
(the same raw `TIME R CANID DATA...` stream `ydnu02/pgn_decoder.py` parses),
decodes AIS PGNs in-process with the `nmea2000` library, and keeps a per-MMSI
in-memory target table. The only things it ever registers in HA are the live
`geo_location.ais_<mmsi>` entities, which are transient by design.

Field ids below were verified against the installed `nmea2000` fork's
`pgns.py` decoders (decode_pgn_129038/129039/129040/129794/129809/129810).
--------------------------------------------------------------------------
"""

DOMAIN = "ais_targets"

# ── Config-entry option keys (single source of truth, set via the UI) ───────
CONF_GW_HOST = "gw_host"
CONF_GW_PORT = "gw_port"
CONF_OWN_MMSI = "own_mmsi"
# Static identity of OUR boat. Our own transceiver broadcasts its position
# (129039) onto the N2K bus but — verified on the live bus — never its own
# msg24/static data, so our row would otherwise stay "AIS <mmsi>" with empty
# name/callsign/type/size columns forever. These options are the single source
# of truth for those fields and are used ONLY as a fallback, i.e. only for the
# target matching `own_mmsi` and only while the bus has not delivered the real
# static data for it.
CONF_OWN_NAME = "own_name"
CONF_OWN_CALLSIGN = "own_callsign"
CONF_OWN_SHIP_TYPE = "own_ship_type"
CONF_OWN_LENGTH = "own_length"
CONF_OWN_BEAM = "own_beam"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_STALE_TIMEOUT = "stale_timeout"

# Default gateway endpoint. The YDNU-02 TCP gateway broadcasts the raw N2K
# bus on port 4001; the host depends on where HA runs relative to the
# gateway and MUST be set by the skipper in the config flow.
DEFAULT_GW_HOST = "127.0.0.1"
DEFAULT_GW_PORT = 4001
# Empty by default: our own AIS unit also broadcasts its own MMSI (own-ship
# message), so once the skipper fills this in, that target is flagged as our
# own boat (named "Bumblebee") and reported under a distinct geo_location
# `source` so it never gets plotted twice next to device_tracker.nevera.
DEFAULT_OWN_MMSI = ""
# Own-boat static fallbacks, all optional (blank = leave the field unknown).
DEFAULT_OWN_NAME = ""
DEFAULT_OWN_CALLSIGN = ""
DEFAULT_OWN_SHIP_TYPE = ""
DEFAULT_OWN_LENGTH = ""
DEFAULT_OWN_BEAM = ""
# Seconds between refreshes of the geo_location entities from the in-memory
# target table (the socket read itself is continuous/push-driven).
DEFAULT_UPDATE_INTERVAL = 5
# Minutes without a position update before a target is expired/removed.
DEFAULT_STALE_TIMEOUT = 10

# ── AIS PGNs we decode off the bus ──────────────────────────────────────────
# Position reports (frequent): lat/lon/sog/cog/heading/nav_status/rate_of_turn.
PGN_AIS_CLASS_A_POSITION = 129038
PGN_AIS_CLASS_B_POSITION = 129039
PGN_AIS_CLASS_B_EXT_POSITION = 129040
AIS_POSITION_PGNS = frozenset(
    {
        PGN_AIS_CLASS_A_POSITION,
        PGN_AIS_CLASS_B_POSITION,
        PGN_AIS_CLASS_B_EXT_POSITION,
    }
)
# Static & voyage data (infrequent): name/callsign/type/size/destination/eta.
PGN_AIS_CLASS_A_STATIC = 129794
PGN_AIS_CLASS_B_STATIC_A = 129809  # msg24 part A: name
PGN_AIS_CLASS_B_STATIC_B = 129810  # msg24 part B: type/callsign/size
AIS_STATIC_PGNS = frozenset(
    {
        PGN_AIS_CLASS_A_STATIC,
        PGN_AIS_CLASS_B_STATIC_A,
        PGN_AIS_CLASS_B_STATIC_B,
    }
)
# ── OUR OWN position, straight off the bus ──────────────────────────────────
# The boat carries proper GNSS receivers with the antennas out in the open sky,
# and they broadcast their fix on the very same N2K bus we already read. So the
# distance origin is taken from THESE PGNs — not from HA's `device_tracker.*`
# template sensor, which re-derives the same fix through two extra layers (the
# nmea2000 integration + a Jinja template with a last-known-position hold) and
# on prod was observed publishing a partial fix (longitude 0.0).
PGN_GNSS_POSITION_DATA = 129029   # full GNSS fix (FastPacket)
PGN_POSITION_RAPID_UPDATE = 129025  # lat/lon only, ~1 Hz
OWN_POSITION_PGNS = frozenset({PGN_GNSS_POSITION_DATA, PGN_POSITION_RAPID_UPDATE})

# Full set the drift-guard verifies is decodable by the installed fork.
ALL_AIS_PGNS = frozenset(
    AIS_POSITION_PGNS
    | AIS_STATIC_PGNS
    | {129041, 129793}  # AtoN report, UTC/date report (decoded, not plotted)
)

# ── nmea2000 field ids (from the fork's pgns.py) → our normalized names ──────
# The MMSI is the `userId` field on every AIS message.
FIELD_MMSI = "userId"

# Position-report field id → attribute name. Values come from NMEA2000Field.value
# (already scaled by the library): lat/lon in deg, cog/heading in rad, sog in
# m/s, rateOfTurn in rad/s, navStatus is a LOOKUP → human string.
POSITION_FIELDS = {
    "latitude": "latitude",
    "longitude": "longitude",
    "sog": "sog_ms",
    "cog": "cog_rad",
    "heading": "heading_rad",
    "trueHeading": "heading_rad",
    "navStatus": "nav_status",
    "rateOfTurn": "rate_of_turn",
}

# Static/voyage field id → attribute name (129794 / 129809 / 129810 / 129040).
STATIC_FIELDS = {
    "name": "vessel_name",
    "callsign": "callsign",
    "typeOfShip": "ship_type",
    "length": "length",
    "beam": "beam",
    "destination": "destination",
    "etaDate": "eta_date",
    "etaTime": "eta_time",
}

# `source` reported by EVERY AIS target entity, including our own boat — this
# is the value referenced by `geo_location_sources: ['ais_targets']` on the
# map card. Our own boat used to get a distinct source so it would never be
# plotted twice next to the GPS-based device_tracker.nevera marker, but that
# meant it depended entirely on that GPS tracker to appear on the map at all;
# it now shares this source like every other target (flagged separately via
# the `is_own_ship` attribute) so it is guaranteed visible once our own
# AIS unit's own-ship message is decoded, even if device_tracker.nevera is
# stale/unavailable.
GEO_LOCATION_SOURCE = "ais_targets"
