# <Название фичи>

## Metadata

- id: NNN
- type: feature
- status: draft
- owner: <owner>
- date: YYYY-MM-DD

## Context

Зачем нужна фича, какую проблему пользователя/системы решает, что уже есть в коде.

## Requirements

- Функциональные требования (пронумерованный список, проверяемые формулировки).
- Нефункциональные требования (производительность, безопасность, ограничения Raspberry Pi 5 / Python 3.13).
- Out of scope.

## Architecture & Technical Design

Затрагиваемые модули, потоки данных, ключевые решения и альтернативы.

```mermaid
graph LR
  A[Источник] --> B[Обработка] --> C[Потребитель]
```

## Interfaces / Contracts

HTTP-эндпоинты, WebSocket-каналы, PGN/N2K-команды, BLE GATT-характеристики, форматы payload.

## Implementation Plan

1. Шаг — файл(ы) — ожидаемый результат.
2. ...

## Verification

- Тесты: `tests/...`
- Ручные проверки / команды.
- Критерии приёмки.

## Known Issues

Известные ограничения, ловушки, ссылки на `.agents/skills/nmea2000-setup/SKILL.md` и `patches/`.
