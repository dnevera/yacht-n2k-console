#!/usr/bin/env python3
"""
Yacht Devices YDNU-02 NMEA 2000 USB Gateway — Controller, Diagnostic & Monitor Tool.

Two-level command architecture (from official YDNU-02 User Manual):
  Level 1: OS Shell commands — sent via `echo > port` (port must be CLOSED).
  Level 2: Service Menu commands — sent via serial session (port must be OPEN).

Usage:
  ydnu02.py service info|help|reset|diag|filters|shell
  ydnu02.py monitor raw|0183|scan [-t SEC] [--log]
  ydnu02.py mode auto|0183|raw|n2k|service
  ydnu02.py silent on|off
  ydnu02.py diag-record
  ydnu02.py firmware FILE.BIN
"""

import sys
import os
import time
import subprocess
import json
import re
from datetime import datetime
import serial
import glob
import argparse
from typing import Optional, List, Dict, Any

# nmea2000 library — reference decoder/encoder for NMEA 2000
try:
    from nmea2000 import NMEA2000Decoder as _N2KDecoder
    from nmea2000.consts import IndirectLookupEncodeMaps as _N2KMaps
    _n2k_decoder = _N2KDecoder()
    # Build reverse device function map: (class, func_code) → name
    _DEVICE_FUNC_REVERSE: Dict[tuple, str] = {}
    for _cls, _funcs in _N2KMaps.get("DEVICE_FUNCTION", {}).items():
        for _name, _code in _funcs.items():
            _DEVICE_FUNC_REVERSE[(_cls, _code)] = _name
    _HAS_N2K_LIB = True
except ImportError:
    _n2k_decoder = None
    _DEVICE_FUNC_REVERSE = {}
    _HAS_N2K_LIB = False


# ---------------------------------------------------------------------------
#  N2K PGN Decoder
# ---------------------------------------------------------------------------

class N2KPGNDecoder:
    """Декодер 29-bit CAN ID и PGN пакетов NMEA 2000.
    
    Uses the nmea2000 library (tomer-w/nmea2000) for full PGN decoding,
    device class/function name resolution, and manufacturer lookup.
    Falls back to raw hex display if library is unavailable.
    """

    @classmethod
    def parse_device_info(cls, parsed: Dict[str, Any]) -> Dict[str, Any]:
        """Parse PGN 60928 / 126996 from parse_raw_line() result into structured device info.
        
        Uses nmea2000 library for correct (device_class, function) → name resolution.
        """
        pgn = parsed.get("info", {}).get("pgn", 0)
        data = parsed.get("data", b"")
        raw_line = parsed.get("raw", "")
        result = {}

        if pgn == 60928 and len(data) >= 8:
            # Try library decode first for correct field extraction
            lib_msg = cls._decode_via_lib(parsed)
            if lib_msg and lib_msg.source_iso_name:
                iso = lib_msg.source_iso_name
                result["unique_id"] = iso.unique_number
                result["mfg_code"] = iso.manufacturer_code
                result["function"] = iso.device_function
                result["device_class"] = iso.device_class
                # IsoName returns human-readable strings when known,
                # or raw int codes when unknown — use directly
                result["device_class_name"] = str(iso.device_class)
                result["function_name"] = str(iso.device_function)
                # Manufacturer: library field value or raw code
                mfr_field = lib_msg.get_field_by_id("manufacturerCode")
                if mfr_field and mfr_field.value:
                    result["manufacturer"] = str(mfr_field.value)
                else:
                    result["manufacturer"] = f"MfgCode {iso.manufacturer_code}"
            else:
                # Fallback: manual bit extraction
                val = int.from_bytes(data[:8], 'little')
                result["unique_id"] = val & 0x1FFFFF
                result["mfg_code"] = (val >> 21) & 0x7FF
                result["function"] = (val >> 40) & 0xFF
                result["device_class"] = (val >> 49) & 0x7F
                result["device_class_name"] = cls._class_name(result["device_class"])
                result["function_name"] = _DEVICE_FUNC_REVERSE.get(
                    (result["device_class"], result["function"]),
                    f"Function {result['function']}"
                )
                result["manufacturer"] = f"MfgCode {result['mfg_code']}"

        elif pgn == 126996 and len(data) >= 36:
            # Product Information — try library decode
            lib_msg = cls._decode_via_lib(parsed)
            if lib_msg:
                fields = {f.id: f for f in lib_msg.fields}
                if "modelId" in fields and fields["modelId"].value:
                    result["model"] = str(fields["modelId"].value).strip()
                if "softwareVersionCode" in fields and fields["softwareVersionCode"].value:
                    result["firmware"] = str(fields["softwareVersionCode"].value).strip()
                if "modelVersion" in fields and fields["modelVersion"].value:
                    result["model_version"] = str(fields["modelVersion"].value).strip()
                if "modelSerialCode" in fields and fields["modelSerialCode"].value:
                    result["serial"] = str(fields["modelSerialCode"].value).strip()
                pc = fields.get("nmea2000DatabaseVersion") or fields.get("nmea2000CertificationLevel")
                if pc:
                    result["product_code"] = pc.raw_value
            else:
                # Fallback: manual extraction
                result["product_code"] = int.from_bytes(data[2:4], 'little')
                def _extract(start, end):
                    if len(data) > start:
                        chunk = data[start:min(end, len(data))]
                        return chunk.split(b"\x00")[0].decode("ascii", errors="ignore").strip("\xff ")
                    return ""
                model = _extract(4, 36)
                if model: result["model"] = model
                fw = _extract(36, 68)
                if fw: result["firmware"] = fw
                mv = _extract(68, 100)
                if mv: result["model_version"] = mv
                sn = _extract(100, 132)
                if sn: result["serial"] = sn

        return result

    @staticmethod
    def _class_name(dev_class: int) -> str:
        """Resolve device class code to human-readable name."""
        _DEVICE_CLASS_NAMES = {
            0: "Reserved", 10: "System Tools", 20: "Safety Systems",
            25: "Inter/Intranetwork Device", 30: "Electrical Distribution",
            35: "Electrical Generation", 40: "Steering and Control",
            50: "Propulsion", 60: "Navigation", 70: "Communication",
            75: "Sensor Communication Interface", 80: "Instrumentation",
            85: "External Environment", 90: "Internal Environment",
        }
        return _DEVICE_CLASS_NAMES.get(dev_class, f"Class {dev_class}")

    @staticmethod
    def parse_can_id(can_id_hex: str) -> Dict[str, int]:
        """Разбор 29-битного CAN ID в компоненты NMEA 2000."""
        can_id = int(can_id_hex, 16)
        priority = (can_id >> 26) & 0x7
        pgn_raw = (can_id >> 8) & 0x3FFFF
        src = can_id & 0xFF

        pdu_format = (pgn_raw >> 8) & 0xFF
        pdu_specific = pgn_raw & 0xFF
        if pdu_format < 240:  # PDU1: addressed
            dst = pdu_specific
            pgn = pgn_raw & 0x3FF00
        else:  # PDU2: broadcast
            dst = 255
            pgn = pgn_raw

        return {"can_id": can_id, "priority": priority, "pgn": pgn, "src": src, "dst": dst}

    @staticmethod
    def pgn_name(pgn: int) -> str:
        """Get human-readable name for a PGN number. Uses nmea2000 library."""
        if _HAS_N2K_LIB and _n2k_decoder:
            try:
                # Decode a dummy CAN frame to extract PGN description
                can_id = (6 << 26) | (pgn << 8)
                raw_str = f"{can_id:08X} 00 00 00 00 00 00 00 00"
                msg = _n2k_decoder.decode(raw_str)
                if msg and msg.description:
                    return msg.description
            except Exception:
                pass
        return f"PGN {pgn}"

    @classmethod
    def decode_pgn(cls, pgn: int, src: int, data: bytes) -> str:
        """Декодирование PGN в читаемую строку. Использует nmea2000 библиотеку."""
        # Try library decode via raw CAN frame format
        if _HAS_N2K_LIB and _n2k_decoder:
            try:
                # Reconstruct CAN frame hex for library decoder
                # Build CAN ID from pgn + src (priority 6, broadcast)
                if pgn < 0xF000:  # PDU1
                    can_id = (6 << 26) | (pgn << 8) | src
                else:  # PDU2
                    can_id = (6 << 26) | (pgn << 8) | src
                raw_str = f"{can_id:08X} " + " ".join(f"{b:02X}" for b in data)
                msg = _n2k_decoder.decode(raw_str)
                if msg:
                    parts = [f"[PGN {pgn} {msg.description}] Src:{src}"]
                    for f in msg.fields:
                        if f.id.startswith("reserved") or f.id.startswith("spare"):
                            continue
                        val = f.value if f.value is not None else f.raw_value
                        parts.append(f"{f.name}:{val}")
                    return " ".join(parts)
            except Exception:
                pass  # Fall through to manual decode

        # Fallback: basic PGN names
        return f"[PGN {pgn}] Src:{src} Data:{data.hex(' ').upper()}"

    @classmethod
    def _decode_via_lib(cls, parsed: Dict[str, Any]) -> Any:
        """Decode a parsed CAN frame using the nmea2000 library."""
        if not _HAS_N2K_LIB or not _n2k_decoder:
            return None
        try:
            info = parsed.get("info", {})
            data = parsed.get("data", b"")
            can_id = info.get("can_id", 0)
            raw_str = f"{can_id:08X} " + " ".join(f"{b:02X}" for b in data)
            return _n2k_decoder.decode(raw_str)
        except Exception:
            return None

    @classmethod
    def parse_raw_line(cls, line: str) -> Optional[Dict[str, Any]]:
        """Парсинг одной RAW-строки от YDNU-02. Возвращает None при ошибке."""
        # Format: hh:mm:ss.ddd R|T CANID b0 b1 b2 ...
        parts = line.strip().split()
        if len(parts) < 4:
            return None
        if parts[1] not in ('R', 'T'):
            return None
        try:
            info = cls.parse_can_id(parts[2])
            data = bytes.fromhex("".join(parts[3:]))
            decoded = cls.decode_pgn(info['pgn'], info['src'], data)
            return {"time": parts[0], "dir": parts[1], "info": info, "data": data, "decoded": decoded, "raw": line.strip()}
        except (ValueError, IndexError):
            return None


# ---------------------------------------------------------------------------
#  YDNU-02 Controller
# ---------------------------------------------------------------------------

class YDNU02Controller:
    """
    Контроллер Yacht Devices YDNU-02 с двухуровневой архитектурой команд.

    Level 1 (OS Shell):  _send_shell_command() — порт ЗАКРЫТ, echo > port.
    Level 2 (Terminal):  _send_terminal_command() — порт ОТКРЫТ, serial session.
    """

    def __init__(self, port: Optional[str] = None, debug: bool = False):
        self.port = port or self._find_port()
        self.debug = debug
        self.ser: Optional[serial.Serial] = None
        self.mode: Optional[str] = None

    @staticmethod
    def _find_port() -> str:
        """Автопоиск USB-порта YDNU-02."""
        for pattern in ["/dev/ttyACM*", "/dev/cu.usbmodem*", "/dev/tty.usbmodem*"]:
            found = sorted(glob.glob(pattern))
            if found:
                return found[0]
        return "/dev/ttyACM0"

    def _log(self, direction: str, data):
        """Debug-логирование."""
        if self.debug:
            print(f"  [{direction}] {data}")

    # --- Level 1: OS Shell ---

    def _send_shell_command(self, cmd: str):
        """
        Отправка OS Shell команды через echo > port.
        Порт должен быть ЗАКРЫТ.
        """
        self._close_terminal()

        # stty hupcl — обязательно на Linux для корректного DTR
        subprocess.run(["stty", "-F", self.port, "hupcl"], capture_output=True, timeout=5)
        self._log("SHELL", f'stty -F {self.port} hupcl')

        # echo command > port
        full_cmd = f'echo "{cmd}" > {self.port}'
        subprocess.run(full_cmd, shell=True, capture_output=True, timeout=5)
        self._log("SHELL", full_cmd)

        # Ожидание подтверждения (1-сек зелёный LED)
        time.sleep(1.5)

    # --- Level 2: Terminal Session ---

    def _open_terminal(self) -> bool:
        """Открытие serial-сессии с DTR=True."""
        if self.ser and self.ser.is_open:
            return True
        try:
            self.ser = serial.Serial(self.port, baudrate=115200, timeout=1.5, dsrdtr=True, rtscts=False)
            self.ser.dtr = True
            self.ser.rts = True
            time.sleep(0.3)
            self._log("TERM", f"Opened {self.port}")
            return True
        except Exception as e:
            print(f"[ERROR] Не удалось открыть порт {self.port}: {e}")
            return False

    def _close_terminal(self):
        """Закрытие serial-сессии."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            self._log("TERM", f"Closed {self.port}")
        self.ser = None

    def _write(self, data: bytes):
        """Отправка байт в открытый serial."""
        if self.ser and self.ser.is_open:
            self.ser.write(data)
            self.ser.flush()
            self._log("TX", data)

    def _read_response(self, duration: float = 2.0) -> str:
        """Чтение текстового ответа из serial за период duration."""
        if not self.ser:
            return ""
        chunks = []
        t0 = time.time()
        while time.time() - t0 < duration:
            if self.ser.in_waiting:
                chunk = self.ser.read(self.ser.in_waiting)
                self._log("RX", chunk)
                chunks.append(chunk.decode('utf-8', errors='ignore'))
            time.sleep(0.05)
        return "".join(chunks)

    def _send_terminal_command(self, cmd: str, wait: float = 2.0) -> str:
        """Отправка текстовой команды Service Menu и чтение ответа."""
        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Терминальная сессия не открыта. Вызовите enter_service_mode() сначала.")
        # Дочитать всё что осталось в буфере от предыдущей команды
        if self.ser.in_waiting:
            self.ser.read(self.ser.in_waiting)
        self.ser.reset_input_buffer()
        time.sleep(0.1)
        self._write(f"{cmd}\r\n".encode('ascii'))
        time.sleep(0.3)
        return self._read_response(duration=wait)

    # --- Service Mode lifecycle ---

    def enter_service_mode(self) -> str:
        """
        Вход в сервисный режим YDNU-02.
        1. Закрыть serial.
        2. echo "YDNU MODE SERVICE" > port.
        3. Открыть serial.
        4. Отправить HELP, вернуть Welcome Screen.
        """
        print(f"[YDNU02] Вход в сервисный режим ({self.port})...")
        self._send_shell_command("YDNU MODE SERVICE")

        if not self._open_terminal():
            return "[ERROR] Не удалось открыть порт после YDNU MODE SERVICE"

        self.mode = "SERVICE"
        # Читаем возможный Welcome Screen (может прийти сразу)
        welcome = self._read_response(duration=1.0)
        # Запрашиваем HELP
        self._write(b"HELP\r\n")
        welcome += self._read_response(duration=2.0)
        return welcome

    def exit_service_mode(self, target_mode: str = "AUTO") -> str:
        """
        Выход из сервисного режима.
        Из Service Menu отправляет MODE <target>, затем закрывает serial.
        """
        result = ""
        if self.ser and self.ser.is_open:
            result = self._send_terminal_command(f"MODE {target_mode.upper()}", wait=1.5)
        self._close_terminal()
        self.mode = target_mode.upper()
        print(f"[YDNU02] Режим установлен: {self.mode}")
        return result

    # --- Service Menu commands (Level 2) ---

    def service_help(self, cmd: Optional[str] = None) -> str:
        """HELP или HELP <cmd>."""
        if cmd:
            return self._send_terminal_command(f"HELP {cmd.upper()}")
        return self._send_terminal_command("HELP")

    def service_diag(self, scope: str = "ALL") -> str:
        """DIAG ALL|SETTINGS|USB_RX|USB_TX|N2K_RX|N2K_TX."""
        return self._send_terminal_command(f"DIAG {scope.upper()}", wait=10.0)

    def service_reset_settings(self) -> str:
        """RESET SETTINGS — полный заводской сброс настроек (прошивка не затрагивается)."""
        return self._send_terminal_command("RESET SETTINGS", wait=3.0)

    def service_reset_filters(self) -> str:
        """RESET FILTERS — сброс фильтров."""
        return self._send_terminal_command("RESET FILTERS", wait=2.0)

    def service_reset_mcu(self) -> str:
        """RESET MCU — перезагрузка устройства (настройки и прошивка не затрагиваются)."""
        return self._send_terminal_command("RESET MCU", wait=3.0)

    def service_reset_hardware(self) -> str:
        """
        RESET HARDWARE — откат на заводскую прошивку из EEPROM.
        ⚠️ ОПАСНО: сбрасывает ВСЕ настройки + откатывает прошивку на заводскую версию.
        """
        return self._send_terminal_command("RESET HARDWARE", wait=5.0)

    def service_print_filter(self, name: Optional[str] = None) -> str:
        """PRINT [filter_name] — вывод записей фильтра."""
        if name:
            return self._send_terminal_command(f"PRINT {name.upper()}", wait=2.0)
        return self._send_terminal_command("PRINT", wait=2.0)

    def service_set(self, key: Optional[str] = None, val: Optional[str] = None) -> str:
        """SET [key [value]] — просмотр/изменение настроек."""
        parts = ["SET"]
        if key:
            parts.append(key.upper())
        if val:
            parts.append(val)
        return self._send_terminal_command(" ".join(parts), wait=2.0)

    def _parse_welcome_screen(self, text: str) -> Dict[str, str]:
        """Парсинг Welcome Screen в структурированные данные."""
        info = {}
        for line in text.split('\n'):
            line = line.strip().strip('*').strip()
            # Firmware version : 1.75 07/08/2025
            m = re.search(r'Firmware version\s*:\s*(.+?)(?:\s{2,}|$)', line)
            if m:
                info['firmware_version'] = m.group(1).strip()
            # Serial number : 00402047
            m = re.search(r'Serial number\s*:\s*(\S+)', line)
            if m:
                info['serial_number'] = m.group(1)
            # NMEA 2000 silent : OFF
            m = re.search(r'NMEA 2000 silent\s*:\s*(\S+)', line)
            if m:
                info['silent_mode'] = m.group(1)
            # Previous mode : 0183
            m = re.search(r'Previous mode\s*:\s*(\S+)', line)
            if m:
                info['previous_mode'] = m.group(1)
        return info

    def service_backup(self, backup_dir: Optional[str] = None) -> str:
        """
        Полный бэкап состояния устройства в JSON:
        - Версия прошивки, серийник
        - Все настройки (SET)
        - Все фильтры (8 списков)
        - DIAG SETTINGS (если есть)
        """
        backup_dir = backup_dir or os.path.dirname(os.path.abspath(__file__))

        print("[BACKUP] Сбор данных устройства...")

        # Welcome Screen
        welcome = self._send_terminal_command("HELP", wait=2.0)
        device_info = self._parse_welcome_screen(welcome)
        device_info['welcome_screen_raw'] = welcome

        # Settings (HELP SET — bare SET requires an argument)
        print("[BACKUP] Настройки (HELP SET)...")
        settings_raw = self._send_terminal_command("HELP SET", wait=2.0)
        device_info['settings_raw'] = settings_raw

        # All filters (with delay between queries to prevent concatenation)
        filters = {}
        filter_names = ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                        "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]
        for fname in filter_names:
            print(f"[BACKUP] Фильтр {fname}...")
            filters[fname] = self._send_terminal_command(f"PRINT {fname}", wait=1.5)
            time.sleep(0.2)  # Пауза между запросами фильтров
        device_info['filters'] = filters

        # DIAG SETTINGS (snapshot from last diagnostic recording)
        print("[BACKUP] DIAG SETTINGS...")
        diag_settings = self._send_terminal_command("DIAG SETTINGS", wait=3.0)
        device_info['diag_settings_raw'] = diag_settings

        # Metadata
        device_info['backup_timestamp'] = datetime.now().isoformat()
        device_info['port'] = self.port

        # Save
        serial_num = device_info.get('serial_number', 'unknown')
        fw_ver = device_info.get('firmware_version', 'unknown').replace(' ', '_').replace('/', '-')
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"ydnu02_backup_{serial_num}_fw{fw_ver}_{ts}.json"
        filepath = os.path.join(backup_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(device_info, f, indent=2, ensure_ascii=False)

        print(f"[BACKUP] ✅ Сохранено: {filepath}")
        return filepath

    def service_interactive(self):
        """Интерактивный REPL-терминал Service Menu."""
        print("\n=== YDNU-02 Service Menu (введите 'exit' для выхода) ===")
        print("Команды: HELP, MODE, FILTER, PRINT, TYPE, ADD, REMOVE, SET, RESET, DIAG")
        print("Esc = возврат из data-режима. exit/quit = завершение.\n")

        while True:
            try:
                cmd = input("YDNU> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ('exit', 'quit'):
                    break
                resp = self._send_terminal_command(cmd, wait=2.0)
                if resp:
                    print(resp)
                else:
                    print("  [нет ответа]")
            except (KeyboardInterrupt, EOFError):
                print("\n[Завершение]")
                break

    # --- OS Shell quick commands (Level 1) ---

    def set_mode(self, mode: str):
        """Установка режима через OS Shell: YDNU MODE <mode>."""
        mode_upper = mode.upper()
        print(f"[YDNU02] Установка режима: {mode_upper}...")
        if self.mode == "SERVICE" and self.ser and self.ser.is_open:
            # Из сервисного режима — через terminal command
            self.exit_service_mode(target_mode=mode_upper)
        else:
            self._send_shell_command(f"YDNU MODE {mode_upper}")
            self.mode = mode_upper
        print(f"[YDNU02] Режим {mode_upper} установлен (LED должен мигнуть зелёным).")

    def set_silent(self, on: bool):
        """YDNU SILENT ON/OFF через OS Shell."""
        state = "ON" if on else "OFF"
        self._send_shell_command(f"YDNU SILENT {state}")
        print(f"[YDNU02] Silent mode: {state}")

    def start_diag_record(self):
        """YDNU DIAG — начать запись диагностики в EEPROM."""
        self._send_shell_command("YDNU DIAG")
        print("[YDNU02] Запись диагностики начата (LED = 3 сек зелёный).")

    def update_firmware(self, bin_path: str, skip_backup: bool = False,
                        progress_cb=None):
        """
        Загрузка прошивки на устройство.
        Официальный метод Yacht Devices: cp FILE.BIN > /dev/ttyACM0
        Безопасность (из документации):
        - Прерванная прошивка автоматически перезапускается при следующем включении
        - Настройки сохраняются (если README.TXT не указывает иначе)
        - Откат: hardware reset возвращает заводскую прошивку

        progress_cb: callable(stage: str, percent: int) — для отчёта в UI
        """
        if not os.path.isfile(bin_path):
            raise FileNotFoundError(f"Firmware file not found: {bin_path}")

        file_size = os.path.getsize(bin_path)

        # Валидация .BIN файла
        if file_size < 1024:
            raise ValueError(f"Firmware file too small ({file_size} bytes), likely corrupt")
        if file_size > 512 * 1024:
            raise ValueError(f"Firmware file too large ({file_size} bytes), max 512KB expected")

        # Проверяем что это не ZIP (magic bytes PK = 0x504B)
        with open(bin_path, 'rb') as f:
            magic = f.read(2)
        if magic == b'PK':
            raise ValueError("File is a ZIP archive, not a BIN. Extract .BIN from ZIP first")

        # Проверяем что порт существует
        if not os.path.exists(self.port):
            raise RuntimeError(f"Device port {self.port} not found. Is YDNU-02 connected?")

        def _progress(stage, pct):
            print(f"[FIRMWARE] {stage}: {pct}%")
            if progress_cb:
                progress_cb(stage, pct)

        # Автоматический бэкап перед прошивкой
        if not skip_backup:
            _progress("backup", 0)
            print("[FIRMWARE] Автоматический бэкап перед прошивкой...")
            self.enter_service_mode()
            backup_path = self.service_backup()
            self.exit_service_mode("AUTO")
            print(f"[FIRMWARE] Бэкап сохранён: {backup_path}")
            _progress("backup", 100)
            time.sleep(1.0)

        self._close_terminal()

        # Прошивка: пишем BIN файл напрямую в serial port чанками (с прогрессом)
        _progress("flashing", 0)
        print(f"[FIRMWARE] Загрузка прошивки {bin_path} ({file_size} bytes)...")

        CHUNK_SIZE = 4096
        written = 0
        try:
            with open(bin_path, 'rb') as src, open(self.port, 'wb') as dst:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    dst.write(chunk)
                    dst.flush()
                    written += len(chunk)
                    pct = min(100, int(written * 100 / file_size))
                    _progress("flashing", pct)
        except PermissionError:
            raise RuntimeError(f"Permission denied writing to {self.port}. Check user is in 'dialout' group")
        except OSError as e:
            raise RuntimeError(f"Failed to write firmware to {self.port}: {e}")

        _progress("done", 100)
        print(f"[FIRMWARE] Загрузка завершена ({written} bytes). LED индикация:")
        print("  🟢🟢🟢 = успех")
        print("  🔴🔴🟢 = уже установлена эта версия")
        print("  🔴🟢🔴 = файл повреждён")
        print("  Нет сигналов = файл не распознан")
        return {"written": written, "total": file_size}

    # --- NMEA Monitoring ---

    def monitor_raw(self, duration: float = 10.0, log_file: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Мониторинг CAN-шины в RAW-режиме с декодированием PGN.
        """
        self.set_mode("RAW")
        if not self._open_terminal():
            return []

        # Активация RAW-режима (AUTO mode detection: 0x30 = '0')
        self._write(b"0\n")
        time.sleep(0.5)

        results = []
        fh = open(log_file, 'w') if log_file else None

        print(f"[YDNU02] RAW мониторинг ({duration} сек)... Ctrl+C для остановки.")
        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(line)
                    if parsed:
                        results.append(parsed)
                        print(f"  {parsed['decoded']}")
                        if fh:
                            fh.write(f"{line}\n")
                    else:
                        print(f"  [RAW] {line}")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n[Остановлено]")
        finally:
            if fh:
                fh.close()
            self._close_terminal()

        return results

    def monitor_0183(self, duration: float = 10.0, log_file: Optional[str] = None):
        """Мониторинг NMEA 0183 предложений."""
        self.set_mode("0183")
        if not self._open_terminal():
            return

        # Активация 0183 (AUTO mode detection: '$' = 0x24)
        self._write(b"$\n")
        time.sleep(0.5)

        fh = open(log_file, 'w') if log_file else None
        print(f"[YDNU02] NMEA 0183 мониторинг ({duration} сек)... Ctrl+C для остановки.")
        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if line:
                        print(f"  {line}")
                        if fh:
                            fh.write(f"{line}\n")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n[Остановлено]")
        finally:
            if fh:
                fh.close()
            self._close_terminal()

    def scan_bus(self, duration: float = 5.0) -> List[Dict[str, Any]]:
        """
        Активное сканирование CAN-шины:
        RAW mode → ISO Request (PGN 60928 + 126996) → сбор ответов.
        """
        self.set_mode("RAW")
        if not self._open_terminal():
            return []

        self._write(b"0\n")
        time.sleep(0.5)

        # ISO Request for Address Claim (PGN 60928 = 0x00EE00 LE)
        self._write(b"18EAFF10 00 EE 00\r\n")
        time.sleep(0.2)

        # ISO Request for Product Info (PGN 126996 = 0x01F014 LE → 14 F0 01)
        self._write(b"18EAFF10 14 F0 01\r\n")
        time.sleep(0.2)

        devices: Dict[int, Dict[str, Any]] = {}
        results = []

        print(f"[YDNU02] Сканирование CAN-шины ({duration} сек)...")
        try:
            t0 = time.time()
            while time.time() - t0 < duration:
                if self.ser and self.ser.in_waiting:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    if not line:
                        continue
                    parsed = N2KPGNDecoder.parse_raw_line(line)
                    if parsed:
                        results.append(parsed)
                        src = parsed['info']['src']
                        pgn = parsed['info']['pgn']
                        if src not in devices:
                            devices[src] = {"src": src}
                        if pgn == 60928:
                            devices[src]["address_claim"] = parsed['decoded']
                        elif pgn == 126996:
                            devices[src]["product_info"] = parsed['decoded']
                        print(f"  {parsed['decoded']}")
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\n[Остановлено]")
        finally:
            self._close_terminal()

        # Сводная таблица
        if devices:
            print(f"\n=== Обнаружено устройств: {len(devices)} ===")
            for src, info in sorted(devices.items()):
                print(f"  Src {src:3d}: {info.get('address_claim', '')}  {info.get('product_info', '')}")
        else:
            print("\n  CAN-шина молчит: устройств не обнаружено.")

        return results


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Yacht Devices YDNU-02 USB Gateway — Controller & Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s service info           # Прошивка, серийник
  %(prog)s service shell          # Интерактивный REPL
  %(prog)s service reset          # Заводской сброс
  %(prog)s monitor raw -t 10      # RAW мониторинг 10 сек
  %(prog)s monitor scan           # Сканирование устройств на шине
  %(prog)s mode auto              # Переключение в AUTO
  %(prog)s silent off             # Выключить silent mode
        """)
    parser.add_argument("-p", "--port", type=str, help="Путь к USB-порту")
    parser.add_argument("--debug", action="store_true", help="Логирование сырых TX/RX байт")

    sub = parser.add_subparsers(dest="command", help="Группа команд")

    # --- service ---
    svc = sub.add_parser("service", help="Сервисные команды (Service Menu)")
    svc_sub = svc.add_subparsers(dest="svc_cmd")
    svc_sub.add_parser("info", help="Welcome Screen (прошивка, серийник)")
    svc_sub.add_parser("shell", help="Интерактивный REPL-терминал")
    svc_sub.add_parser("backup", help="Полный бэкап устройства в JSON")
    svc_sub.add_parser("reset", help="RESET SETTINGS — сброс настроек (прошивка не трогается)")
    svc_sub.add_parser("reset-filters", help="RESET FILTERS — сброс фильтров")
    svc_sub.add_parser("reset-mcu", help="RESET MCU — перезагрузка устройства")
    svc_sub.add_parser("reset-hardware", help="RESET HARDWARE — ⚠️ откат на ЗАВОДСКУЮ прошивку")
    svc_sub.add_parser("filters", help="Показать все фильтры")
    svc_sub.add_parser("settings", help="Показать настройки (SET)")

    svc_help = svc_sub.add_parser("help", help="HELP [command]")
    svc_help.add_argument("help_cmd", nargs="?", default=None, help="Команда для справки")

    svc_diag = svc_sub.add_parser("diag", help="Просмотр записанной диагностики")
    svc_diag.add_argument("scope", nargs="?", default="ALL",
                          choices=["ALL", "SETTINGS", "USB_RX", "USB_TX", "N2K_RX", "N2K_TX"])

    # --- monitor ---
    mon = sub.add_parser("monitor", help="Мониторинг NMEA данных")
    mon_sub = mon.add_subparsers(dest="mon_cmd")

    mon_raw = mon_sub.add_parser("raw", help="RAW CAN-кадры с декодированием PGN")
    mon_raw.add_argument("-t", "--time", type=float, default=10.0, help="Длительность (сек)")
    mon_raw.add_argument("--log", type=str, help="Записать в файл")

    mon_0183 = mon_sub.add_parser("0183", help="NMEA 0183 предложения")
    mon_0183.add_argument("-t", "--time", type=float, default=10.0, help="Длительность (сек)")
    mon_0183.add_argument("--log", type=str, help="Записать в файл")

    mon_scan = mon_sub.add_parser("scan", help="Активное сканирование устройств на шине")
    mon_scan.add_argument("-t", "--time", type=float, default=5.0, help="Длительность (сек)")

    # --- mode ---
    md = sub.add_parser("mode", help="Установка режима (OS Shell)")
    md.add_argument("target", choices=["auto", "0183", "raw", "n2k", "service"])

    # --- silent ---
    sl = sub.add_parser("silent", help="Silent mode ON/OFF")
    sl.add_argument("state", choices=["on", "off"])

    # --- diag-record ---
    sub.add_parser("diag-record", help="Начать запись диагностики в EEPROM")

    # --- firmware ---
    fw = sub.add_parser("firmware", help="Обновление прошивки (cp BIN > port)")
    fw.add_argument("bin_file", help="Путь к .BIN файлу прошивки")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    ctrl = YDNU02Controller(port=args.port, debug=args.debug)
    print(f"[YDNU02] Порт: {ctrl.port}")

    try:
        if args.command == "service":
            if not args.svc_cmd:
                print("Укажите подкоманду: info, help, shell, backup, reset, reset-mcu, reset-hardware, diag, filters, settings")
                sys.exit(1)

            welcome = ctrl.enter_service_mode()

            if args.svc_cmd == "info":
                print(welcome if welcome.strip() else "[Welcome Screen не получен]")

            elif args.svc_cmd == "help":
                if welcome.strip():
                    print(welcome)
                if args.help_cmd:
                    print(ctrl.service_help(args.help_cmd))

            elif args.svc_cmd == "shell":
                if welcome.strip():
                    print(welcome)
                ctrl.service_interactive()

            elif args.svc_cmd == "backup":
                ctrl.service_backup()

            elif args.svc_cmd == "reset":
                print("⚠️  RESET SETTINGS — сброс всех настроек (прошивка НЕ затрагивается)")
                print(ctrl.service_reset_settings())

            elif args.svc_cmd == "reset-filters":
                print(ctrl.service_reset_filters())

            elif args.svc_cmd == "reset-mcu":
                print("🔄 RESET MCU — перезагрузка устройства...")
                print(ctrl.service_reset_mcu())

            elif args.svc_cmd == "reset-hardware":
                print("")
                print("⛔ RESET HARDWARE — ОТКАТ НА ЗАВОДСКУЮ ПРОШИВКУ")
                print("   Это откатит прошивку на версию из EEPROM (заводская).")
                print("   Все настройки будут сброшены.")
                print("")
                # Автоматический бэкап перед опасной операцией
                print("[SAFETY] Автоматический бэкап перед reset-hardware...")
                backup_path = ctrl.service_backup()
                print(f"[SAFETY] Бэкап сохранён: {backup_path}")
                print("")
                confirm = input("Введите 'RESET' для подтверждения: ").strip()
                if confirm == "RESET":
                    print(ctrl.service_reset_hardware())
                    print("[YDNU02] Устройство откатывается на заводскую прошивку...")
                    print("[YDNU02] Дождитесь LED-сигналов и переподключения.")
                else:
                    print("[ОТМЕНА] Reset hardware отменён.")

            elif args.svc_cmd == "diag":
                print(ctrl.service_diag(args.scope))

            elif args.svc_cmd == "filters":
                for filt in ["GLOBAL_RX", "GLOBAL_TX", "RAW_RX", "RAW_TX",
                             "N2K_RX", "N2K_TX", "0183_RX", "0183_TX"]:
                    print(f"\n--- {filt} ---")
                    print(ctrl.service_print_filter(filt))

            elif args.svc_cmd == "settings":
                print(ctrl.service_set())

            # Не делаем exit_service_mode после reset-hardware (устройство перезагружается)
            if args.svc_cmd not in ("reset-hardware", "reset-mcu"):
                ctrl.exit_service_mode("AUTO")

        elif args.command == "monitor":
            if not args.mon_cmd:
                print("Укажите подкоманду: raw, 0183, scan")
                sys.exit(1)

            if args.mon_cmd == "raw":
                ctrl.monitor_raw(duration=args.time, log_file=args.log)
            elif args.mon_cmd == "0183":
                ctrl.monitor_0183(duration=args.time, log_file=args.log)
            elif args.mon_cmd == "scan":
                ctrl.scan_bus(duration=args.time)

        elif args.command == "mode":
            ctrl.set_mode(args.target)

        elif args.command == "silent":
            ctrl.set_silent(args.state == "on")

        elif args.command == "diag-record":
            ctrl.start_diag_record()

        elif args.command == "firmware":
            ctrl.update_firmware(args.bin_file)

    except KeyboardInterrupt:
        print("\n[Прервано]")
    finally:
        ctrl._close_terminal()
        print("[YDNU02] Завершено.")


if __name__ == "__main__":
    main()
