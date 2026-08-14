# `ais_targets` — gateway-direct AIS → `geo_location` integration

Small custom Home Assistant integration that opens a TCP socket to the
**YDNU-02 gateway**, decodes AIS PGNs itself in RAM, and turns each tracked
vessel into a dynamic `geo_location.ais_<mmsi>` entity so it can be plotted on
the stock `map` Lovelace card via `geo_location_sources: ['ais_targets']` (the
same mechanism HA core's `adsb`/`opensky` integrations use).

## Why it reads the gateway directly (and NOT the nmea2000 integration)

Letting the `nmea2000` HA integration decode AIS creates one throwaway device
plus a batch of `sensor.ais_*` entities **per passing MMSI**. In a busy
anchorage that floods HA's device/entity registry and the recorder DB with
transient junk. So instead:

- AIS PGNs are decoded here, in memory, straight off the gateway stream — the
  `nmea2000` HA integration is kept AWAY from AIS (`ha/ais/deploy.sh --install`
  removes the AIS PGNs from its `pgn_include` and sets `exclude_AIS = True`).
- The only things ever registered in HA are the live `geo_location.ais_<mmsi>`
  entities, which are transient by design and disappear when a vessel goes out
  of range.

## How it works

1. `ais_bus.py` connects to `gw_host:gw_port` and reads the RAW
   `HH:MM:SS.mmm R <CANID> <DATA>...` line format (identical to
   `ydnu02/pgn_decoder.py`), reconnecting with backoff on any drop.
2. Only AIS PGNs are decoded (every other frame is dropped after a cheap
   CAN-ID parse). Multi-frame FastPacket AIS messages are reassembled by an
   in-process `nmea2000.NMEA2000Decoder`.
3. Decoded fields are grouped by MMSI (`userId`) into an in-memory table:
   - position PGNs **129038 / 129039 / 129040** → `latitude`, `longitude`,
     `sog` (m/s → knots), `cog`/`heading` (rad → deg), `nav_status`,
     `rate_of_turn`;
   - static/voyage PGNs **129794 / 129809 / 129810** → `vessel_name`,
     `callsign`, `ship_type`, `length`, `beam`, `destination`, `eta`
     (merged in whenever they arrive; static data never blocks a
     position-only target).
4. `geo_location.py` refreshes one `geo_location.ais_<mmsi>` entity per
   vessel every `update_interval` seconds and removes any target whose
   `last_seen` exceeds `stale_timeout`.

## Own vessel

Set `own_mmsi` to your own boat's MMSI (this config entry is the **single
source of truth** — nothing else stores it). The matching target is then named
"⛵ Bumblebee (Own Boat)" and reported under a distinct `source`
(`ais_targets_own`) so the map never plots a duplicate pin next to the
GPS-based `device_tracker.nevera` marker, while it still appears in the detail
table (filtered by `entity_id`, not `source`) with the full attribute set.

## Configuration

Set up via the HA UI (Settings → Devices & services → Add integration →
"AIS Targets"). Single instance only. All options are changeable later via the
integration's "Configure" button:

| Option | Default | Meaning |
|---|---|---|
| `gw_host` | `127.0.0.1` | YDNU-02 gateway host (as reachable from HA). |
| `gw_port` | `4001` | YDNU-02 raw-stream TCP port. |
| `own_mmsi` | *(blank)* | Your own vessel's MMSI (own-ship row / no duplicate map pin). |
| `update_interval` | 5 (seconds) | How often geo_location entities are refreshed from the in-memory table. |
| `stale_timeout` | 10 (minutes) | How long without a position update before a target is expired/removed. |

## Requirements

The `nmea2000` library must be importable inside the HA environment. It is
declared in this integration's `manifest.json` (the same fork tag as the rest
of the project) and is already present because the `nmea2000` HA integration
used by `ha/sailing-dash` bundles it — HA will not reinstall an
already-satisfied requirement.

## Field-id reference (verified against the installed fork's `pgns.py`)

`userId` (MMSI), `latitude`/`longitude` (deg), `sog` (m/s), `cog`/`heading`/
`trueHeading` (rad), `rateOfTurn` (rad/s), `navStatus` (lookup string) on the
position PGNs; `name`, `callsign`, `typeOfShip` (lookup string), `length`,
`beam`, `destination`, `etaDate`/`etaTime` on the static PGNs. See
`const.py` (`POSITION_FIELDS` / `STATIC_FIELDS`) for the exact mapping.
