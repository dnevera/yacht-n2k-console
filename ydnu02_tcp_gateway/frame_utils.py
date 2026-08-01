"""Frame format utilities and gateway protocol reference for YDNU-02 TCP Gateway.
=================================================================================

YDNU-02 TCP GATEWAY — ПОЛНАЯ ДОКУМЕНТАЦИЯ ПРОТОКОЛА
====================================================

Этот модуль — единое каноническое место для документации всего протокола шлюза:
  • Физический порт /dev/ttyACM0 (YDNU-02 CAN_FRAME_ASCII)
  • DATA порт :4001 (двунаправленный N2K-хаб для всех TCP-клиентов)
  • CTRL порт :4002 (эксклюзивный сервисный терминал / прошивка)

Реализация: data_hub.py, ctrl_handler.py, serial_reader.py, ydnu02_tcp_gateway.py


══════════════════════════════════════════════════════════════════
ЧАСТЬ I. ФИЗИЧЕСКИЙ ПОРТ /dev/ttyACM0 (YDNU-02 CAN_FRAME_ASCII)
══════════════════════════════════════════════════════════════════

Параметры порта:
  Устройство : /dev/ttyACM0 (USB CDC ACM, Linux) / /dev/cu.usbmodemXXX (macOS)
  Baud rate  : 115200
  Frame      : 8N1 (8 data bits, no parity, 1 stop bit)
  DTR        : True (обязательно — YDNU-02 использует DTR для обнаружения хоста)
  Timeout    : 0.1s (чтение не блокируется дольше 100ms)
  Режим      : CAN_FRAME_ASCII (текстовый протокол, собственный YDNU-02)


──────────────────────────────────────────────────────────────────
1.1 Инициализационная последовательность (RAW mode)
──────────────────────────────────────────────────────────────────

После открытия порта serial_reader.py выполняет:

  Шаг 1 → serial.write(b"YDNU MODE RAW\r\n")   # перевод в CAN_FRAME_ASCII режим
  Шаг 2   time.sleep(2.0)                        # ждём ответа YDNU-02
  Шаг 3   ser.read(in_waiting)                   # читаем и отбрасываем ответ
  Шаг 4 → serial.write(b"0\n")                  # сброс pending prompt
  Шаг 5   time.sleep(0.5)
  Шаг 6   serial_ready.set()                     # флаг "порт готов"
  Шаг 7   send_iso_request()                     # ISO Request на шину

После инициализации YDNU-02 начинает передавать CAN-фреймы в формате NMEA_LINE_RE.


──────────────────────────────────────────────────────────────────
1.2 Переход в SERVICE MODE (DTR toggle)
──────────────────────────────────────────────────────────────────

Обычный serial.write("YDNU MODE SERVICE") ИГНОРИРУЕТСЯ при открытом порту.
YDNU-02 реагирует только на DTR low→high (закрытие и открытие порта).

Последовательность (ctrl_handler.py::enter_service_mode_on_device):

  Шаг 1   serial.close()                                  # DTR → low
  Шаг 2   stty -F /dev/ttyACM0 115200 raw -echo hupcl    # настройка порта через OS
  Шаг 3 → echo 'YDNU MODE SERVICE' > /dev/ttyACM0        # запись в устройство при DTR=low
  Шаг 4   time.sleep(0.15)                                # ждём аппаратного переключения
  Шаг 5   serial.open(dsrdtr=True, dtr=True)              # DTR → high → YDNU-02 переключается
  Шаг 6   ← YDNU-02 выводит сервисное приглашение        # интерактивный терминал

Возврат из SERVICE MODE:

  → serial.write(b"MODE RAW\r\n")   # команда возврата
    time.sleep(0.5)                  # ждём перезапуска CAN-режима
  → serial_reader продолжает чтение NMEA_LINE_RE фреймов


──────────────────────────────────────────────────────────────────
1.3 Форматы CAN-фреймов (физический порт ↔ DataHub)
──────────────────────────────────────────────────────────────────

ВСЕ форматы используют ASCII, строки разделяются переводом строки.

  Формат A — NMEA_LINE_RE  (YDNU-02 → Host, чтение из /dev/ttyACM0):
  ──────────────────────────────────────────────────────────────────
    HH:MM:SS.mmm D XXXXXXXX XX XX ... XX\n
    │            │ │        └── DATA bytes, hex uppercase, пробел-разделитель
    │            │ └── CAN ID, 8 символов hex uppercase (без 0x)
    │            └── D: R=Receive (кто-то на шине → YDNU-02) / T=Transmit (YDNU-02 → шину, эхо)
    └── timestamp (текущее время YDNU-02 часы:минуты:секунды.мс)
    Терминатор: \n (только LF)

    Regex: NMEA_LINE_RE = rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"

    Примеры:
      18:48:47.064 R 09FD025C 00 67 00 00 00 FF 00 00\n  ← Gobius C PGN 127490, SA=92 (0x5C)
      18:48:47.064 T 19F01440 C0 86 15 05 00 EE 00\n     ← YDNU-02 эхо Product Info PGN 126996, SA=64


  Формат B — TX_LINE_RE  (Host → YDNU-02, запись в /dev/ttyACM0):
  ──────────────────────────────────────────────────────────────────
    XXXXXXXX XX XX ... XX\r\n
    │        └── DATA bytes, hex uppercase, пробел-разделитель
    └── CAN ID, 8 символов hex uppercase (без 0x)
    Терминатор: \r\n (CRLF — ОБЯЗАТЕЛЬНО, только \n игнорируется)

    Regex: TX_LINE_RE = rb"^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r?\n$"

    Примеры:
      18EAFFFE 00 EE 00\r\n            ← ISO Request PGN 59904 для PGN 60928, DA=255 (broadcast)
      18EAFFFE 14 F0 01\r\n            ← ISO Request PGN 59904 для PGN 126996, DA=255 (broadcast)
      18EAFFC8 00 00 00 00 00 E8 FF 00\r\n  ← ISO Address Claim PGN 60928, SA=200 (0xC8)
      09FF04C8 05 00 02 91 7E FF FF 00\r\n  ← CPU Temp PGN 130312, SA=200


    ОШИБОЧНЫЕ форматы (молча игнорируются YDNU-02):
      18:48:47.064 R 18EAFFC8 00\n    ← таймштамп/флаг запрещены
      18EAFFC8 00 EE 00\n             ← только \n без \r — не работает
      18EAFFC8 00 EE 00               ← нет терминатора


──────────────────────────────────────────────────────────────────
1.4 Преобразование входящих фреймов в TX_LINE_RE для ser.write()
──────────────────────────────────────────────────────────────────

При n2k_serial_tx_enabled=True DataHub конвертирует любой входящий фрейм
перед передачей в ser.write(). Оба пути дают формат B (TX_LINE_RE):

  Из формата A (NMEA_LINE_RE):
    parts = line.strip().split(b' ')
    # parts[0]="HH:MM:SS.mmm"  ← отбрасываем
    # parts[1]="R" или "T"     ← отбрасываем
    # parts[2]="XXXXXXXX"      ← CAN ID
    # parts[3:]=[XX,...]        ← DATA bytes
    raw_tx = parts[2] + b' ' + b' '.join(parts[3:]) + b'\r\n'

  Из формата B (TX_LINE_RE):
    parts = raw.rstrip(b'\r\n').split(b' ')
    # parts[0]="XXXXXXXX"   ← CAN ID
    # parts[1:]=[XX,...]    ← DATA bytes
    raw_tx = parts[0] + b' ' + b' '.join(parts[1:]) + b'\r\n'


──────────────────────────────────────────────────────────────────
1.5 normalize_frame() — T→R нормализация для деcoдеров
──────────────────────────────────────────────────────────────────

nmea2000-lib и ha-nmea2000 принимают ТОЛЬКО R-фреймы (флаг 'T' вызывает
возврат None из decode()). Поэтому:

    normalize_frame(b"18:48:47.064 T 19F01440 C0 86\r\n")
    → b"18:48:47.064 R 19F01440 C0 86\n"

Это безопасно: в CAN-данных не может встретиться " T " как отдельный токен
(данные — двухсимвольные hex-пары, не однобуквенный символ между пробелами).


══════════════════════════════════════════════════════════════════
ЧАСТЬ II. DATA ПОРТ :4001 — ДВУНАПРАВЛЕННЫЙ N2K TCP ХАБ
══════════════════════════════════════════════════════════════════

Клиенты: Home Assistant (nmea2000 IOClient), Signal K, ydnu02-web (Monitor tab),
         N2KDevice SA=200 (виртуальный гейтвей, loop-back к :4001).
Максимум клиентов: не ограничено.
Протокол клиента: NMEA_LINE_RE (формат A) или TX_LINE_RE (формат B).
Кодировка: ASCII.
Терминатор строки: \n (клиент может слать \r\n — нормализуется при чтении).


──────────────────────────────────────────────────────────────────
2.1 Матрица маршрутизации фреймов
──────────────────────────────────────────────────────────────────

  Источник              → Назначение                     Условие
  ─────────────────────────────────────────────────────────────────
  /dev/ttyACM0          → все TCP-клиенты :4001          всегда (через SerialReader)
  TCP-клиент :4001      → все ДРУГИЕ TCP-клиенты :4001   exclude=sender
  TCP-клиент :4001      → /dev/ttyACM0                   n2k_serial_tx_enabled=True
                                                           И NOT service_mode
  /dev/ttyACM0          → /dev/ttyACM0                   НИКОГДА (петля невозможна —
                                                           SerialReader→broadcast() минует
                                                           handle_client())


──────────────────────────────────────────────────────────────────
2.2 Onboarding — последовательность при подключении клиента
──────────────────────────────────────────────────────────────────

При каждом новом TCP-подключении к :4001 DataHub.handle_client() выполняет:

  Шаг 1  Клиент добавляется в множество clients (clients_lock)

  Шаг 2  send_iso_request() — rate-limited (≥5s между вызовами):
    → serial: b"18EAFFFE 00 EE 00\r\n"   (PGN 59904 req PGN 60928 от всех, DA=0xFF)
    → serial: b"18EAFFFE 14 F0 01\r\n"   (PGN 59904 req PGN 126996 от всех, DA=0xFF)
    → TCP broadcast: fmt_frame("18EAFFFE", b'\x00\xee\x00')  ← виртуальные устройства отвечают
    → TCP broadcast: fmt_frame("18EAFFFE", b'\x14\xf0\x01')

  Шаг 3  announce_all_devices(delay=0.6s) — двухфазный анонс:

    ФАЗА 1 (немедленно):
      → TCP broadcast: PGN 60928 (ISO Address Claim) для SA=64 (YDNU-02)
      → TCP broadcast: PGN 60928 (ISO Address Claim) для SA=200 (TCP-GW)
      HA decoder: source_to_iso_name[64]=IsoName(402047), source_to_iso_name[200]=IsoName(902047)

    ФАЗА 2 (через 0.6s, Timer):
      → TCP broadcast: PGN 126996 (Product Info) для SA=64 (YDNU-02)
      → TCP broadcast: PGN 126996 (Product Info) для SA=200 (TCP-GW)
      HA decoder: source_iso_name уже заполнен → уникальный hash per device

    ПОЧЕМУ 0.6s задержка:
      Если PGN 126996 приходит раньше PGN 60928 — source_to_iso_name[SA]=None →
      decode() молча возвращает None → HA не создаёт устройство.
      Задержка гарантирует, что HA построил карту адресов перед Product Info.

  Шаг 4  Основной цикл чтения (непрерывно):
    buf: bytes = b""
    while True:
        chunk = conn.recv(4096)
        # парсинг по \n → обработка строк:

        if NMEA_LINE_RE.match(line):     ← формат A от клиента
            broadcast(line, exclude=conn)
            если n2k_serial_tx_enabled: ser.write(raw_tx)   # формат B

        elif TX_LINE_RE.match(raw):      ← формат B от клиента
            broadcast(fmt_frame(can_id, data), exclude=conn) # конверт в формат A
            если n2k_serial_tx_enabled: ser.write(raw_tx)   # формат B


──────────────────────────────────────────────────────────────────
2.3 Периодические широковещательные сообщения (N2KDevice SA=200)
──────────────────────────────────────────────────────────────────

N2KDevice (ydnu02_gateway_device.py) подключается к :4001 как обычный TCP-клиент
и периодически транслирует через него N2K фреймы в формате NMEA_LINE_RE:

  PGN       Интервал  CAN ID prefix  Описание
  ────────────────────────────────────────────────────────────────
  60928     1 раз     18EEFFC8       ISO Address Claim SA=200 (при старте + ISO Request)
  126996    60s       19F014C8       Product Info SA=200 (FastPacket, 19 фреймов)
  126993    10s       19F11100       Heartbeat SA=200 (управляется N2KDevice lib)
  130312    3s*       09FF04C8       CPU Temperature SA=200 (PGN 130312, 8 байт)

  * интервалы 3s (TCP) и 5s (serial) настраиваются через GatewaySettings:
    n2k_tcp_temp_interval_s    (default 3.0)
    n2k_serial_temp_interval_s (default 5.0)


──────────────────────────────────────────────────────────────────
2.4 ISO Request — инициирование физических устройств
──────────────────────────────────────────────────────────────────

ISO Request (PGN 59904) — стандартный N2K механизм запроса у всех устройств
на шине их идентификации. Формат данных: 3 байта LE PGN.

  Запрос PGN 60928 (ISO Address Claim от всех):
    CAN ID: 18EAFFFE   (Priority=6, PGN=59904=0xEA00, DA=0xFF=broadcast, SA=0xFE=unclaimed)
    DATA:   00 EE 00   (PGN 60928 = 0x00EE00 в Little-Endian)
    serial: b"18EAFFFE 00 EE 00\r\n"

  Запрос PGN 126996 (Product Info от всех):
    CAN ID: 18EAFFFE
    DATA:   14 F0 01   (PGN 126996 = 0x01F014 в Little-Endian)
    serial: b"18EAFFFE 14 F0 01\r\n"

  Rate limit: не чаще 1 раза в 5 секунд (_ISO_REQUEST_MIN_INTERVAL).
  Условие: serial_ready.is_set() AND NOT service_mode.is_set().


──────────────────────────────────────────────────────────────────
2.5 CAN ID — структура 29-битного J1939 Extended Frame
──────────────────────────────────────────────────────────────────

  Bits 28-26: Priority   (3 бит, 0=высший, 7=низший)
  Bit  25:    Reserved   (= 0)
  Bit  24:    DataPage   (= 0 для большинства N2K PGN)
  Bits 23-16: PF         (PDU Format)
               PF < 0xF0 → Peer-to-Peer: bits 15-8 = Destination Address (DA)
               PF ≥ 0xF0 → Broadcast:    bits 15-8 = Group Extension
  Bits 15-8:  PS         (PDU Specific — DA или Group Extension)
  Bits 7-0:   SA         (Source Address, 0–253; 254=unclaimed; 255=global)

  Декодирование:  get_pgn_sa("18EAFFFE") → (59904, 254)
                  get_pgn_sa("09FF04C8") → (130312, 200)
                  get_pgn_sa("18EEFFC8") → (60928, 200)
                  get_pgn_sa("09FD025C") → (127490, 92)

  Известные Source Address:
    SA=64  (0x40)  — YDNU-02 физическое устройство (unique_number=402047)
    SA=92  (0x5C)  — Gobius C (unique_number=697207)
    SA=200 (0xC8)  — виртуальный TCP-GW (unique_number=902047)
    SA=254 (0xFE)  — unclaimed (используется в ISO Request как "от кого угодно")
    SA=255 (0xFF)  — global broadcast destination


══════════════════════════════════════════════════════════════════
ЧАСТЬ III. CTRL ПОРТ :4002 — СЕРВИСНЫЙ ТЕРМИНАЛ И ПРОШИВКА
══════════════════════════════════════════════════════════════════

Клиент: исключительно ydnu02-web (через ProxyControlClient / device_manager/).
Максимум клиентов: ОДИН одновременно.
Протокол: line-oriented UTF-8, команды разделяются \n.
Ответы сервера: строки с \r\n (ctrl_send → f"{msg}\r\n").


──────────────────────────────────────────────────────────────────
3.1 Команды CTRL порта (полный список)
──────────────────────────────────────────────────────────────────

  Команда клиента    Ответ сервера    Описание
  ─────────────────────────────────────────────────────────────────────────────
  SERVICE_START\n    READY\r\n        Начало сервисного сеанса:
                                       1. service_mode.set() → SerialReader паузирует
                                       2. DTR toggle → YDNU-02 переходит в service terminal
                                       3. Последующие строки пробрасываются в /dev/ttyACM0

  FIRMWARE_START\n   READY\r\n        Начало сеанса прошивки:
                                       1. service_mode.set() → SerialReader паузирует
                                       2. serial.reset_input_buffer()
                                       3. RAW passthrough — DTR НЕ toggle, режим не меняется
                                       4. Последующие строки пробрасываются в /dev/ttyACM0

  <cmd>\n            (ответ YDNU-02)  Во время SERVICE/FIRMWARE сеанса:
                                       cmd_bytes отправляется в ser.write(raw)
                                       Данные из serial.in_waiting → conn.sendall() (poll 100ms)

  SERVICE_END\n      OK\r\n           Завершение SERVICE сеанса:
                                       1. serial.write(b"MODE RAW\r\n")
                                       2. time.sleep(0.5)
                                       3. service_mode.clear() → SerialReader возобновляет

  FIRMWARE_END\n     OK\r\n           Завершение FIRMWARE сеанса:
                                       1. service_mode.clear() (без MODE RAW)

  <любое вне сеанса>  ERROR: not in service mode\r\n

  (второй клиент)    ERROR: another control session is active\r\n
                                       conn.close() немедленно


──────────────────────────────────────────────────────────────────
3.2 Диаграмма состояний CTRL сессии
──────────────────────────────────────────────────────────────────

  IDLE ──SERVICE_START──► SERVICE ──SERVICE_END──► IDLE
       ──FIRMWARE_START──► FIRMWARE──FIRMWARE_END──► IDLE
       ──disconnect──────► IDLE (auto SERVICE_END если был активен)

  В состоянии SERVICE:
    • SerialReader: service_mode.is_set() → sleep(0.05) вместо readline()
    • DataHub:      TCP→TCP broadcast продолжает работать
    • serial write: команды клиента → ser.write(cmd_bytes + b"\n")
    • serial read:  ser.read(in_waiting) каждые 100ms (socket timeout) → клиенту

  В состоянии FIRMWARE:
    • Идентично SERVICE, но без DTR toggle (YDNU-02 остаётся в RAW режиме)
    • Используется для bflash / firmware update утилит


──────────────────────────────────────────────────────────────────
3.3 Исключительность CTRL сессии и взаимодействие с SerialReader
──────────────────────────────────────────────────────────────────

  ГАРАНТИЯ MUTEX:
    service_conn_lock защищает service_conn и переходы service_mode.
    Если service_mode.is_set() при подключении нового клиента — немедленный отказ.

  ВЛИЯНИЕ НА DATA ПОРТ :4001:
    • Физический serial читается ТОЛЬКО SerialReader.
    • Пока service_mode.is_set(), SerialReader не вызывает broadcast().
    • TCP→TCP forwarding и виртуальный N2KDevice (SA=200) продолжают работать.
    • N2K шина "замолкает" для TCP-клиентов на время сервисного сеанса.


══════════════════════════════════════════════════════════════════
ЧАСТЬ IV. ДИАГНОСТИЧЕСКИЕ КОМАНДЫ
══════════════════════════════════════════════════════════════════

  # DATA порт — мониторинг всего N2K трафика:
  nc <gateway-host> 4001 | head -30

  # Только SA=200 (виртуальный гейтвей):
  nc <gateway-host> 4001 | grep 'C8'

  # Только ISO Claim (PGN 60928):
  nc <gateway-host> 4001 | grep 'EEFF'

  # Только CPU Temp (PGN 130312):
  nc <gateway-host> 4001 | grep '09FF04'

  # Проверка CTRL handshake:
  python3 -c "
  import socket
  s = socket.create_connection(('<host>', 4002))
  s.sendall(b'SERVICE_START\n')
  print(s.recv(64))   # → b'READY\r\n'
  s.sendall(b'SERVICE_END\n')
  print(s.recv(64))   # → b'OK\r\n'
  s.close()
  "

  # Ручная ISO Request в шину:
  python3 -c "
  import socket
  s = socket.create_connection(('<host>', 4001))
  s.sendall(b'18EAFFFE 00 EE 00\r\n')  # запрос ISO Claim от всех
  s.close()
  "

  # Декодинг CAN ID:
  python3 -c "
  from ydnu02_tcp_gateway.frame_utils import get_pgn_sa
  for cid in ['18EAFFFE','18EEFFC8','09FF04C8','09FD025C','19F014C8']:
      pgn, sa = get_pgn_sa(cid)
      print(f'{cid} → PGN={pgn}, SA={sa}')
  "

  # Проверка normalize_frame:
  python3 -c "
  from ydnu02_tcp_gateway.frame_utils import normalize_frame
  r = normalize_frame(b'18:48:47.064 T 19F01440 C0 86\r\n')
  assert b' R ' in r and r.endswith(b'\n') and not r.endswith(b'\r\n')
  print('OK:', r)
  "
"""
import re
from typing import Tuple, Union
from ydnu02.pgn_decoder import N2KPGNDecoder


# RX format regex: "HH:MM:SS.mmm R XXXXXXXX XX XX ...\n"
NMEA_LINE_RE = re.compile(
    rb"^\d{2}:\d{2}:\d{2}\.\d{3} [RT] [0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\n$"
)

# TX format regex: "XXXXXXXX XX XX ...\r?\n"
TX_LINE_RE = re.compile(
    rb"^[0-9A-Fa-f]{8}( [0-9A-Fa-f]{2})+\r?\n$"
)


def normalize_frame(line: bytes) -> bytes:
    """Normalize a YDNU-02 ASCII line into canonical RX (R-frame) format.

    Replaces outgoing ' T ' flags with ' R ' so nmea2000 decoders process them correctly,
    and strips trailing carriage returns while guaranteeing a single '\n' terminator.

    Args:
        line: Raw line bytes from serial or socket.

    Returns:
        Normalized ASCII line bytes ending with b'\\n'.

    Skill — verify T-to-R conversion::

        assert normalize_frame(b'00:00:00.000 T 18EEFF40 00\\r\\n') == b'00:00:00.000 R 18EEFF40 00\\n'
    """
    if b" T " in line:
        line = line.replace(b" T ", b" R ", 1)
    return line.rstrip(b"\r\n") + b"\n"


def fmt_frame(can_id_hex: str, data: bytes) -> bytes:
    """Format raw CAN data as a YDNU-02 ASCII RX-format text line.

    Output format: 00:00:00.000 R XXXXXXXX XX XX XX ...\n
    """
    return f'00:00:00.000 R {can_id_hex} {" ".join(f"{b:02X}" for b in data)}\n'.encode('ascii')


def get_pgn_sa(can_id: Union[bytes, str]) -> Tuple[int, int]:
    """Decode (PGN, SourceAddress) from an 8-char hex CAN ID.

    Delegates to N2KPGNDecoder.parse_can_id() — the single canonical
    CAN ID bit-math implementation — to prevent divergence.

    Args:
        can_id: 8-char hex CAN ID as str or ASCII bytes (e.g. b'18EEFF5C').

    Returns:
        (pgn, sa) tuple.
    """
    can_id_str = can_id.decode("ascii") if isinstance(can_id, bytes) else can_id
    parsed = N2KPGNDecoder.parse_can_id(can_id_str)
    return parsed["pgn"], parsed["src"]
