---
name: linktap-ha-setup
description: >-
  Информация по интеграции LinkTap Q1 (4-канальный клапан) и датчиков Ecowitt WH51
  в Home Assistant, совместимость с Gardena и локальный режим работы.
---

# LinkTap Q1 + Home Assistant — Сад на 3-4 шланга

## Инфраструктура

```
HA (bambuddy.local) <───[LAN/WLAN]───► LinkTap Gateway ───[868 MHz]───► LinkTap Q1 (4 выхода)
                         └───[Webhook]───► Ecowitt Gateway ───[RF]───► 3x Ecowitt WH51 (влажность)
```

---

## Комплектующие и совместимость с Gardena

1.  **LinkTap Q1 Set (with Gateway)** (арт. B0CQRLTTPM):
    *   Входной патрубок: 1" BSP (в комплекте переходники на 3/4" и 1/2").
    *   Выходные патрубки: 4 шт. с внешней резьбой **3/4" BSP**.
    *   **Совместимость:** Для подключения шлангов Gardena с быстроразъемными разъемами на выходы Q1 накручиваются стандартные переходники **GARDENA Tap Connector G3/4"** (арт. 18201-20).
2.  **Ecowitt WH51** (3 шт.) + **Ecowitt Wi-Fi Gateway** (1 шт.):
    *   Беспроводные герметичные емкостные датчики влажности. Передают данные на частоте 433/915 МГц.

---

## Локальная интеграция (Без облака)

### 1. LinkTap Q1
Установите из HACS интеграцию **LinkTap Local** (`linktap_local_http_component`). Она автоматически находит шлюз и создает сущности клапанов (`valve.*`), расходомеров и датчиков протечки.

### 2. Ecowitt WH51
Используйте встроенную в HA интеграцию **Ecowitt**. В приложении WS View Plus на мобильном телефоне настройте шлюз Ecowitt в режиме Custom Server, указав URL Webhook от интеграции в Home Assistant.

---

## Шаблоны автоматизаций

### Полив по датчикам влажности почвы:
```yaml
alias: "Полив: Зона 1 (Авто)"
trigger:
  - platform: time
    at: "07:00:00"
condition:
  - condition: numeric_state
    entity_id: sensor.ecowitt_wh51_zone_1_moisture
    below: 45
action:
  - service: valve.open_valve
    target:
      entity_id: valve.linktap_q1_valve_1
    data:
      duration: 600
```
