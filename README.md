# Интеграция NMEA 2000 + YDNU-02 + Gobius C + Mopeka BLE (Raspberry Pi 5)

## 📌 Обзор архитектуры

Система объединяет датчики уровня баков и marine-электронику для Home Assistant и Victron Venus OS:

```
[ Gobius C (Radar/US) ] ── (NMEA 2000) ──┐
                                         ├──> [ YDNU-02 USB ] ──> (/dev/ttyACM0) ──> [ Signal K Server ] ──> Home Assistant / Victron
[ Mopeka Pro 200 BLE  ] ── (BLE Advert) ─┴────────────────────> [ Bluetooth D-Bus] ──┘
```

---

## 🛠️ Базовые параметры оборудования

1. **Физический слой NMEA 2000:**
   * Сопротивление шины между CAN-H и CAN-L: **60 Ом** (2 терминатора по 120 Ом).
   * Напряжение питания CAN-шины: **12V DC**.

2. **Yacht Devices YDNU-02 USB Gateway:**
   * Устройство на Pi 5: `/dev/ttyACM0`.
   * Режимы работы: `AUTO`, `0183`, `RAW`, `N2K`.
   * Диагностика сервисного режима: [ydnu02.py](file:///Users/denn/Develop/3dprint/ha/nmea2000/ydnu02.py).

3. **Датчик Gobius C NMEA 2000:**
   * Приложение Gobius C (Bluetooth):
     - `NMEA 2000 State: 1` (Включен).
     - `Device Instance: 0`, `Fluid Instance: 0` (Бак 1).
     - `Fluid Type`: Water (1) или Fuel (0).
     - **Обязательная калибровка:** Выполнить калибровку бака в приложении.

---

## 💻 Управление YDNU-02 через сервисный режим

Для запуска диагностики YDNU-02 из консоли Pi 5:

```bash
# 1. Убедиться, что порт свободен
pkill -f signalk

# 2. Запустить скрипт диагностики YDNU-02
python3 ha/nmea2000/ydnu02.py
```

### Набор сервисных команд:
- `HELP` — Вывод меню помощи.
- `STATUS` — Диагностика CAN-шины (RX/TX counters, Bus-ON).
- `INFO` — Ревизия и версия прошивки.
- `MODE AUTO` / `MODE 0183` / `MODE RAW` — Установка режима работы.
- `YD:RESET` — Заводской сброс настроек EEPROM.

---

## 🚀 Запуск Signal K в Docker

После проверки YDNU-02 запустите контейнер Signal K:

```bash
cd ha/nmea2000
docker compose up -d
```

Веб-интерфейс Signal K: `http://192.168.68.56:3000`
