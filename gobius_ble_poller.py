"""
Gobius BLE Poller — persistent background GATT connection.

Owns the single BLE connection to Gobius C. All reads and writes go through here.
No other code creates BLE connections to Gobius.

On first connect — reads ALL characteristics (device info, configs, N2K status).
Then polls telemetry (FFE8 + FFE9 + FFF3) every 5s.

BLE data is BLE data, NMEA data is NMEA data — independent channels.
"""

import asyncio
import time
from typing import Optional

# GATT UUIDs
UUID_STATUS     = "0000ffe8-0000-1000-8000-00805f9b34fb"  # R: temp, voltage, MAC, state
UUID_MEAS       = "0000ffe9-0000-1000-8000-00805f9b34fb"  # R: fill, distance
UUID_N2K_STATUS = "0000fff3-0000-1000-8000-00805f9b34fb"  # R: n2k state/src
UUID_USER_CFG   = "0000ffe6-0000-1000-8000-00805f9b34fb"  # R/W: geometry
UUID_N2K_CFG    = "0000fff2-0000-1000-8000-00805f9b34fb"  # R/W: n2k volume, fluid type
UUID_COMMAND    = "0000ffe7-0000-1000-8000-00805f9b34fb"  # W: commands
UUID_FW_REV     = "00002a28-0000-1000-8000-00805f9b34fb"
UUID_SERIAL     = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_INFO1      = "0000ffeb-0000-1000-8000-00805f9b34fb"
UUID_INFO2      = "0000ffec-0000-1000-8000-00805f9b34fb"

STATUS_POLL_INTERVAL = 30.0   # seconds between FFE8+FFF3 reads (FFE9 uses notifications)
RECONNECT_DELAY = 10.0


class GobiusBLEPoller:
    """Background BLE poller — owns the single GATT connection to Gobius C."""

    def __init__(self, device_manager, registry, mopeka_scanner=None):
        self._dm = device_manager
        self._registry = registry
        self._mopeka = mopeka_scanner
        self._client = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._paused = False
        self._last_read: float = 0.0
        self._initial_read_done = False
        self._lock = asyncio.Lock()

    def _get_mac(self) -> Optional[str]:
        sensors = self._registry.get_by_type("gobius")
        return sensors[0]["mac"] if sensors else None

    async def start(self):
        self._running = True
        self._initial_read_done = False
        self._lock = asyncio.Lock()
        self._task = asyncio.create_task(self._poll_loop())
        print("[Gobius BLE] Poller started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._disconnect()
        print("[Gobius BLE] Poller stopped")

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    @property
    def age_sec(self) -> Optional[float]:
        if self._last_read > 0:
            return round(time.time() - self._last_read, 1)
        return None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    # ─── Connection ───

    async def _connect(self) -> bool:
        mac = self._get_mac()
        if not mac:
            return False
        try:
            # Stop Mopeka scanner to free BLE adapter (BlueZ InProgress conflict)
            if self._mopeka:
                await self._mopeka.stop()
                await asyncio.sleep(0.5)

            from bleak import BleakScanner, BleakClient

            # Scan first to find device object on Linux BlueZ
            device = await BleakScanner.find_device_by_address(mac, timeout=6.0)
            if not device:
                # BlueZ may have a stale connection from a previous crash —
                # try to disconnect it so device becomes discoverable again
                print(f"[Gobius BLE] Device {mac} not found — clearing stale BlueZ connection")
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "bluetoothctl", "disconnect", mac,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except Exception:
                    pass
                try:
                    stale = BleakClient(mac, timeout=5.0)
                    await stale.connect()
                    await stale.disconnect()
                    print(f"[Gobius BLE] Stale connection cleared, will retry next cycle")
                except Exception:
                    pass
                if self._mopeka:
                    await self._mopeka.start()
                return False

            self._client = BleakClient(device, timeout=10.0)
            await self._client.connect()
            if self._client.is_connected:
                print(f"[Gobius BLE] Connected to {mac}")
                if self._mopeka:
                    await self._mopeka.start()
                return True
            if self._mopeka:
                await self._mopeka.start()
            return False
        except Exception as e:
            print(f"[Gobius BLE] Connect failed: {e}")
            self._client = None
            # Restart scanner on failure
            if self._mopeka:
                try:
                    await self._mopeka.start()
                except Exception:
                    pass
            return False

    async def _disconnect(self):
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._initial_read_done = False
        # Restart Mopeka scanner
        if self._mopeka:
            try:
                await self._mopeka.start()
            except Exception:
                pass

    # ─── Sensor helper ───

    def _ensure_sensor(self):
        from sensors import GobiusCSensor
        with self._dm._sensors_lock:
            if 0 not in self._dm.sensors:
                self._dm.sensors[0] = GobiusCSensor(instance=0, name="Gobius")
            return self._dm.sensors[0]

    # ─── Public API for routes ───

    async def read_char(self, uuid: str) -> bytes:
        """Read a GATT characteristic through the poller's connection."""
        async with self._lock:
            if not self.connected:
                raise RuntimeError("BLE not connected")
            return await self._client.read_gatt_char(uuid)

    async def write_char(self, uuid: str, data: bytes):
        """Write a GATT characteristic through the poller's connection."""
        async with self._lock:
            if not self.connected:
                raise RuntimeError("BLE not connected")
            await self._client.write_gatt_char(uuid, data)

    async def do_full_read(self) -> bool:
        """Public: trigger a full re-read of all characteristics."""
        async with self._lock:
            return await self._read_full_unlocked()

    # ─── Internal reads ───

    async def _read_full_unlocked(self) -> bool:
        """Read ALL characteristics — device info, configs, telemetry."""
        if not self._client or not self._client.is_connected:
            return False
        try:
            from gobius_parsers import parse_n2k_cfg, parse_user_cfg, parse_status, parse_measurement, parse_n2k_status

            sensor = self._ensure_sensor()

            # Telemetry (FFE8 + FFE9 + FFF3)
            status_raw = await self._client.read_gatt_char(UUID_STATUS)
            meas_raw = await self._client.read_gatt_char(UUID_MEAS)
            n2k_status_raw = await self._client.read_gatt_char(UUID_N2K_STATUS)
            sensor.update_from_ble_status(status_raw)
            sensor.update_from_ble_measurement(meas_raw)
            sensor.update_from_ble_n2k_status(n2k_status_raw)

            # Configs (FFE6 + FFF2)
            user_cfg_raw = await self._client.read_gatt_char(UUID_USER_CFG)
            n2k_cfg_raw = await self._client.read_gatt_char(UUID_N2K_CFG)
            sensor.update_from_ble_user_cfg(user_cfg_raw)
            sensor.update_from_ble_n2k_cfg(n2k_cfg_raw)

            # Device info
            serial = (await self._client.read_gatt_char(UUID_SERIAL)).decode("utf-8", errors="replace").strip()
            fw = (await self._client.read_gatt_char(UUID_FW_REV)).decode("utf-8", errors="replace").strip()
            info1 = (await self._client.read_gatt_char(UUID_INFO1)).decode("utf-8", errors="replace").strip()
            info2 = (await self._client.read_gatt_char(UUID_INFO2)).decode("utf-8", errors="replace").strip()
            sensor.update_from_ble_device_info({
                "serial": serial, "firmware": fw,
                "info1": info1, "info2": info2,
            })

            # Sync only app-level config (name) to registry.
            # Physical data (fluid_type, capacity) lives on the sensor — NOT in registry.
            mac = self._get_mac()
            if mac and info1:
                self._registry.update(mac, {"name": info1})

            # Store raw parsed dicts for /api/gobius/live config fields
            n2k_cfg_parsed = parse_n2k_cfg(n2k_cfg_raw)
            user_cfg_parsed = parse_user_cfg(user_cfg_raw)
            sensor._ble_n2k_config = n2k_cfg_parsed
            sensor._ble_user_config = user_cfg_parsed

            self._last_read = time.time()
            self._initial_read_done = True
            print(f"[Gobius BLE] Full read OK — {serial} fw:{fw}")
            return True

        except Exception as e:
            print(f"[Gobius BLE] Full read error: {e}")
            return False

    async def _subscribe_notifications(self) -> bool:
        """Subscribe to FFE9 (Measurement) notifications — sensor pushes fill/distance."""
        if not self._client or not self._client.is_connected:
            return False
        try:
            sensor = self._ensure_sensor()

            def _on_measurement(_, data: bytearray):
                sensor.update_from_ble_measurement(bytes(data))
                self._last_read = time.time()

            await self._client.start_notify(UUID_MEAS, _on_measurement)
            print("[Gobius BLE] Subscribed to FFE9 notifications")
            return True
        except Exception as e:
            print(f"[Gobius BLE] Notify subscribe failed: {e}")
            return False

    async def _read_status(self) -> bool:
        """Periodic read — FFE8 (status: temp, voltage) + FFF3 (n2k state)."""
        if not self._client or not self._client.is_connected:
            return False
        try:
            sensor = self._ensure_sensor()
            status_raw = await self._client.read_gatt_char(UUID_STATUS)
            n2k_status_raw = await self._client.read_gatt_char(UUID_N2K_STATUS)
            sensor.update_from_ble_status(status_raw)
            sensor.update_from_ble_n2k_status(n2k_status_raw)
            self._last_read = time.time()
            return True
        except Exception as e:
            print(f"[Gobius BLE] Status read error: {e}")
            return False

    # ─── Main loop ───

    async def _poll_loop(self):
        await asyncio.sleep(3.0)

        while self._running:
            try:
                if self._paused:
                    await asyncio.sleep(1.0)
                    continue

                if not self.connected:
                    ok = await self._connect()
                    if not ok:
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue

                # First connect → full read + subscribe to notifications
                if not self._initial_read_done:
                    async with self._lock:
                        ok = await self._read_full_unlocked()
                    if not ok:
                        await self._disconnect()
                        await asyncio.sleep(RECONNECT_DELAY)
                        continue
                    # Subscribe to FFE9 notifications (measurement push)
                    await self._subscribe_notifications()

                # Periodic status read (FFE8 + FFF3) — temp, voltage, n2k state
                # FFE9 measurement is handled by notifications
                async with self._lock:
                    ok = await self._read_status()
                if not ok:
                    await self._disconnect()
                    await asyncio.sleep(RECONNECT_DELAY)
                    continue

                await asyncio.sleep(STATUS_POLL_INTERVAL)

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[Gobius BLE] Poll error: {e}")
                await self._disconnect()
                await asyncio.sleep(RECONNECT_DELAY)

