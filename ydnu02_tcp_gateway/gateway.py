"""TCP Gateway for YDNU-02 NMEA 2000 USB interface.

Facade class `Gateway` coordinating serial reader, data hub, control handler,
and TCP servers on ports 4001 (DATA) and 4002 (CTRL).
"""

import sys
import socket
import threading
from typing import Callable

from ydnu02_tcp_gateway.data_hub import DataHub
from ydnu02_tcp_gateway.ctrl_handler import CtrlHandler
from ydnu02_tcp_gateway.serial_reader import SerialReader


def make_server(host: str, port: int) -> socket.socket:
    """Create and bind a TCP server socket with SO_REUSEADDR."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    return srv


def accept_loop(srv: socket.socket, handler: Callable[[socket.socket, tuple], None], label: str = "") -> None:
    """Accept TCP connections in a loop, spawning a handler thread per client."""
    while True:
        try:
            conn, addr = srv.accept()
            t = threading.Thread(target=handler, args=(conn, addr), daemon=True)
            t.start()
        except KeyboardInterrupt:
            break
        except OSError:
            break


class Gateway:
    """Gateway orchestrator managing serial connection and TCP servers."""

    def __init__(self,
                 serial_port: str = "/dev/ttyACM0",
                 serial_baud: int = 115200,
                 tcp_host: str = "",
                 data_port: int = 4001,
                 ctrl_port: int = 4002):
        self.serial_port = serial_port
        self.serial_baud = serial_baud
        self.tcp_host = tcp_host
        self.data_port = data_port
        self.ctrl_port = ctrl_port

        self.serial_instance = None
        self.serial_lock = threading.Lock()

        self.service_mode = threading.Event()
        self.serial_ready = threading.Event()

        self.data_hub = DataHub(
            get_serial_instance=lambda: self.serial_instance,
            get_serial_ready=lambda: self.serial_ready.is_set(),
            get_service_mode=lambda: self.service_mode.is_set(),
            serial_lock=self.serial_lock,
        )

        def set_ser(ser):
            self.serial_instance = ser

        self.ctrl_handler = CtrlHandler(
            service_mode=self.service_mode,
            get_serial_instance=lambda: self.serial_instance,
            set_serial_instance=set_ser,
            serial_lock=self.serial_lock,
            serial_port=self.serial_port,
            serial_baud=self.serial_baud,
        )

        self.serial_reader = SerialReader(
            serial_port=self.serial_port,
            serial_baud=self.serial_baud,
            get_serial_instance=lambda: self.serial_instance,
            set_serial_instance=set_ser,
            serial_lock=self.serial_lock,
            serial_ready=self.serial_ready,
            service_mode=self.service_mode,
            broadcast=self.data_hub.broadcast,
            send_iso_request=self.data_hub.send_iso_request,
        )

    def start(self, start_gateway_device: bool = True) -> None:
        """Start serial reader, virtual device, and TCP servers."""
        t_serial = threading.Thread(target=self.serial_reader.run, daemon=True)
        t_serial.start()

        if start_gateway_device:
            try:
                from ydnu02_gateway_device import start_in_thread as start_gw_device
                start_gw_device()
            except ImportError:
                print("[proxy] virtual gateway device not available (missing library)", flush=True)

        data_srv = make_server(self.tcp_host, self.data_port)
        print(f"[proxy] NMEA data  listening on :{self.data_port}", flush=True)

        ctrl_srv = make_server(self.tcp_host, self.ctrl_port)
        print(f"[proxy] NMEA ctrl  listening on :{self.ctrl_port}", flush=True)

        t_ctrl = threading.Thread(
            target=accept_loop,
            args=(ctrl_srv, self.ctrl_handler.handle_client, "ctrl"),
            daemon=True,
        )
        t_ctrl.start()

        try:
            accept_loop(data_srv, self.data_hub.handle_client, "data")
        except KeyboardInterrupt:
            print("Shutting down.", flush=True)
            sys.exit(0)
