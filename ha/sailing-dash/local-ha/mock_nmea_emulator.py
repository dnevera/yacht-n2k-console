#!/usr/bin/env python3
"""
NMEA 2000 PGN Frames Emulator for Stage Home Assistant Demo Mode

Simulates a live TCP NMEA 2000 gateway (port 4001) broadcasting realistic
marine sensor frames (Speed, Depth, Wind, Position, Heading, COG/SOG, Barometer)
to connected TCP clients (such as Home Assistant ha-nmea2000 integration).
"""

import sys
import os
import time
import math
import socket
import struct
import threading
import argparse
from datetime import datetime


def fmt_nmea_line(can_id_hex: str, data_bytes: bytes) -> str:
    """Format a CAN ID and raw data bytes into standard NMEA 2000 ASCII format.
    Format: HH:MM:SS.mmm R <CAN_ID_HEX> <DATA_HEX_SPACED>\n
    """
    now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    hex_data = " ".join(f"{b:02X}" for b in data_bytes)
    return f"{now} R {can_id_hex} {hex_data}\n"


class NMEAEmulatorServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 4001):
        self.host = host
        self.port = port
        self.server_sock = None
        self.clients = set()
        self.clients_lock = threading.Lock()
        self.running = False
        self._start_time = time.time()

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(10)
        self.running = True

        print(f"[NMEA-EMULATOR] Listening on TCP {self.host}:{self.port} ...")

        # Start accept thread
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()

        # Start simulation broadcasting loop
        broadcast_thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        broadcast_thread.start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                print(f"[NMEA-EMULATOR] Client connected: {addr}")
                with self.clients_lock:
                    self.clients.add(conn)

                # Send onboarding ISO claims and Product Info for SA=64 and SA=200
                threading.Thread(target=self._onboard_client, args=(conn,), daemon=True).start()
            except Exception as e:
                if self.running:
                    print(f"[NMEA-EMULATOR] Accept error: {e}")
                break

    def _send_to_client(self, conn, text: str):
        try:
            conn.sendall(text.encode("ascii"))
        except Exception:
            with self.clients_lock:
                self.clients.discard(conn)

    def _broadcast(self, text: str):
        with self.clients_lock:
            dead = set()
            for conn in list(self.clients):
                try:
                    conn.sendall(text.encode("ascii"))
                except Exception:
                    dead.add(conn)
            for d in dead:
                self.clients.discard(d)

    def _onboard_client(self, conn):
        """Send ISO Address Claim and Product Info to new client."""
        # ISO Address Claim SA=64 (Raymarine Sensor Node) & SA=200 (TCP Gateway)
        iso_name_64 = struct.pack("<IHBBBB", 442559, 409, 4, 8, 85, 0b11100000 | 7) + b"\x00"
        iso_name_200 = struct.pack("<IHBBBB", 902047, 409, 1, 1, 15, 0b11100000 | 7) + b"\x00"

        line_claim_64 = fmt_nmea_line("18EEFF40", iso_name_64[:8])
        line_claim_200 = fmt_nmea_line("18EEFFC8", iso_name_200[:8])

        self._send_to_client(conn, line_claim_64)
        self._send_to_client(conn, line_claim_200)

        time.sleep(0.6)

        # Product Info PGN 126996
        prod_64 = b"\x00\x00\x01\x00" + b"Raymarine DST800".ljust(32, b"\x00") + b"1.00".ljust(32, b"\x00") + b"SER64001".ljust(32, b"\x00") + b"\x01"
        prod_200 = b"\x00\x00\x01\x00" + b"ydnu02_tcp_gateway".ljust(32, b"\x00") + b"2.10".ljust(32, b"\x00") + b"GW200001".ljust(32, b"\x00") + b"\x01"

        self._send_to_client(conn, fmt_nmea_line("19F01440", prod_64[:8]))
        self._send_to_client(conn, fmt_nmea_line("19F014C8", prod_200[:8]))

    def _broadcast_loop(self):
        sid = 1
        while self.running:
            time.sleep(1.0)
            if not self.clients:
                continue

            t = time.time() - self._start_time
            sid = (sid % 250) + 1

            # 1. STW (PGN 128259 = 0x1F503) -> 09F50340
            stw_knots = 6.0 + 1.5 * math.sin(t / 10.0)
            stw_mps = stw_knots * 0.514444
            stw_data = struct.pack("<BHHB", sid, int(stw_mps / 0.01), 0xFFFF, 0xFF) + b"\xFF\xFF"
            self._broadcast(fmt_nmea_line("09F50340", stw_data))

            # 2. Depth (PGN 128267 = 0x1F50B) -> 09F50B40
            depth_m = 8.5 + 2.0 * math.cos(t / 15.0)
            depth_data = struct.pack("<BiHB", sid, int(depth_m / 0.01), 0, 0xFF)
            self._broadcast(fmt_nmea_line("09F50B40", depth_data))

            # 3. Wind Data (PGN 130306 = 0x1FD02) -> 09FD0240
            wind_speed_kts = 12.0 + 4.0 * math.sin(t / 8.0)
            wind_mps = wind_speed_kts * 0.514444
            wind_angle_deg = 45.0 + 10.0 * math.sin(t / 12.0)
            wind_rad = math.radians(wind_angle_deg)
            wind_data = struct.pack("<BHHB", sid, int(wind_mps / 0.01), int(wind_rad / 0.0001), 0) + b"\xFF\xFF"
            self._broadcast(fmt_nmea_line("09FD0240", wind_data))

            # 4. GPS Position Rapid (PGN 129025 = 0x1F801) -> 09F80140
            lat = 42.4300 + (t * 0.00001)
            lon = 18.6000 + (t * 0.000015)
            pos_data = struct.pack("<ii", int(lat * 1e7), int(lon * 1e7))
            self._broadcast(fmt_nmea_line("09F80140", pos_data))

            # 5. COG & SOG (PGN 129026 = 0x1F802) -> 09F80240
            cog_deg = 210.0 + 5.0 * math.sin(t / 20.0)
            cog_rad = math.radians(cog_deg)
            sog_mps = stw_mps * 0.98
            cog_sog_data = struct.pack("<BBHH2x", sid, 0, int(cog_rad / 0.0001), int(sog_mps / 0.01))
            self._broadcast(fmt_nmea_line("09F80240", cog_sog_data))

            # 6. Heading (PGN 127250 = 0x1F112) -> 09F11240
            hdg_rad = math.radians(cog_deg - 3.0)
            var_rad = math.radians(2.5)
            hdg_data = struct.pack("<BHHhb", sid, int(hdg_rad / 0.0001), 0x7FFF, int(var_rad / 0.0001), 0)
            self._broadcast(fmt_nmea_line("09F11240", hdg_data))

            # 7. Environmental / Pressure (PGN 130310 = 0x1FD0A) -> 09FD0A40
            temp_k = 294.65  # 21.5 C
            pressure_hpa = 1013.2 + 0.5 * math.sin(t / 30.0)
            env_data = struct.pack("<BHHH", sid, int(temp_k / 0.01), int(temp_k / 0.01), int(pressure_hpa)) + b"\xFF"
            self._broadcast(fmt_nmea_line("09FD0A40", env_data))

    def stop(self):
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        with self.clients_lock:
            for conn in self.clients:
                try:
                    conn.close()
                except Exception:
                    pass
            self.clients.clear()


def main():
    parser = argparse.ArgumentParser(description="NMEA 2000 Stage Demo Emulator")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=4001, help="TCP port (default: 4001)")
    args = parser.parse_args()

    emulator = NMEAEmulatorServer(host=args.host, port=args.port)
    emulator.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[NMEA-EMULATOR] Stopping server...")
        emulator.stop()


if __name__ == "__main__":
    main()
