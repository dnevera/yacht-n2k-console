#!/usr/bin/env python3
"""
tests/test_service_mode.py
==========================
Тесты сервисного режима YDNU-02 на четырёх уровнях:

  1. TestProxyCtrlProtocol   — raw TCP тесты ctrl-порта (:4002)
  2. TestServiceModeRaceFix  — проверка race-condition фиксов
  3. TestProxyControlClient  — unit тесты ProxyControlClient
  4. TestDeviceManagerService— интеграция DeviceManager.enter/exit_service()

АРХИТЕКТУРА СЕРВИСНОГО РЕЖИМА (mini-skill)
==========================================
YDNU-02 работает в RAW mode: прокси читает ASCII NMEA фреймы и
раздаёт их всем TCP клиентам на :4001.

Для сервисного режима прокси переключается в PASSTHROUGH:

  1. Клиент подключается к :4002 (CTRL port)
  2. Клиент → прокси:  SERVICE_START\\n
  3. Прокси:  service_mode.set()   → serial_reader перестаёт форвардить
             sleep(0.15)           → ждём пока serial_reader завершит readline()
             reset_input_buffer()  → сбрасываем накопившиеся NMEA фреймы
             READY\\n              → клиент может писать команды
  4. Клиент → прокси → serial: passthrough (write/read)
  5. Клиент → прокси:  SERVICE_END\\n
  6. Прокси:  service_mode.clear() → serial_reader возобновляет broadcast
             OK\\n

КРИТИЧЕСКИЕ ДЕТАЛИ
==================
• serial.Serial(timeout=0.1)  — serial_reader быстро отдаёт управление
• conn.settimeout(0.1)        — ctrl handler не висит 2с ждя ответ
• sleep(0.15) + reset_input_buffer() — без этого serial_reader успевает
  прочитать ответ на "YDNU MODE SERVICE\\r\\n" до того как passthrough
  начнёт работать → клиент получает пустой ответ (race condition)
• Одновременно только ОДНА ctrl-сессия (service_conn_lock + service_conn)
• ProxyControlClient.__init__ захватывает _PROXY_CTRL_PORT как default arg
  в момент определения класса — патч переменной после импорта не работает.
  В тестах всегда передавай port= явно или патчи сам класс.
"""
import os
import sys
import socket
import threading
import time
import types
import importlib
import importlib.util
import unittest
from unittest.mock import MagicMock, patch, call

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# ЗАГЛУШКИ СЕРВЕРНЫХ ЗАВИСИМОСТЕЙ
# ---------------------------------------------------------------------------
# device_manager.py импортирует fastapi, ydnu02, sensors — они не установлены
# локально (только на gateway.local). Регистрируем MagicMock-модули в sys.modules
# ДО первого импорта device_manager. Это стандартный паттерн для тестирования
# серверного кода без установки всего стека.

def _mock_module(name: str, **attrs) -> MagicMock:
    """Создаёт MagicMock-модуль и регистрирует в sys.modules[name]."""
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

# fastapi — нужен только для type-hints (WebSocket, WebSocketDisconnect)
if 'fastapi' not in sys.modules:
    fa = _mock_module('fastapi')
    fa.WebSocket = MagicMock
    fa.WebSocketDisconnect = Exception
    _mock_module('fastapi.websockets')
    _mock_module('fastapi.routing')

# ydnu02 — YDNU02Controller и N2KPGNDecoder (работа с реальным железом)
if 'ydnu02' not in sys.modules:
    _mock_module('ydnu02',
                 YDNU02Controller=MagicMock,
                 N2KPGNDecoder=MagicMock)

# sensors — датчики (Gobius C, Mopeka)
if 'sensors' not in sys.modules:
    _mock_module('sensors', GobiusCSensor=MagicMock)
    _mock_module('sensors.base_sensor')
    _mock_module('sensors.gobius_sensor')
    _mock_module('sensors.mopeka_sensor')


# ===========================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ===========================================================================

def _free_port() -> int:
    """Возвращает свободный TCP-порт на localhost (bind→get→close)."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _tcp_connect(port: int, timeout: float = 3.0) -> socket.socket:
    """Создаёт TCP-соединение к 127.0.0.1:port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(('127.0.0.1', port))
    return s


def _recv_line(sock: socket.socket, timeout: float = 3.0) -> str:
    """
    Читает одну \\n-terminated строку из сокета.
    Возвращает декодированную строку без завершающего whitespace.

    ВАЖНО: Не использует makefile().readline() — после socket.timeout
    makefile входит в broken state (Python bug "cannot read from timed out
    object"). Читаем через raw recv() с буферизацией вручную.
    """
    sock.settimeout(timeout)
    buf = b''
    while b'\n' not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError('Socket closed before newline')
        buf += chunk
    return buf.split(b'\n')[0].decode('utf-8', errors='replace').strip()


def _load_proxy_module(ctrl_port: int = 0) -> types.ModuleType:
    """
    Загружает изолированную копию nmea_tcp_proxy.py.

    Каждый вызов возвращает НОВЫЙ модуль с уникальным именем — это важно
    для изоляции state между тестами (service_mode Event, clients set, etc).
    Без этого все тесты делили бы один глобальный service_mode флаг.
    """
    spec = importlib.util.spec_from_file_location(
        f'nmea_tcp_proxy_{ctrl_port}',
        os.path.join(ROOT, 'nmea_tcp_proxy.py'),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Переопределяем константы ПОСЛЕ exec_module (exec ставит дефолты)
    mod.SERIAL_PORT  = '/dev/null'
    mod.TCP_PORT     = 0           # DATA port (не нужен в ctrl тестах)
    mod.CTRL_PORT    = ctrl_port
    mod.clients      = set()
    mod.clients_lock = threading.Lock()
    mod.serial_lock  = threading.Lock()
    mod.serial_instance = None
    return mod


def _start_ctrl_server(mod: types.ModuleType, ctrl_port: int,
                       fake_serial) -> threading.Event:
    """
    Запускает TCP-сервер на ctrl_port в фоновом потоке.
    Каждое входящее соединение диспатчится в mod.handle_ctrl_client().

    Возвращает stop_event — установи его в tearDown() чтобы остановить.

    ПОЧЕМУ не используем реальный прокси целиком:
    Нам нужен только ctrl handler в изоляции. Реальный прокси запускает
    serial_reader и DATA server — это лишние зависимости в unit тестах.
    """
    mod.serial_instance = fake_serial
    ready = threading.Event()
    stop  = threading.Event()

    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', ctrl_port))
        srv.listen(5)
        srv.settimeout(0.1)   # чтобы проверять stop_event каждые 100мс
        ready.set()
        while not stop.is_set():
            try:
                conn, addr = srv.accept()
                t = threading.Thread(
                    target=mod.handle_ctrl_client,
                    args=(conn, addr),
                    daemon=True,
                )
                t.start()
            except socket.timeout:
                continue
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    ready.wait(timeout=3.0)
    return stop


def _make_fake_serial(read_data: bytes = b'') -> MagicMock:
    """
    Создаёт MagicMock симулирующий serial.Serial.

    Ключевые атрибуты:
    - is_open = True        : прокси проверяет перед write
    - in_waiting = 0        : 0 = нет данных в буфере (по умолчанию)
    - readline() → b''      : пустой ответ (тихая шина)
    - read() → read_data    : для симуляции ответа устройства
    - reset_input_buffer    : отслеживаемый вызов (race-condition тест)
    """
    fake = MagicMock()
    fake.is_open       = True
    fake.in_waiting    = 0
    fake.readline.return_value = b''
    fake.read.return_value     = read_data
    return fake


# ===========================================================================
# 1. ТЕСТЫ TCP CTRL ПРОТОКОЛА
# ===========================================================================

class TestProxyCtrlProtocol(unittest.TestCase):
    """
    Тесты raw TCP протокола ctrl-порта (:4002).

    Проверяем что handle_ctrl_client правильно:
    - отвечает READY на SERVICE_START
    - отвечает OK на SERVICE_END
    - форвардит passthrough команды в serial
    - отклоняет команды без SERVICE_START
    - отклоняет второй одновременный ctrl-клиент
    - освобождает сессию после disconnect
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

    def tearDown(self):
        self.stop.set()
        # Сбрасываем глобальное состояние между тестами
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    # -----------------------------------------------------------------------
    # Happy path
    # -----------------------------------------------------------------------

    def test_service_start_returns_ready(self):
        """
        SERVICE_START → READY.

        ПОЧЕМУ READY а не OK:
        Прокси должен сначала выполнить init-последовательность
        (sleep + reset_input_buffer) ПЕРЕД тем как разрешить passthrough.
        READY сигнализирует: "инициализация завершена, можешь писать команды".
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            resp = _recv_line(sock)
        self.assertEqual(resp, 'READY')

    def test_service_end_returns_ok(self):
        """
        SERVICE_END (после SERVICE_START) → OK.

        OK означает: прокси возобновил NMEA broadcast.
        После OK клиент должен немедленно закрыть соединение.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)          # ждём READY
            sock.sendall(b'SERVICE_END\n')
            resp = _recv_line(sock)
        self.assertEqual(resp, 'OK')

    def test_firmware_start_end(self):
        """
        FIRMWARE_START/END — alias для SERVICE_START/END.

        Используется при OTA обновлении прошивки. Тот же механизм pausing
        broadcast, тот же passthrough. Разные имена для наглядности в логах.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'FIRMWARE_START\n')
            self.assertEqual(_recv_line(sock), 'READY')
            sock.sendall(b'FIRMWARE_END\n')
            self.assertEqual(_recv_line(sock), 'OK')

    def test_passthrough_writes_to_serial(self):
        """
        После READY команды форвардятся в serial.write().

        Passthrough работает через ctrl socket: прокси читает данные
        из socket и пишет в serial_instance.write(). Направление
        serial → socket тестируется в test_serial_data_forwarded_within_200ms.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # ждём READY
            sock.sendall(b'YDNU MODE SERVICE\r\n')
            time.sleep(0.15)  # даём handler'у время записать в serial

        self.fake_ser.write.assert_called()
        written = b''.join(c.args[0] for c in self.fake_ser.write.call_args_list)
        self.assertIn(b'YDNU MODE SERVICE', written)

    def test_command_before_start_returns_error(self):
        """
        Passthrough команды без предварительного SERVICE_START → ERROR.

        Защита от случайной записи в serial_instance без захвата
        исключительной сессии. Без SERVICE_START прокси продолжает
        форвардить NMEA фреймы — passthrough конфликтовал бы с broadcast.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SOME COMMAND\n')
            resp = _recv_line(sock)
        self.assertIn('ERROR', resp)

    # -----------------------------------------------------------------------
    # Concurrency
    # -----------------------------------------------------------------------

    def test_second_session_rejected(self):
        """
        Пока активна SERVICE сессия — второй клиент получает ERROR.

        Гарантирует что в любой момент времени только ОДИН клиент
        управляет serial. service_conn_lock + service_conn = None/Socket
        реализует этот mutex на уровне прокси.
        """
        with _tcp_connect(self.ctrl_port) as first:
            first.sendall(b'SERVICE_START\n')
            _recv_line(first)  # READY — первая сессия активна

            with _tcp_connect(self.ctrl_port) as second:
                resp = _recv_line(second, timeout=3.0)
            self.assertIn('ERROR', resp)

    def test_session_freed_after_disconnect(self):
        """
        После disconnect первого клиента — второй может войти.

        handle_ctrl_client очищает service_conn = None в finally блоке
        при любом завершении (normal exit, exception, client disconnect).
        Проверяем что cleanup действительно происходит.
        """
        with _tcp_connect(self.ctrl_port) as first:
            first.sendall(b'SERVICE_START\n')
            _recv_line(first)  # READY

        time.sleep(0.3)  # ждём пока handle_ctrl_client завершится и сделает cleanup

        with _tcp_connect(self.ctrl_port) as second:
            second.sendall(b'SERVICE_START\n')
            resp = _recv_line(second)
        self.assertEqual(resp, 'READY')


# ===========================================================================
# 2. ТЕСТЫ RACE-CONDITION ФИКСА
# ===========================================================================

class TestServiceModeRaceFix(unittest.TestCase):
    """
    Проверяет три компонента race-condition фикса:

    ПРОБЛЕМА (без фикса):
    serial_reader работает в цикле: readline() → broadcast. Когда
    DeviceManager вызывает enter_service(), прокси выставляет service_mode.set().
    Но serial_reader мог быть в середине readline() с timeout=2.0s — он
    продолжает читать ещё до 2 секунд. За это время YDNU-02 отвечает на
    "YDNU MODE SERVICE\\r\\n" — но ответ читает serial_reader (не ctrl client)
    и бросает его в broadcast (NMEA фильтр отбрасывает, но ctrl client
    получает пустой ответ).

    ФИКС (три части):
    1. serial.Serial(timeout=0.1)    → serial_reader завершает readline() за ≤100мс
    2. sleep(0.15)                   → ждём пока serial_reader выйдет из readline()
    3. reset_input_buffer()          → сбрасываем всё что serial_reader успел
                                       накопить в буфере за время перехода
    4. conn.settimeout(0.1) в ctrl handler → быстрый опрос serial → client
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

    def tearDown(self):
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def test_reset_input_buffer_called_on_service_start(self):
        """
        reset_input_buffer() должен вызываться ровно ОДИН раз при SERVICE_START.

        Вызов происходит ПОСЛЕ sleep(0.15) и ДО отправки READY — в этот
        момент serial_reader уже вышел из readline(), а буфер содержит
        только стале NMEA данные которые нужно выбросить.

        Если этот вызов убрать: ctrl client получит чужие NMEA фреймы
        как "ответ" на первую команду в сервисном режиме.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # ждём READY (он отправляется ПОСЛЕ reset)

        self.fake_ser.reset_input_buffer.assert_called_once()

    def test_service_start_minimum_delay(self):
        """
        READY не должен прийти мгновенно — между SERVICE_START и READY
        прокси спит ≥100мс (фактически 150мс).

        Если READY приходит за <100мс — sleep() убрали или уменьшили,
        race window для serial_reader снова открыт.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            t0 = time.monotonic()
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            elapsed = time.monotonic() - t0

        self.assertGreaterEqual(elapsed, 0.10,
            f'READY пришёл слишком быстро ({elapsed:.3f}s) — sleep() убрали?')

    def test_serial_data_forwarded_within_200ms(self):
        """
        С conn.settimeout(0.1) данные от serial должны прийти за ≤200мс.

        Без фикса: ctrl handler делал conn.settimeout(2.0), поэтому
        даже если serial ответил мгновенно — клиент ждал до 2 секунд
        пока handler прочитает ответ через socket timeout цикл.

        Тест симулирует: устройство ответило → in_waiting > 0 → read() →
        прокси форвардит в ctrl socket.
        """
        response_payload = b'YDNU-02 Service Terminal\n'
        self.fake_ser.in_waiting = len(response_payload)
        self.fake_ser.read.return_value = response_payload

        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            sock.sendall(b'YDNU MODE SERVICE\r\n')

            sock.settimeout(1.0)
            t0 = time.monotonic()
            try:
                data = sock.recv(1024)
                elapsed = time.monotonic() - t0
            except socket.timeout:
                self.fail('Нет данных за 1с — conn.settimeout() слишком большой')

        self.assertLess(elapsed, 0.5,
            f'Данные пришли за {elapsed:.3f}s — ожидалось <0.5s при settimeout(0.1)')
        self.assertIn(b'YDNU-02', data)

    def test_service_mode_flag_set_during_session(self):
        """
        service_mode Event выставляется на время сессии и сбрасывается по окончании.

        serial_reader проверяет этот флаг в каждой итерации:
          if service_mode.is_set(): time.sleep(0.05); continue  ← уступает управление
        Без этого флага serial_reader продолжал бы читать и форвардить
        NMEA фреймы пока ctrl handler работает с устройством.
        """
        with _tcp_connect(self.ctrl_port) as sock:
            sock.sendall(b'SERVICE_START\n')
            _recv_line(sock)  # READY
            self.assertTrue(self.mod.service_mode.is_set(),
                'service_mode не установлен после SERVICE_START')
            sock.sendall(b'SERVICE_END\n')
            _recv_line(sock)  # OK
            time.sleep(0.05)  # handler делает clear() после отправки OK
            self.assertFalse(self.mod.service_mode.is_set(),
                'service_mode не сброшен после SERVICE_END')


# ===========================================================================
# 3. UNIT ТЕСТЫ ProxyControlClient
# ===========================================================================

class TestProxyControlClient(unittest.TestCase):
    """
    Unit тесты ProxyControlClient — Python класса в device_manager.py.

    ProxyControlClient — тонкий клиент для ctrl-порта. Он:
    - Открывает TCP соединение к прокси :4002
    - Отправляет SERVICE_START/END
    - Форвардит passthrough_write() в сокет
    - Читает ответы через passthrough_read_for(duration)

    КЛЮЧЕВАЯ ЛОВУШКА — default arg capture:
    class ProxyControlClient:
        def __init__(self, host=_PROXY_HOST, port=_PROXY_CTRL_PORT):
            ...
    Python вычисляет default args ОДИН РАЗ при определении класса.
    Даже если мы потом изменим dm._PROXY_CTRL_PORT — __init__ уже
    захватил старое значение (4002). Поэтому в тестах ВСЕГДА передаём
    port=self.ctrl_port явно, а не патчим переменную.
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

    def tearDown(self):
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def _pcc(self):
        """
        Создаёт ProxyControlClient подключённый к тестовому ctrl-серверу.
        port= передаётся явно чтобы обойти default-arg capture проблему.
        """
        from device_manager import ProxyControlClient
        return ProxyControlClient(port=self.ctrl_port)

    def test_enter_service_sends_start_and_gets_ready(self):
        """
        enter_service() отправляет SERVICE_START и не бросает исключение на READY.
        Если прокси отвечает ERROR (вместо READY) — бросает RuntimeError.
        """
        pcc = self._pcc()
        pcc.enter_service()   # не должен бросить исключение
        pcc.exit_service()

    def test_exit_service_sends_end_and_gets_ok(self):
        """
        exit_service() отправляет SERVICE_END и не бросает исключение на OK.
        Всегда вызывай exit_service() в finally блоке — иначе прокси
        останется в service mode и следующий вызов получит ERROR.
        """
        pcc = self._pcc()
        pcc.enter_service()
        pcc.exit_service()  # не должен бросить исключение

    def test_passthrough_write_delivers_to_serial(self):
        """
        passthrough_write() через прокси достигает serial.write().

        Путь: pcc.socket → proxy recv → serial_instance.write()
        YDNU02Controller использует этот метод для всех команд в service mode:
          ctrl._passthrough = pcc
          ctrl.enter_service_mode()  # внутри: self._write("YDNU MODE SERVICE\\r\\n")
        """
        pcc = self._pcc()
        pcc.enter_service()
        pcc.passthrough_write(b'YDNU MODE SERVICE\r\n')
        time.sleep(0.2)  # ждём пока прокси обработает и запишет в serial
        pcc.exit_service()

        written = b''.join(c.args[0] for c in self.fake_ser.write.call_args_list)
        self.assertIn(b'YDNU MODE SERVICE', written)

    def test_passthrough_read_for_collects_serial_lines(self):
        """
        passthrough_read_for(duration) собирает данные от serial за duration секунд.

        Используется в _read_response() для сбора многострочных ответов
        от YDNU-02 (например HELP выводит 10+ строк за ~300мс).
        Симулируем: serial.in_waiting > 0 → прокси делает read() → форвардит.
        """
        response_payload = b'Welcome to YDNU-02 Service Terminal\n'
        self.fake_ser.in_waiting = len(response_payload)
        self.fake_ser.read.return_value = response_payload

        pcc = self._pcc()
        pcc.enter_service()
        pcc.passthrough_write(b'YDNU MODE SERVICE\r\n')
        text = pcc.passthrough_read_for(1.0)
        pcc.exit_service()

        self.assertIn('Welcome', text, f'Получили: {text!r}')


# ===========================================================================
# 4. ИНТЕГРАЦИОННЫЕ ТЕСТЫ DeviceManager
# ===========================================================================

class TestDeviceManagerService(unittest.TestCase):
    """
    Тесты DeviceManager.enter_service() / exit_service().

    DeviceManager оборачивает ProxyControlClient в более высокоуровневый
    паттерн:
      1. _pause_event.set()    → bus worker прекращает читать :4001
      2. sleep(0.2)            → ждём завершения текущего readline()
      3. pcc = ProxyControlClient()  ← наш _TestPCC (перехватываем)
      4. pcc.enter_service()   → SERVICE_START → READY
      5. ctrl._passthrough=pcc → YDNU02Controller теперь пишет через pcc
      6. ctrl.enter_service_mode() → "YDNU MODE SERVICE\\r\\n" → ответ
      7. self._state = "SERVICE"
      ...позже при exit_service():
      8. ctrl.exit_service_mode() → "MODE RAW\\r\\n"
      9. pcc.exit_service()    → SERVICE_END → OK
      10. _pause_event.clear() → bus worker переподключается к :4001

    ПАТЧ ProxyControlClient (а не _PROXY_CTRL_PORT):
    default arg уже захвачен при определении класса. Патчим сам класс:
      dm.ProxyControlClient = _TestPCC  (наш subclass с port=ctrl_port)
    Это работает потому что внутри _raw_locked_operation написано:
      pcc = ProxyControlClient()   ← смотрит в dm.__dict__['ProxyControlClient']
    Т.е. поиск имени происходит при ВЫЗОВЕ, а не при определении функции.

    YDNU02Controller — MagicMock (из заглушки ydnu02 модуля).
    Устанавливаем возвращаемые значения через mock_ctrl.
    """

    def setUp(self):
        self.ctrl_port = _free_port()
        self.fake_ser  = _make_fake_serial()
        self.mod       = _load_proxy_module(ctrl_port=self.ctrl_port)
        self.stop      = _start_ctrl_server(self.mod, self.ctrl_port, self.fake_ser)

        import device_manager as dm
        _port = self.ctrl_port
        _orig_cls = dm.ProxyControlClient

        class _TestPCC(_orig_cls):
            """
            Subclass ProxyControlClient, всегда подключается к тестовому порту.
            Используется как замена dm.ProxyControlClient на время теста.
            """
            def __init__(self_):          # noqa: N805
                super().__init__(port=_port)

        self._orig_pcc_cls = _orig_cls
        dm.ProxyControlClient = _TestPCC  # патчим класс в модуле

    def tearDown(self):
        import device_manager as dm
        dm.ProxyControlClient = self._orig_pcc_cls  # восстанавливаем
        self.stop.set()
        self.mod.service_mode.clear()
        with self.mod.service_conn_lock:
            self.mod.service_conn = None

    def _make_manager_with_mock_ctrl(self,
                                     enter_ret: str = 'ok',
                                     exit_ret:  str = 'RAW mode.\r\n'):
        """
        Создаёт DeviceManager с pre-installed mock YDNU02Controller.

        Без мока enter_service_mode() пытается реально говорить с железом
        через passthrough (которого нет в unit тестах). Мы инжектируем
        готовый ctrl объект напрямую в mgr._ctrl, минуя реальный init.
        """
        import device_manager as dm
        mgr = dm.DeviceManager(port='127.0.0.1:4001')
        mock_ctrl = MagicMock()
        mock_ctrl.enter_service_mode.return_value = enter_ret
        mock_ctrl.exit_service_mode.return_value  = exit_ret
        mgr._ctrl = mock_ctrl
        return mgr

    # -----------------------------------------------------------------------
    # State machine
    # -----------------------------------------------------------------------

    def test_enter_service_returns_service_state(self):
        """
        enter_service() → {status:'ok', state:'SERVICE', welcome:'...'}

        'welcome' — это ответ YDNU-02 на YDNU MODE SERVICE (многострочный
        HELP текст). Фронтенд показывает его в терминале при нажатии Enter.
        """
        mgr = self._make_manager_with_mock_ctrl(enter_ret='YDNU-02 Service Terminal\n')
        result = mgr.enter_service()

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['state'],  'SERVICE')
        self.assertIn('welcome', result)

    def test_exit_service_returns_idle_state(self):
        """
        exit_service() → {status:'ok', state:'IDLE', response:'RAW mode.\\r\\n'}

        'RAW mode.' — подтверждение от YDNU-02 что устройство вернулось
        в нормальный режим. После этого NMEA broadcast возобновляется.
        """
        mgr = self._make_manager_with_mock_ctrl()
        mgr.enter_service()
        result = mgr.exit_service()

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['state'],  'IDLE')

    def test_get_state_reflects_enter_exit(self):
        """
        get_state() корректно отслеживает SERVICE→IDLE переходы.

        Это значение используется:
        - GET /api/service/state → {state: 'SERVICE'|'IDLE'}
        - Frontend #svc-state бейдж (зелёный/серый)
        """
        mgr = self._make_manager_with_mock_ctrl()

        self.assertEqual(mgr.get_state(), 'IDLE')    # начальное состояние
        mgr.enter_service()
        self.assertEqual(mgr.get_state(), 'SERVICE')
        mgr.exit_service()
        self.assertEqual(mgr.get_state(), 'IDLE')    # вернулись в норму

    def test_concurrent_enter_serialized_by_lock(self):
        """
        Два одновременных enter_service() на ОДНОМ менеджере сериализуются
        через _service_lock. Ни один не должен получить 'another control
        session is active'.

        КАК РАБОТАЕТ:
        DeviceManager._service_lock = threading.Lock()
        enter_service() вызывается через _raw_locked_operation() который
        внутри делает with self._service_lock: — второй вызов ждёт.

        Прокси защита (service_conn_lock) — второй уровень, срабатывает
        только если два РАЗНЫХ менеджера пытаются одновременно. На одном
        менеджере это предотвращает _service_lock.

        BARRIER нужен чтобы оба потока точно стартовали конкурентно,
        иначе первый успеет завершиться до того как второй начнёт.
        """
        mgr     = self._make_manager_with_mock_ctrl()
        results = []
        errors  = []
        lock    = threading.Lock()
        barrier = threading.Barrier(2)

        def one_cycle():
            barrier.wait()  # оба потока стартуют одновременно
            try:
                r = mgr.enter_service()
                time.sleep(0.05)  # держим сессию чтобы создать реальную конкуренцию
                mgr.exit_service()
                with lock:
                    results.append(r)
            except Exception as e:
                with lock:
                    errors.append(e)

        t1 = threading.Thread(target=one_cycle, daemon=True)
        t2 = threading.Thread(target=one_cycle, daemon=True)
        t1.start(); t2.start()
        t1.join(timeout=15); t2.join(timeout=15)

        self.assertEqual(errors, [],
            f'Конкурентный enter_service бросил исключения: {errors}')
        self.assertEqual(len(results), 2,
            f'Ожидали 2 результата, получили {len(results)}: {results}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
