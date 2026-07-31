#!/usr/bin/env python3
"""
gateway_settings.py — Runtime-configurable settings for the YDNU-02 TCP Gateway.
==================================================================================

PURPOSE
  Provides a thread-safe singleton ``GatewaySettings`` that persists user-facing
  daemon settings to JSON. Changes made via the web UI API (``POST /api/gw-settings``)
  take effect within one ``GW_TEMP_INTERVAL_S`` loop iteration (~3s) without
  restarting the daemon.

PERSISTENCE
  Settings are stored in ``~/.config/ydnu02/gateway_settings.json``.
  File is created with defaults on first run.

THREAD SAFETY
  ``GatewaySettings`` is accessed from two threads:
    - FastAPI async event loop (HTTP request handlers) — writes via ``apply_from_dict()``
    - Gateway device daemon thread — reads on every loop iteration

  A ``threading.Lock`` protects ``_data`` dict mutations. Individual field reads
  from the daemon loop do NOT need to hold the lock because Python ``bool`` and
  ``float`` assignments are atomic at the C-level (GIL-protected).

KNOWN ISSUES / SKILLS
  Skill — read current settings via API::

      curl -s http://gateway.local:8080/api/gw-settings | python3 -m json.tool

  Skill — disable ISO replay at runtime::

      curl -X POST http://gateway.local:8080/api/gw-settings \\
           -H 'Content-Type: application/json' \\
           -d '{"ha_iso_replay_enabled": false}'

  Skill — read settings file directly on Pi::

      ssh user@gateway.local 'cat ~/.config/ydnu02/gateway_settings.json'

  Skill — reset to defaults (delete file, restart daemon)::

      ssh user@gateway.local 'rm ~/.config/ydnu02/gateway_settings.json'
      ssh user@gateway.local 'sudo systemctl restart ydnu02-tcp-gateway'
"""

import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────────────

_SETTINGS_DIR  = os.path.expanduser('~/.config/ydnu02')
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, 'gateway_settings.json')

_DEFAULTS: dict = {
    'ha_iso_replay_enabled':    True,
    'ha_iso_replay_interval_s': 60.0,
}

# ── Singleton ─────────────────────────────────────────────────────────────────

_instance: 'GatewaySettings | None' = None
_instance_lock = threading.Lock()


class GatewaySettings:
    """Thread-safe singleton holding runtime gateway settings.

    Usage::

        settings = GatewaySettings.instance()
        if settings.ha_iso_replay_enabled:
            ...

    Settings are persisted to ``~/.config/ydnu02/gateway_settings.json``
    and reloaded on ``instance()`` construction. Changes via ``apply_from_dict()``
    are written immediately to disk.

    Skill — check current values in running daemon::

        curl -s http://gateway.local:8080/api/gw-settings | python3 -m json.tool

    Skill — update interval without restart::

        curl -X POST http://gateway.local:8080/api/gw-settings \\
             -H 'Content-Type: application/json' \\
             -d '{"ha_iso_replay_interval_s": 30}'
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict = dict(_DEFAULTS)
        self._load()

    # ── Singleton access ──────────────────────────────────────────────────────

    @classmethod
    def instance(cls) -> 'GatewaySettings':
        """Return the process-wide singleton, creating it on first call."""
        global _instance
        if _instance is None:
            with _instance_lock:
                if _instance is None:
                    _instance = cls()
        return _instance

    # ── Properties (daemon loop reads these directly) ─────────────────────────

    @property
    def ha_iso_replay_enabled(self) -> bool:
        """Whether periodic ISO Claim + Product Info replay is active.

        KI-001 workaround: when True, the gateway device daemon re-broadcasts
        PGN 60928 + PGN 126996 every ``ha_iso_replay_interval_s`` seconds so
        Home Assistant populates ``source_to_iso_name`` for all known devices.
        """
        return bool(self._data.get('ha_iso_replay_enabled', _DEFAULTS['ha_iso_replay_enabled']))

    @property
    def ha_iso_replay_interval_s(self) -> float:
        """Interval in seconds between ISO replay broadcasts (default 60.0)."""
        return float(self._data.get('ha_iso_replay_interval_s',
                                    _DEFAULTS['ha_iso_replay_interval_s']))

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Return a copy of current settings as a plain dict."""
        with self._lock:
            return dict(self._data)

    def apply_from_dict(self, updates: dict) -> dict:
        """Validate and apply a partial settings update, then persist to disk.

        Only known keys from ``_DEFAULTS`` are accepted; unknown keys are ignored.
        Type coercions:
          - ``ha_iso_replay_enabled``    → bool
          - ``ha_iso_replay_interval_s`` → float, clamped to [5.0, 3600.0]

        Args:
            updates: Partial dict with settings to update.

        Returns:
            Updated full settings dict.

        Raises:
            ValueError: If a value fails type coercion or range validation.
        """
        with self._lock:
            if 'ha_iso_replay_enabled' in updates:
                self._data['ha_iso_replay_enabled'] = bool(updates['ha_iso_replay_enabled'])

            if 'ha_iso_replay_interval_s' in updates:
                val = float(updates['ha_iso_replay_interval_s'])
                if not (5.0 <= val <= 3600.0):
                    raise ValueError(
                        f'ha_iso_replay_interval_s must be 5–3600, got {val}'
                    )
                self._data['ha_iso_replay_interval_s'] = val

            result = dict(self._data)

        self._save()
        return result

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load settings from disk, falling back to defaults on any error."""
        try:
            with open(_SETTINGS_FILE) as f:
                on_disk = json.load(f)
            # Merge: known keys only, unknown keys from disk are ignored
            for key in _DEFAULTS:
                if key in on_disk:
                    self._data[key] = on_disk[key]
            logger.warning('[gw-settings] Loaded from %s: %s', _SETTINGS_FILE, self._data)
        except FileNotFoundError:
            logger.warning('[gw-settings] No settings file found, using defaults')
            self._save()  # create file with defaults
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning('[gw-settings] Failed to load settings (%s), using defaults', exc)

    def _save(self) -> None:
        """Persist current settings to disk (creates directory if needed)."""
        try:
            os.makedirs(_SETTINGS_DIR, exist_ok=True)
            with open(_SETTINGS_FILE, 'w') as f:
                json.dump(self._data, f, indent=2)
                f.write('\n')
            logger.warning('[gw-settings] Saved: %s', self._data)
        except Exception as exc:
            logger.warning('[gw-settings] Failed to save settings: %s', exc)
