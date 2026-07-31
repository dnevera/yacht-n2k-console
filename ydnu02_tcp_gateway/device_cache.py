"""Device frame cache for YDNU-02 TCP Gateway.

Stores ISO Address Claims (PGN 60928) and Product Info (PGN 126996) per Source Address.
Replays cached frames to newly connected TCP clients for instant device discovery.
"""

import socket
import threading
from typing import Dict, Any, List
from ydnu02_tcp_gateway.frame_utils import get_pgn_sa


class DeviceFrameCache:
    """Per-SA storage and fast-packet reassembly of device identification frames."""

    def __init__(self):
        self._cache: Dict[int, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

        self._fp_buf: Dict[int, Dict[str, Any]] = {}
        self._fp_lock = threading.Lock()

    @property
    def cache(self) -> Dict[int, Dict[str, Any]]:
        """Access raw cache dictionary (protected by lock when reading/writing)."""
        return self._cache

    @property
    def lock(self) -> threading.Lock:
        return self._cache_lock

    def update_from_line(self, line: bytes) -> None:
        """Update device frame cache from live N2K line bytes."""
        if len(line) < 24:
            return
        try:
            pgn, sa = get_pgn_sa(line[15:23])
            if pgn == 60928:
                with self._cache_lock:
                    self._cache.setdefault(sa, {})['iso_claim'] = line
                print(f"[cache] ISO Claim cached SA={sa}", flush=True)
            elif pgn == 126996:
                self.cache_product_info_frame(sa, line)
        except (ValueError, IndexError):
            pass

    def cache_product_info_frame(self, sa: int, line: bytes) -> None:
        """Buffer a PGN 126996 (Product Information) fast-packet frame."""
        data_parts = line[24:].decode('ascii', errors='ignore').split()
        if not data_parts:
            return
        try:
            fb = int(data_parts[0], 16)
        except ValueError:
            return

        frame_num = fb & 0x1F
        seq_num   = (fb >> 5) & 0x07

        with self._fp_lock:
            if frame_num == 0:
                total = int(data_parts[1], 16) if len(data_parts) > 1 else 0
                self._fp_buf[sa] = {'seq': seq_num, 'total': total, 'lines': [line]}
            else:
                buf = self._fp_buf.get(sa)
                if buf is None or buf['seq'] != seq_num:
                    return
                if len(buf['lines']) != frame_num:
                    return
                buf['lines'].append(line)
                received = 6 + (len(buf['lines']) - 1) * 7
                if received >= buf['total']:
                    complete = list(buf['lines'])
                    with self._cache_lock:
                        self._cache.setdefault(sa, {})['product_info'] = complete
                    del self._fp_buf[sa]
                    print(f"[cache] Product Info cached SA={sa} "
                          f"({len(complete)} frames)", flush=True)

    def replay(self, conn: socket.socket) -> None:
        """Replay cached device identification frames to a newly connected client."""
        with self._cache_lock:
            snapshot = {sa: dict(e) for sa, e in self._cache.items()}

        if not snapshot:
            print("[data] no cached device frames to replay", flush=True)
            return

        sent = 0
        for sa, entry in sorted(snapshot.items()):
            try:
                if 'iso_claim' in entry:
                    conn.sendall(entry['iso_claim'])
                    sent += 1
                for frame in entry.get('product_info', []):
                    conn.sendall(frame)
                    sent += 1
            except OSError:
                break

        print(f"[data] replayed {sent} frame(s) for {len(snapshot)} device(s)", flush=True)
